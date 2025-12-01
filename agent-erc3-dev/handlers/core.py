from typing import List, Any, Dict, Tuple
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
                # Convert to dict, clean it, and re-instantiate or patch the object if possible.
                # Or better: If the library dispatch uses model_dump_json, we can just trust our SafeReq wrapper?
                # If the SafeReq wrapper isn't working (maybe library uses .dict()), we intercept here.
                # But we can't easily modify the internal state of the pydantic model to remove fields effectively 
                # if they are set to default values.
                # However, if we are using the generated client, `ctx.api.dispatch` takes the model.
                # Let's try to force specific fields to None if they are empty lists/strings.
                
                # Aggressive cleaning
                for field in ['skills', 'wills']:
                    val = getattr(ctx.model, field, None)
                    if val == []:
                        setattr(ctx.model, field, None)
                
                for field in ['notes', 'location', 'department']:
                    val = getattr(ctx.model, field, None)
                    if val == "":
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


class ActionExecutor:
    """Main executor that orchestrates middleware and handlers"""
    def __init__(self, api, middleware: List[Middleware] = None):
        self.api = api
        self.middleware = middleware or []
        self.handler = DefaultActionHandler()
    
    def execute(self, action_dict: dict, action_model: Any) -> ToolContext:
        ctx = ToolContext(self.api, action_dict, action_model)
        
        # Run middleware
        for mw in self.middleware:
            mw.process(ctx)
            if ctx.stop_execution:
                return ctx
                
        # Run handler
        self.handler.handle(ctx)
        return ctx
