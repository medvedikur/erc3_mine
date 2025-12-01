import re
from typing import List, Any, Dict, Tuple, Optional, Iterable
from erc3 import ApiException
from erc3.erc3 import client, dtos
from .base import ToolContext, Middleware, ActionHandler

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_BLUE = "\x1B[34m"
CLI_YELLOW = "\x1B[33m"
CLI_CLR = "\x1B[0m"

class DefaultActionHandler:
    """Standard handler that executes the action against the API"""
    def __init__(self):
        # Cache employee -> list[ProjectBrief] to avoid repeated paging
        self._project_cache: Dict[str, List[Any]] = {}
        self._project_detail_cache: Dict[str, Any] = {}
        self._hint_cache: set[Tuple[str, str]] = set()

    def handle(self, ctx: ToolContext) -> None:
        action_name = ctx.model.__class__.__name__
        print(f"  {CLI_BLUE}▶ Executing:{CLI_CLR} {action_name}")
        
        # Link Auto-Detection for Respond Action
        if isinstance(ctx.model, client.Req_ProvideAgentResponse) and not ctx.model.links:
            # If no links provided, try to find relevant entities from context history
            # This is a fallback if regex in tools.py missed them or they weren't in the text
            # We can scan the previous results in ctx (though ctx is fresh per action)
            # OR we can scan the shared context if we stored history there.
            # Currently we don't store full history in shared.
            pass

        try:
            # SPECIAL HANDLING: Wiki Search (Local vs Remote)
            if isinstance(ctx.model, client.Req_SearchWiki):
                wiki_manager = ctx.shared.get('wiki_manager')
                if wiki_manager:
                    print(f"  {CLI_BLUE}🔍 Using Local Wiki Search (Smart RAG){CLI_CLR}")
                    search_result_text = wiki_manager.search(ctx.model.query_regex)
                    
                    print(f"  {CLI_GREEN}✓ SUCCESS (Local){CLI_CLR}")
                    ctx.results.append(f"Action ({action_name}): SUCCESS\nResult: {search_result_text}")
                    return

            # SPECIAL HANDLING: Payload Cleaning for Req_UpdateEmployeeInfo
            # Ensure empty lists for skills/wills/notes/location/department are removed before dispatch
            # to prevent accidental wiping or event triggering.
            if isinstance(ctx.model, client.Req_UpdateEmployeeInfo):
                task_text = getattr(ctx.shared.get("task"), "task_text", "") or ""
                intent_lower = task_text.lower()
                salary_only = ("salary" in intent_lower or "compensation" in intent_lower) and (
                    "raise" in intent_lower or "increase" in intent_lower
                ) and all(keyword not in intent_lower for keyword in ["skill", "note", "location", "department"])

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
                # SPECIAL HANDLING: Smart Broadening for Project Search
                # If searching by team member AND query, do a dual search (Exact + Broad)
                # This enables finding projects where the query matches the ID/Description but not the Name (which API filters on).
                if isinstance(ctx.model, client.Req_SearchProjects) and ctx.model.team and ctx.model.query:
                    print(f"  {CLI_BLUE}🔍 Smart Search: Executing dual-pass project search (Query + Broad){CLI_CLR}")
                    
                    # 1. Exact Match (Original Request)
                    try:
                        res_exact = ctx.api.dispatch(ctx.model)
                    except Exception as e:
                        print(f"  {CLI_YELLOW}⚠ Exact search failed: {e}{CLI_CLR}")
                        res_exact = None

                    # 2. Broad Match (Remove Query)
                    import copy
                    model_broad = copy.deepcopy(ctx.model)
                    model_broad.query = None # Remove text filter
                    
                    try:
                        res_broad = ctx.api.dispatch(model_broad)
                    except Exception as e:
                        print(f"  {CLI_YELLOW}⚠ Broad search failed: {e}{CLI_CLR}")
                        res_broad = None
                    
                    # 3. Merge Results
                    projects_map = {}
                    
                    # Add exact matches first
                    if res_exact and hasattr(res_exact, 'projects') and res_exact.projects:
                        for p in res_exact.projects:
                            projects_map[p.id] = p
                    
                    # Add broad matches (deduplicating by ID)
                    if res_broad and hasattr(res_broad, 'projects') and res_broad.projects:
                        for p in res_broad.projects:
                            if p.id not in projects_map:
                                projects_map[p.id] = p
                    
                    # 4. Construct Final Response
                    # We use the client module to instantiate the response model
                    # Assuming client contains the DTOs (it usually does in this codebase structure)
                    if hasattr(client, 'Resp_ProjectSearchResults'):
                        ResponseClass = client.Resp_ProjectSearchResults
                    else:
                        # Fallback to dtos if not in client namespace
                        from erc3.erc3 import dtos
                        ResponseClass = dtos.Resp_ProjectSearchResults

                    result = ResponseClass(
                        projects=list(projects_map.values()),
                        next_offset=res_broad.next_offset if res_broad else 0 
                    )
                    print(f"  {CLI_BLUE}🔍 Merged {len(projects_map)} unique projects.{CLI_CLR}")

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
                        # Override other fields only if explicitly set (not None/empty)
                        if getattr(ctx.model, 'notes', None) is not None:
                            payload['notes'] = ctx.model.notes
                        if getattr(ctx.model, 'location', None) is not None:
                            payload['location'] = ctx.model.location
                        if getattr(ctx.model, 'department', None) is not None:
                            payload['department'] = ctx.model.department
                        if getattr(ctx.model, 'skills', None) is not None:
                            payload['skills'] = ctx.model.skills
                        if getattr(ctx.model, 'wills', None) is not None:
                            payload['wills'] = ctx.model.wills
                            
                    except Exception as e:
                        print(f"  {CLI_YELLOW}⚠ Could not fetch current employee data: {e}. Using request data only.{CLI_CLR}")
                        # Fallback: use only what we have
                        payload = {
                            'employee': employee_id,
                        }
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
                            if ctx.model.date is not None:
                                payload['date'] = ctx.model.date
                            if ctx.model.hours is not None:
                                payload['hours'] = ctx.model.hours
                            if ctx.model.work_category is not None:
                                payload['work_category'] = ctx.model.work_category
                            if ctx.model.notes is not None:
                                payload['notes'] = ctx.model.notes
                            if ctx.model.billable is not None:
                                payload['billable'] = ctx.model.billable
                            if ctx.model.status is not None:
                                payload['status'] = ctx.model.status
                            if ctx.model.changed_by:
                                payload['changed_by'] = ctx.model.changed_by
                            
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
                            import re
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
                        # Unknown list error, re-raise
                        raise e
                else:
                    raise e
            
            # Update Identity State if response is WhoAmI
            security_manager = ctx.shared.get('security_manager')
            if security_manager and isinstance(result, client.Resp_WhoAmI):
                security_manager.update_identity(result)

            # Check for Wiki Hash updates in response
            # Many responses might contain the hash or trigger a need to check it?
            # Actually only who_am_i and list_wiki return the hash directly.
            
            wiki_manager = ctx.shared.get('wiki_manager')
            if wiki_manager:
                if isinstance(result, client.Resp_WhoAmI) and result.wiki_sha1:
                    wiki_manager.sync(result.wiki_sha1)
                elif isinstance(result, client.Resp_ListWiki) and result.sha1:
                    wiki_manager.sync(result.sha1)

            # Apply Security Redaction (if applicable)
            if security_manager:
                result = security_manager.redact_result(result)

            # Convert result to JSON
            result_json = result.model_dump_json(exclude_none=True)
            
            print(f"  {CLI_GREEN}✓ SUCCESS{CLI_CLR}")
            
            # DEBUG: Print full API response for search operations
            if "SearchProjects" in action_name:
                print(f"  {CLI_YELLOW}📋 PROJECTS API Response:{CLI_CLR}")
                print(f"     {result_json}")  # Full response for debugging
            elif "Search" in action_name or "List" in action_name:
                print(f"  {CLI_YELLOW}📋 API Response (truncated):{CLI_CLR}")
                print(f"     {result_json[:500]}...")
            
            ctx.results.append(f"Action ({action_name}): SUCCESS\nResult: {result_json}")
            self._maybe_hint_archived_logging(ctx, ctx.model, result)

            # Inject automatic disambiguation hints for project searches
            if isinstance(ctx.model, client.Req_SearchProjects):
                self._analyze_project_overlap(ctx, result)
            
        except ApiException as e:
            error_msg = e.api_error.error if e.api_error else str(e)
            print(f"  {CLI_RED}✗ FAILED:{CLI_CLR} {error_msg}")
            
            ctx.results.append(f"Action ({action_name}): FAILED\nError: {error_msg}")
            
            # Stop if critical? No, let the agent decide usually.
            # But if it's an internal error, maybe stop?
            
        except Exception as e:
            print(f"  {CLI_RED}✗ SYSTEM ERROR:{CLI_CLR} {e}")
            ctx.results.append(f"Action ({action_name}): SYSTEM ERROR\nError: {str(e)}")

    # --- Helper utilities -------------------------------------------------
    def _analyze_project_overlap(self, ctx: ToolContext, search_result: Any) -> None:
        """
        Implements STEP 0 enforcement from prompts.py: whenever the agent searches
        for someone else's projects, automatically surface overlaps with the current
        user's portfolio so the LLM doesn't stop early with clarification requests.
        """
        team_filter = getattr(ctx.model, "team", None)
        if not team_filter or not getattr(team_filter, "employee_id", None):
            return

        security_manager = ctx.shared.get("security_manager")
        current_user = getattr(security_manager, "current_user", None) if security_manager else None
        if not current_user:
            return

        target_employee = team_filter.employee_id
        if not target_employee or target_employee == current_user:
            return

        target_projects = getattr(search_result, "projects", None) or []
        if not target_projects:
            return

        # Avoid spamming the same hint multiple times per target/turn
        hint_key = (target_employee, ctx.model.__class__.__name__)
        if hint_key in self._hint_cache:
            return

        # Gather the current user's projects to detect overlaps
        own_projects = self._fetch_projects_for_member(ctx, current_user)
        if not own_projects:
            return

        target_project_map = {getattr(p, "id", None): p for p in target_projects if getattr(p, "id", None)}
        overlap = [p for p in own_projects if getattr(p, "id", None) in target_project_map]

        if not overlap:
            return

        self._hint_cache.add(hint_key)

        if len(overlap) == 1:
            project_id = getattr(overlap[0], "id", None)
            project_detail = self._get_project_detail(ctx, project_id) if project_id else None
            hint = self._build_unique_overlap_hint(project_detail or overlap[0], project_id, current_user, target_employee)
        else:
            overlap_labels = [self._format_project_label(p) for p in overlap]
            hint = (
                f"AUTO-HINT: Projects shared by you ({current_user}) and {target_employee}: "
                f"{', '.join(overlap_labels)}. Inspect each with `/projects/get` and proceed with the one "
                "where you are Lead/account manager instead of asking for clarification."
            )

        ctx.results.append(hint)

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

    def _build_unique_overlap_hint(self, project: Any, project_id: str, current_user: str, target_employee: str) -> str:
        proj_name = getattr(project, "name", project_id or "unknown project")
        lead_role = None
        target_role = None
        team = getattr(project, "team", None) or []
        for member in team:
            employee = getattr(member, "employee", getattr(member, "employee_id", None))
            if not employee:
                continue
            if employee == current_user:
                lead_role = getattr(member, "role", None)
            if employee == target_employee:
                target_role = getattr(member, "role", None)

        role_parts = []
        if lead_role:
            role_parts.append(f"you are assigned as {lead_role}")
        if target_role:
            role_parts.append(f"{target_employee} is {target_role}")

        role_text = f" ({'; '.join(role_parts)})" if role_parts else ""

        return (
            f"AUTO-HINT: Unique overlap detected — project '{proj_name}' ({project_id}){role_text}. "
            "Follow STEP 0 guidance and log the requested hours here."
        )

    def _format_project_label(self, project: Any) -> str:
        proj_id = getattr(project, "id", "unknown-id")
        proj_name = getattr(project, "name", proj_id)
        return f"'{proj_name}' ({proj_id})"

    def _fetch_projects_for_member(self, ctx: ToolContext, employee_id: str) -> List[Any]:
        if not employee_id:
            return []

        if employee_id in self._project_cache:
            return self._project_cache[employee_id]

        projects: List[Any] = []
        limit = 5  # server hard-limit
        max_pages = 4  # up to 20 projects; enough for disambiguation

        for page in range(max_pages):
            offset = page * limit
            try:
                req = client.Req_SearchProjects(
                    limit=limit,
                    offset=offset,
                    include_archived=True,
                    team=dtos.ProjectTeamFilter(employee_id=employee_id)
                )
                resp = ctx.api.dispatch(req)
            except Exception as e:
                print(f"  {CLI_YELLOW}⚠️ Overlap helper: failed to fetch projects for {employee_id} (page {page}): {e}{CLI_CLR}")
                break

            page_projects = getattr(resp, "projects", None) or []
            if page_projects:
                projects.extend(page_projects)
            if len(page_projects) < limit:
                break

        self._project_cache[employee_id] = projects
        return projects

    def _get_project_detail(self, ctx: ToolContext, project_id: str) -> Any:
        if not project_id:
            return None

        if project_id in self._project_detail_cache:
            return self._project_detail_cache[project_id]

        try:
            resp_project = ctx.api.get_project(project_id)
        except TypeError:
            try:
                resp_project = ctx.api.get_project(project_id=project_id)
            except TypeError:
                resp_project = ctx.api.get_project(id=project_id)
        except Exception as e:
            print(f"  {CLI_YELLOW}⚠️ Overlap helper: failed to fetch project detail for {project_id}: {e}{CLI_CLR}")
            return None

        project = getattr(resp_project, "project", None) or resp_project
        self._project_detail_cache[project_id] = project
        return project

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

    def _search_wiki_for_bonus(self, wiki_manager: Any, terms: Iterable[str]) -> List[str]:
        snippets = []
        for term in terms:
            try:
                response = wiki_manager.search(term, top_k=3)
                if response:
                    snippets.append(response)
            except Exception as e:
                print(f"  {CLI_YELLOW}⚠️ Wiki bonus search failed for '{term}': {e}{CLI_CLR}")
        return snippets

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
        self.handler = DefaultActionHandler()
        self.task = task
    
    def execute(self, action_dict: dict, action_model: Any) -> ToolContext:
        ctx = ToolContext(self.api, action_dict, action_model)
        if self.task:
            ctx.shared['task'] = self.task
        
        # Run middleware
        for mw in self.middleware:
            mw.process(ctx)
            if ctx.stop_execution:
                return ctx
                
        # Run handler
        self.handler.handle(ctx)
        return ctx
