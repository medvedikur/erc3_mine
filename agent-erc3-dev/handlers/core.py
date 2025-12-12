import re
from typing import List, Any, Dict, Optional, Iterable
from erc3 import ApiException
from erc3.erc3 import client, dtos
from .base import ToolContext, Middleware
from .action_handlers import (
    WikiSearchHandler, WikiLoadHandler, CompositeActionHandler,
    ProjectSearchHandler, EmployeeSearchHandler
)
from .enrichers import ProjectSearchEnricher, WikiHintEnricher
from .intent import detect_intent
from utils import CLI_RED, CLI_GREEN, CLI_BLUE, CLI_YELLOW, CLI_CLR


# =============================================================================
# Utility Functions
# =============================================================================

def merge_non_none(payload: dict, model: Any, fields: List[str]) -> None:
    """
    Copy non-None fields from model to payload.

    Used in fetch-merge-dispatch pattern for partial updates where API
    requires all fields but we only want to update some.

    Args:
        payload: Target dict to update
        model: Source model with fields to copy
        fields: List of field names to check and copy
    """
    for field in fields:
        value = getattr(model, field, None)
        if value is not None:
            payload[field] = value


class DefaultActionHandler:
    """
    Standard handler that executes actions against the API.

    This is the fallback handler used by CompositeActionHandler when no
    specialized handler matches the action type.
    """
    def __init__(self):
        # Enrichers (with their own caches)
        self._project_search = ProjectSearchEnricher()
        self._wiki_hints = WikiHintEnricher()

    def can_handle(self, ctx: ToolContext) -> bool:
        """Default handler can handle any action."""
        return True

    def _log_api_call(self, ctx, action_name: str, request: Any, response: Any = None, error: str = None):
        """Log API call to failure logger if available."""
        failure_logger = ctx.shared.get('failure_logger')
        task_id = ctx.shared.get('task_id')
        if failure_logger and task_id:
            try:
                # Serialize request/response for logging
                req_dict = request.model_dump() if hasattr(request, 'model_dump') else str(request)
                resp_dict = None
                if response is not None:
                    resp_dict = response.model_dump() if hasattr(response, 'model_dump') else str(response)
                failure_logger.log_api_call(task_id, action_name, req_dict, resp_dict, error)
            except Exception:
                pass  # Don't break execution on logging errors

    def _extract_learning_from_error(self, error: Exception, request: Any) -> Optional[str]:
        """
        Extract actionable learning hints from API errors.
        This helps the agent adapt to API behavior dynamically without hardcoded rules.
        """
        error_str = str(error).lower()

        # Pattern 1: "Not found" errors for entities
        if "not found" in error_str:
            # Time logging with invalid customer
            if isinstance(request, client.Req_LogTimeEntry) and hasattr(request, 'customer') and request.customer:
                if not request.customer.startswith('cust_'):
                    return (
                        f"💡 LEARNING: Customer '{request.customer}' not found and doesn't match format 'cust_*'. "
                        f"Possible interpretations:\n"
                        f"  1. This might be a 'work_category' code → try: work_category='{request.customer}', customer=None\n"
                        f"  2. This might be an invalid/unknown code → return `none_clarification_needed` asking user what this code means\n"
                        f"Valid customer IDs follow format: cust_acme_systems, cust_baltic_ports, etc.\n"
                        f"Work categories are typically: dev, design, qa, ops, or custom project codes."
                    )

            # Generic not found - suggest checking ID format
            return (
                f"💡 LEARNING: Entity not found. Double-check ID format:\n"
                f"  - Customers: 'cust_*' (e.g., cust_acme_systems)\n"
                f"  - Projects: 'proj_*' (e.g., proj_cv_poc)\n"
                f"  - Employees: username (e.g., felix_baum) or 'emp_*'"
            )

        # Pattern 2: Validation errors reveal expected formats
        if "validation error" in error_str or "should be a valid list" in error_str:
            return (
                f"💡 LEARNING: Validation error - API expects specific format. "
                f"Common fixes:\n"
                f"  - If 'should be a valid list': wrap single values in brackets ['value']\n"
                f"  - If 'required field missing': check tool documentation for required parameters\n"
                f"  - If 'invalid type': verify parameter types (strings, numbers, booleans)"
            )

        return None

    def handle(self, ctx: ToolContext) -> None:
        """
        Default action handler for API dispatch with enrichments.

        Note: Wiki actions (Req_SearchWiki, Req_LoadWiki) are handled by
        specialized handlers (WikiSearchHandler, WikiLoadHandler) before
        this handler is called.
        """
        action_name = ctx.model.__class__.__name__
        print(f"  {CLI_BLUE}▶ Executing:{CLI_CLR} {action_name}")

        try:
            # SPECIAL HANDLING: Payload Cleaning for Req_UpdateEmployeeInfo
            # Ensure empty lists for skills/wills/notes/location/department are removed before dispatch
            # to prevent accidental wiping or event triggering.
            if isinstance(ctx.model, client.Req_UpdateEmployeeInfo):
                task_text = getattr(ctx.shared.get("task"), "task_text", "") or ""
                intent = detect_intent(task_text)
                salary_only = intent.is_salary_only

                # Ensure salary is always an integer (API requirement)
                # We trust the agent's calculation - don't override with bonus policy
                if ctx.model.salary is not None:
                    ctx.model.salary = int(round(ctx.model.salary))

                # For ALL employee updates, ensure unwanted fields are not sent
                # Set all non-essential fields to None - SafeReq_UpdateEmployeeInfo.model_dump() 
                # will exclude None values from the serialization
                current_user = ctx.shared.get('security_manager').current_user if ctx.shared.get('security_manager') else None
                if not getattr(ctx.model, "changed_by", None):
                    ctx.model.changed_by = current_user
                
                if salary_only:
                    # For salary-only updates, explicitly clear all other fields
                    for field in ['skills', 'wills', 'notes', 'location', 'department']:
                        setattr(ctx.model, field, None)
                else:
                    # For other updates, only clear empty fields  
                    for field in ['skills', 'wills', 'notes', 'location', 'department']:
                        val = getattr(ctx.model, field, None)
                        if val in ([], "", None):
                            setattr(ctx.model, field, None)

            # Default API execution
            try:
                # Check if specialized handler had an error
                if '_search_error' in ctx.shared:
                    raise ctx.shared.pop('_search_error')
                # Check if specialized handler already processed (ProjectSearchHandler, EmployeeSearchHandler)
                elif '_project_search_result' in ctx.shared:
                    result = ctx.shared.pop('_project_search_result')
                elif '_employee_search_result' in ctx.shared:
                    result = ctx.shared.pop('_employee_search_result')
                # SPECIAL HANDLING: Employee Update - API requires ALL fields to be sent
                # Otherwise missing fields are cleared! We must fetch current data first.
                elif isinstance(ctx.model, client.Req_UpdateEmployeeInfo):
                    employee_id = ctx.model.employee

                    # Step 1: Fetch current employee data to preserve existing values
                    try:
                        current_data = ctx.api.get_employee(employee_id)
                        emp = current_data.employee

                        # Step 2: Build complete payload - start with current data
                        payload = {
                            'employee': employee_id,
                            'notes': emp.notes if emp.notes else "",
                            'location': emp.location if emp.location else "",
                            'department': emp.department if emp.department else "",
                            'skills': emp.skills if emp.skills else [],
                            'wills': emp.wills if emp.wills else [],
                        }

                        # Step 3: Override with new values from the request
                        if ctx.model.salary is not None:
                            payload['salary'] = int(ctx.model.salary)
                        if ctx.model.changed_by:
                            payload['changed_by'] = ctx.model.changed_by
                        merge_non_none(payload, ctx.model, ['notes', 'location', 'department', 'skills', 'wills'])

                    except Exception as e:
                        print(f"  {CLI_YELLOW}⚠ Could not fetch current employee data: {e}. Using request data only.{CLI_CLR}")
                        # Fallback: use only what we have
                        payload = {'employee': employee_id}
                        if ctx.model.salary is not None:
                            payload['salary'] = int(ctx.model.salary)
                        if ctx.model.changed_by:
                            payload['changed_by'] = ctx.model.changed_by

                    # Create model with complete payload
                    update_model = client.Req_UpdateEmployeeInfo(**payload)
                    result = ctx.api.dispatch(update_model)
                
                # SPECIAL HANDLING: Project Team Update - API replaces entire team
                # If agent wants to add/remove members, we must merge with current team
                elif isinstance(ctx.model, client.Req_UpdateProjectTeam):
                    project_id = ctx.model.id
                    new_team = ctx.model.team or []
                    
                    # For project team updates, we typically want to REPLACE the team,
                    # not merge. The agent should provide the complete new team.
                    # However, log a warning if team is empty (might be accidental)
                    if not new_team:
                        print(f"  {CLI_YELLOW}⚠ Warning: Updating project team with empty team list!{CLI_CLR}")
                    
                    result = ctx.api.dispatch(ctx.model)
                
                # SPECIAL HANDLING: Time Entry Update - API may clear unset fields
                # Fetch current entry and merge with new values
                elif isinstance(ctx.model, client.Req_UpdateTimeEntry):
                    entry_id = ctx.model.id
                    
                    # Try to fetch current time entry to preserve existing values
                    try:
                        # Search for this specific entry
                        search_result = ctx.api.dispatch(client.Req_SearchTimeEntries(
                            employee=None,
                            limit=100  # Should find our entry
                        ))
                        
                        current_entry = None
                        if hasattr(search_result, 'entries') and search_result.entries:
                            for entry in search_result.entries:
                                if entry.id == entry_id:
                                    current_entry = entry
                                    break
                        
                        if current_entry:
                            # Save project/employee for auto-linking (time_entry is not a valid link kind!)
                            time_update_entities = []
                            if hasattr(current_entry, 'project') and current_entry.project:
                                time_update_entities.append({"id": current_entry.project, "kind": "project"})
                            if hasattr(current_entry, 'employee') and current_entry.employee:
                                time_update_entities.append({"id": current_entry.employee, "kind": "employee"})
                            ctx.shared['time_update_entities'] = time_update_entities

                            # Build payload starting with current data
                            payload = {
                                'id': entry_id,
                                'date': current_entry.date,
                                'hours': current_entry.hours,
                                'work_category': current_entry.work_category or "",
                                'notes': current_entry.notes or "",
                                'billable': current_entry.billable,
                                'status': current_entry.status or "",
                            }

                            # Override with new values
                            merge_non_none(payload, ctx.model, [
                                'date', 'hours', 'work_category', 'notes', 'billable', 'status', 'changed_by'
                            ])

                            update_model = client.Req_UpdateTimeEntry(**payload)
                            result = ctx.api.dispatch(update_model)
                        else:
                            # Entry not found, proceed with original request
                            result = ctx.api.dispatch(ctx.model)
                            
                    except Exception as e:
                        print(f"  {CLI_YELLOW}⚠ Could not fetch current time entry: {e}. Using request data only.{CLI_CLR}")
                        result = ctx.api.dispatch(ctx.model)
                    
                else:
                    # Standard Dispatch
                    # Check if the request has a limit field for retry logic
                    has_limit = hasattr(ctx.model, 'limit')
                    
                    try:
                       result = ctx.api.dispatch(ctx.model)
                    except ApiException as e:
                        # Check for page limit exceeded error
                        error_str = str(e).lower()
                        if "page limit exceeded" in error_str and has_limit:
                            # Parse max limit from error like "page limit exceeded: 5 > 3" or "1 > -1"
                            # Note: re is imported at module level
                            match = re.search(r'(\d+)\s*>\s*(-?\d+)', str(e))
                            if match:
                                max_limit = int(match.group(2))
                                if max_limit <= 0:
                                    # API says no pagination allowed at all - this is a system restriction
                                    print(f"  {CLI_YELLOW}⚠ API forbids pagination (max_limit={max_limit}). Cannot retrieve data.{CLI_CLR}")
                                    # Re-raise original exception to preserve proper error handling
                                    raise e
                                else:
                                    # Retry with allowed limit
                                    print(f"  {CLI_YELLOW}⚠ Page limit exceeded. Retrying with limit={max_limit}.{CLI_CLR}")
                                    ctx.model.limit = max_limit
                                    result = ctx.api.dispatch(ctx.model)
                            else:
                                # Can't parse, try with limit=1
                                print(f"  {CLI_YELLOW}⚠ Page limit exceeded. Retrying with limit=1.{CLI_CLR}")
                                ctx.model.limit = 1
                                result = ctx.api.dispatch(ctx.model)
                        else:
                            if "limit" in error_str:
                                 print(f"  {CLI_YELLOW}⚠ Potential limit error not caught: {error_str}{CLI_CLR}")
                            raise e
            except Exception as e:
                error_str = str(e)
                # Check for "Input should be a valid list" error (Server returning null)
                if "valid list" in error_str and "NoneType" in error_str:
                    print(f"  {CLI_YELLOW}⚠ API returned invalid list (null). Patching response.{CLI_CLR}")
                    
                    from erc3.erc3.dtos import (
                        Resp_SearchWiki, Resp_ProjectSearchResults, Resp_SearchEmployees, 
                        Resp_SearchTimeEntries, Resp_CustomerSearchResults
                    )
                    
                    if "Resp_SearchWiki" in error_str:
                        result = Resp_SearchWiki(results=[])
                    elif "Resp_ProjectSearchResults" in error_str:
                        result = Resp_ProjectSearchResults(projects=[])
                    elif "Resp_SearchEmployees" in error_str:
                        result = Resp_SearchEmployees(employees=[])
                    elif "Resp_SearchTimeEntries" in error_str:
                        # Patch with required aggregate fields
                        result = Resp_SearchTimeEntries(
                            time_entries=[], 
                            entries=[], # Alias check
                            total_hours=0.0, 
                            total_billable=0.0, 
                            total_non_billable=0.0
                        )
                    elif "Resp_CustomerSearchResults" in error_str:
                        result = Resp_CustomerSearchResults(customers=[])
                    else:
                        # Unknown list error, re-raise with learning hint
                        learning_hint = self._extract_learning_from_error(e, ctx.model)
                        if learning_hint:
                            ctx.results.append(f"\n{learning_hint}\n")
                        raise e
                else:
                    # Generic error - try to extract learning hint
                    learning_hint = self._extract_learning_from_error(e, ctx.model)
                    if learning_hint:
                        ctx.results.append(f"\n{learning_hint}\n")
                    raise e
            
            # Update Identity State if response is WhoAmI
            security_manager = ctx.shared.get('security_manager')
            if security_manager and isinstance(result, client.Resp_WhoAmI):
                identity_msg = security_manager.update_identity(result)
                # CRITICAL: Inject identity message into results so LLM sees it
                if identity_msg:
                    ctx.results.append(f"\n{identity_msg}\n")

            # Check for Wiki Hash updates in response
            # Many responses might contain the hash or trigger a need to check it?
            # Actually only who_am_i and list_wiki return the hash directly.

            wiki_manager = ctx.shared.get('wiki_manager')
            wiki_changed = False
            if wiki_manager:
                if isinstance(result, client.Resp_WhoAmI) and result.wiki_sha1:
                    wiki_changed = wiki_manager.sync(result.wiki_sha1)
                elif isinstance(result, client.Resp_ListWiki) and result.sha1:
                    wiki_changed = wiki_manager.sync(result.sha1)

            # 🔥 DYNAMIC WIKI INJECTION: When wiki changes, inject critical docs
            # This replaces hardcoded rules in prompts.py with actual wiki content
            if wiki_changed and wiki_manager:
                critical_docs = wiki_manager.get_critical_docs()
                if critical_docs:
                    print(f"  {CLI_YELLOW}📚 Wiki changed! Injecting critical docs into context...{CLI_CLR}")
                    ctx.results.append(
                        f"\n⚠️ WIKI UPDATED! You MUST read these policy documents before proceeding:\n\n"
                        f"{critical_docs}\n\n"
                        f"Action based on outdated rules will be REJECTED."
                    )

                # 🔥 TASK-RELEVANT FILE HINT on wiki change
                task = ctx.shared.get('task')
                task_text = getattr(task, 'task_text', '') if task else ''
                security_manager = ctx.shared.get('security_manager')
                is_public_user = getattr(security_manager, 'is_public', False) if security_manager else False

                hint = self._wiki_hints.get_task_file_hints(
                    wiki_manager, task_text, is_public_user,
                    skip_critical=True, context="wiki_change"
                )
                if hint:
                    ctx.results.append(hint)

            # 🔥 TASK-RELEVANT FILE HINT: When wiki_list is called, suggest files that match task keywords
            if isinstance(result, client.Resp_ListWiki) and wiki_manager and wiki_manager.pages:
                task = ctx.shared.get('task')
                task_text = getattr(task, 'task_text', '') if task else ''

                hint = self._wiki_hints.get_task_file_hints(
                    wiki_manager, task_text, is_public_user=False,
                    skip_critical=False, context="wiki_list"
                )
                if hint:
                    ctx.results.append(hint)

            # 🔥 PUBLIC USER MERGER POLICY: If user is public and merger.md exists, inject it
            # This ensures public chatbot always includes acquiring company name in responses
            if security_manager and isinstance(result, client.Resp_WhoAmI):
                if security_manager.is_public and wiki_manager and wiki_manager.has_page("merger.md"):
                    merger_content = wiki_manager.get_page("merger.md")
                    if merger_content:
                        print(f"  {CLI_YELLOW}🏢 Public user detected - Injecting merger policy...{CLI_CLR}")
                        ctx.results.append(
                            f"\n⚠️ CRITICAL POLICY - You are a PUBLIC chatbot and merger.md exists:\n\n"
                            f"=== merger.md ===\n{merger_content}\n\n"
                            f"YOU MUST include the acquiring company name (exactly as written in merger.md) "
                            f"in EVERY response you give, regardless of the question topic."
                        )

            # Apply Security Redaction (if applicable)
            if security_manager:
                result = security_manager.redact_result(result)

            # Convert result to JSON
            result_json = result.model_dump_json(exclude_none=True)
            
            print(f"  {CLI_GREEN}✓ SUCCESS{CLI_CLR}")
            self._log_api_call(ctx, action_name, ctx.model, result)
            
            # ENRICHMENT: Add your_role to project responses
            # This helps weaker models understand their permissions without extra API calls
            if isinstance(ctx.model, (client.Req_SearchProjects, client.Req_GetProject)):
                security_manager = ctx.shared.get('security_manager')
                current_user = getattr(security_manager, 'current_user', None) if security_manager else None
                if current_user:
                    role_hint = self._enrich_projects_with_user_role(result, current_user)
                    if role_hint:
                        ctx.results.append(role_hint)

            # DEBUG: Print full API response for search operations
            if "SearchProjects" in action_name:
                print(f"  {CLI_YELLOW}📋 PROJECTS API Response:{CLI_CLR}")
                print(f"     {result_json}")  # Full response for debugging
            elif "Search" in action_name or "List" in action_name:
                print(f"  {CLI_YELLOW}📋 API Response (truncated):{CLI_CLR}")
                print(f"     {result_json[:500]}...")
            
            ctx.results.append(f"Action ({action_name}): SUCCESS\nResult: {result_json}")
            self._maybe_hint_archived_logging(ctx, ctx.model, result)

            # PAGINATION WARNING: Alert agent if there are more pages
            next_offset = getattr(result, 'next_offset', None)
            if next_offset is not None and next_offset > 0:
                ctx.results.append(
                    f"\n⚠️ PAGINATION: next_offset={next_offset} means there are MORE results! "
                    f"Use offset={next_offset} in your next search to get the remaining items. "
                    f"Do NOT assume you found everything!"
                )

            # EMPTY CUSTOMERS SEARCH WARNING: Suggest broadening filters
            if isinstance(ctx.model, client.Req_SearchCustomers):
                customers = getattr(result, 'customers', None) or []
                if not customers:
                    # Count how many filters are active
                    active_filters = []
                    if getattr(ctx.model, 'locations', None):
                        active_filters.append(f"locations={ctx.model.locations}")
                    if getattr(ctx.model, 'deal_phase', None):
                        active_filters.append(f"deal_phase={ctx.model.deal_phase}")
                    if getattr(ctx.model, 'account_managers', None):
                        active_filters.append(f"account_managers={ctx.model.account_managers}")
                    if getattr(ctx.model, 'query', None):
                        active_filters.append(f"query={ctx.model.query}")

                    if len(active_filters) >= 2:
                        ctx.results.append(
                            f"\n⚠️ EMPTY RESULTS with {len(active_filters)} filters: {', '.join(active_filters)}. "
                            f"BROADEN YOUR SEARCH! Try:\n"
                            f"  1. Remove location filter (API may use different names like 'DK' vs 'Denmark' vs 'Danmark')\n"
                            f"  2. Search with fewer filters, then manually inspect results\n"
                            f"  3. Try `customers_search(account_managers=['your_id'])` to see ALL your customers, then filter yourself"
                        )

            # TIME ENTRY UPDATE HINT: If searching time entries and task suggests modification
            if isinstance(ctx.model, client.Req_SearchTimeEntries):
                entries = getattr(result, 'entries', None) or []
                if entries:
                    task = ctx.shared.get('task')
                    task_text = (getattr(task, 'task_text', '') if task else '').lower()

                    update_keywords = ['change', 'fix', 'update', 'correct', 'modify', 'edit', 'adjust']
                    is_update_task = any(kw in task_text for kw in update_keywords)

                    if is_update_task:
                        entry_ids = [getattr(e, 'id', 'unknown') for e in entries[:3]]
                        ctx.results.append(
                            f"\n💡 TIME UPDATE HINT: You found {len(entries)} time entries. "
                            f"To MODIFY an existing entry, use `time_update(id='...', hours=X, ...)`. "
                            f"Entry IDs: {', '.join(entry_ids)}{'...' if len(entries) > 3 else ''}. "
                            f"Do NOT use `time_log` to fix existing entries - that creates duplicates!"
                        )

            # Inject automatic disambiguation hints for project searches
            if isinstance(ctx.model, client.Req_SearchProjects):
                task = ctx.shared.get("task")
                task_text = getattr(task, "task_text", "") if task else ""
                for hint in self._project_search.enrich(ctx, result, task_text):
                    ctx.results.append(hint)

        except ApiException as e:
            error_msg = e.api_error.error if e.api_error else str(e)
            print(f"  {CLI_RED}✗ FAILED:{CLI_CLR} {error_msg}")

            ctx.results.append(f"Action ({action_name}): FAILED\nError: {error_msg}")
            self._log_api_call(ctx, action_name, ctx.model, error=error_msg)

            # Stop if critical? No, let the agent decide usually.
            # But if it's an internal error, maybe stop?

        except Exception as e:
            print(f"  {CLI_RED}✗ SYSTEM ERROR:{CLI_CLR} {e}")
            ctx.results.append(f"Action ({action_name}): SYSTEM ERROR\nError: {str(e)}")
            self._log_api_call(ctx, action_name, ctx.model, error=str(e))

    # --- Helper utilities -------------------------------------------------

    def _maybe_hint_archived_logging(self, ctx: ToolContext, request_model: Any, response: Any) -> None:
        task = ctx.shared.get("task")
        task_text = getattr(task, "task_text", "") if task else ""
        instructions = task_text.lower()
        # Check for time-related keywords: "log" AND ("time" OR "hour")
        # This catches both "log time" and "log 3 hours" phrasings
        if "log" not in instructions:
            return
        if "time" not in instructions and "hour" not in instructions:
            return

        projects: List[Any] = []
        if isinstance(request_model, client.Req_SearchProjects):
            projects = getattr(response, "projects", None) or []
        elif isinstance(request_model, client.Req_GetProject):
            project = getattr(response, "project", None)
            if project:
                projects = [project]
        else:
            return

        archived = [
            p for p in projects
            if getattr(p, "status", "").lower() == "archived"
        ]

        if not archived:
            return

        project_labels = ", ".join(self._format_project_label(p) for p in archived)
        hint = (
            f"AUTO-HINT: {project_labels} is archived, yet your instructions explicitly ask to log time. "
            "Use `/time/log` with the provided project ID to backfill the requested hours — archival usually "
            "means delivery wrapped up, not that historical time entries are disallowed."
        )
        ctx.results.append(hint)

    def _format_project_label(self, project: Any) -> str:
        """Format project for display in hints."""
        proj_id = getattr(project, "id", "unknown-id")
        proj_name = getattr(project, "name", proj_id)
        return f"'{proj_name}' ({proj_id})"

    def _enrich_projects_with_user_role(self, result: Any, current_user: str) -> Optional[str]:
        """
        Analyze project response and add YOUR_ROLE hint for current user.
        This helps weaker models understand their permissions without extra API calls.

        Returns a hint string to append to results, or None if no useful info.
        """
        projects = []

        # Handle both SearchProjects (list) and GetProject (single)
        if hasattr(result, 'projects') and result.projects:
            projects = result.projects
        elif hasattr(result, 'project') and result.project:
            projects = [result.project]

        if not projects:
            return None

        role_info = []
        lead_projects = []
        member_projects = []

        for proj in projects:
            proj_id = getattr(proj, 'id', None)
            proj_name = getattr(proj, 'name', proj_id)
            team = getattr(proj, 'team', None) or []

            user_role = None
            for member in team:
                employee = getattr(member, 'employee', getattr(member, 'employee_id', None))
                if employee == current_user:
                    user_role = getattr(member, 'role', 'Member')
                    break

            if user_role:
                if user_role == 'Lead':
                    lead_projects.append(f"'{proj_name}' ({proj_id})")
                else:
                    member_projects.append(f"'{proj_name}' ({proj_id}) as {user_role}")

        # Build concise hint
        hints = []
        if lead_projects:
            if len(lead_projects) == 1:
                hints.append(f"💡 YOUR_ROLE: You ({current_user}) are the LEAD of {lead_projects[0]}. You have full authorization to modify this project and log time for team members.")
            else:
                hints.append(f"💡 YOUR_ROLE: You ({current_user}) are the LEAD of {len(lead_projects)} projects: {', '.join(lead_projects)}.")

        if member_projects and len(member_projects) <= 3:
            hints.append(f"💡 YOUR_ROLE: You are a team member of: {', '.join(member_projects)}")
        elif member_projects:
            hints.append(f"💡 YOUR_ROLE: You are a team member of {len(member_projects)} projects.")

        if not hints:
            # User is not in any of these projects - also useful info!
            if len(projects) == 1:
                hints.append(f"💡 YOUR_ROLE: You ({current_user}) are NOT a member of this project. Check if you have Account Manager or Direct Manager authorization.")

        return "\n".join(hints) if hints else None

    def _fetch_employee_salary(self, ctx: ToolContext, employee_id: Optional[str]) -> Optional[float]:
        if not employee_id:
            return None
        try:
            resp = ctx.api.get_employee(employee_id)
            employee = getattr(resp, "employee", None)
            if employee and hasattr(employee, "salary"):
                return float(employee.salary)
        except Exception as e:
            print(f"  {CLI_YELLOW}⚠️ Salary helper failed to fetch current salary for {employee_id}: {e}{CLI_CLR}")
        return None

    def _lookup_bonus_policy(self, ctx: ToolContext, instructions: str) -> Optional[Dict[str, Any]]:
        text = (instructions or "").lower()
        keywords = ["ny bonus", "new year bonus", "holiday bonus", "eoy bonus", "bonus tradition"]
        mentions_bonus = any(k in text for k in keywords)

        wiki_manager = ctx.shared.get('wiki_manager')
        if mentions_bonus and wiki_manager and wiki_manager.pages:
            search_terms = ["bonus", "NY bonus", "New Year bonus", "EoY bonus"]
            snippets = self._search_wiki_for_bonus(wiki_manager, search_terms)
            parsed = self._parse_bonus_snippet_list(snippets)
            if parsed:
                return parsed

        value = self._parse_bonus_from_instructions(text)
        if value:
            return {
                "type": value["type"],
                "amount": value["amount"],
                "message": f"AUTO-HINT: Interpreting '+{value['raw']}' from instructions as {value['type']} bonus."
            }
        return None

    def _parse_bonus_from_instructions(self, text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        percentage = re.search(r"\+\s*(\d+)\s*%", text)
        if percentage:
            return {"type": "percent", "amount": float(percentage.group(1)), "raw": percentage.group(1) + "%"}
        flat = re.search(r"\+\s*\$?(\d+)(?!\s*%)", text)
        if flat:
            return {"type": "flat", "amount": float(flat.group(1)), "raw": flat.group(1)}
        return None

    def _search_wiki_for_bonus(self, wiki_manager: Any, terms: Iterable[str]) -> List[str]:
        snippets = []
        for term in terms:
            try:
                response = wiki_manager.search(term, top_k=3)
                snippets.append(response)
            except Exception as e:
                print(f"  {CLI_YELLOW}⚠️ Wiki bonus search failed for '{term}': {e}{CLI_CLR}")
        return snippets

    def _parse_bonus_snippet(self, snippet: str) -> Optional[Dict[str, Any]]:
        if not snippet:
            return None
        percent = re.search(r"(\d+)\s*%", snippet)
        if percent:
            return {"type": "percent", "amount": float(percent.group(1)), "message": f"AUTO-HINT: Wiki snippet suggests +{percent.group(1)}%: {snippet.strip()[:120]}..."}
        flat_currency = re.search(r"(\d+)\s*(?:EUR|euro|bucks|usd)", snippet, re.I)
        if flat_currency:
            return {"type": "flat", "amount": float(flat_currency.group(1)), "message": f"AUTO-HINT: Wiki snippet suggests +{flat_currency.group(1)} currency: {snippet.strip()[:120]}..."}
        flat_plain = re.search(r"\b\+?(\d+)\b", snippet)
        if flat_plain:
            return {"type": "flat", "amount": float(flat_plain.group(1)), "message": f"AUTO-HINT: Wiki snippet suggests +{flat_plain.group(1)} units: {snippet.strip()[:120]}..."}
        return None

    def _parse_bonus_snippet_list(self, snippets: List[str]) -> Optional[Dict[str, Any]]:
        for snippet in snippets:
            parsed = self._parse_bonus_snippet(snippet)
            if parsed:
                return parsed
        return None

    def _apply_bonus_policy(self, current_salary: float, policy: Dict[str, Any]) -> Optional[float]:
        amount = policy.get("amount")
        if amount is None:
            return None
        if policy.get("type") == "flat":
            return current_salary + amount
        if policy.get("type") == "percent":
            return current_salary * (1 + amount / 100.0)
        return None



class ActionExecutor:
    """Main executor that orchestrates middleware and handlers"""
    def __init__(self, api, middleware: List[Middleware] = None, task: Any = None):
        self.api = api
        self.middleware = middleware or []
        # Use CompositeActionHandler with specialized handlers first, then default
        default_handler = DefaultActionHandler()
        self.handler = CompositeActionHandler(
            handlers=[
                WikiSearchHandler(),
                WikiLoadHandler(),
                ProjectSearchHandler(),
                EmployeeSearchHandler(),
            ],
            default_handler=default_handler
        )
        self.task = task

    def execute(self, action_dict: dict, action_model: Any, initial_shared: dict = None) -> ToolContext:
        ctx = ToolContext(self.api, action_dict, action_model)
        if self.task:
            ctx.shared['task'] = self.task

        # Merge initial shared state from caller (agent.py)
        if initial_shared:
            for key, value in initial_shared.items():
                if key not in ctx.shared:  # Don't override task if already set
                    ctx.shared[key] = value

        # Run middleware
        for mw in self.middleware:
            mw.process(ctx)
            if ctx.stop_execution:
                return ctx

        # Run handler
        self.handler.handle(ctx)
        return ctx
