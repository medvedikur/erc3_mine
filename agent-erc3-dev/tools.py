from typing import Any, Callable, Dict, List, Optional
import re
import datetime
from erc3.erc3 import client
from pydantic import BaseModel, Field


# =============================================================================
# Tool Parser Registry
# =============================================================================

class ParseContext:
    """Context passed to tool parsers with pre-processed data."""
    __slots__ = ('args', 'raw_args', 'context', 'current_user')

    def __init__(self, args: dict, raw_args: dict, context: Any, current_user: Optional[str]):
        self.args = args
        self.raw_args = raw_args
        self.context = context
        self.current_user = current_user


class ToolParser:
    """
    Registry of tool parsers with automatic dispatch.

    Usage:
        @ToolParser.register("whoami", "me", "identity")
        def _parse_who_am_i(ctx: ParseContext) -> Any:
            return client.Req_WhoAmI()
    """
    _parsers: Dict[str, Callable[[ParseContext], Any]] = {}

    @classmethod
    def register(cls, *names: str):
        """Decorator to register a parser function for one or more tool names."""
        def decorator(func: Callable[[ParseContext], Any]) -> Callable[[ParseContext], Any]:
            for name in names:
                # Normalize name same way as in parse()
                normalized = name.lower().replace("_", "").replace("-", "").replace("/", "")
                cls._parsers[normalized] = func
            return func
        return decorator

    @classmethod
    def get_parser(cls, tool_name: str) -> Optional[Callable[[ParseContext], Any]]:
        """Get parser for a tool name (normalized)."""
        normalized = tool_name.lower().replace("_", "").replace("-", "").replace("/", "")
        return cls._parsers.get(normalized)

    @classmethod
    def list_tools(cls) -> List[str]:
        """List all registered tool names."""
        return sorted(cls._parsers.keys())


# =============================================================================
# Models
# =============================================================================

# Define simplified tool models where needed, or map directly to erc3.client

class Req_Respond(BaseModel):
    message: str
    outcome: str = Field(..., description="One of: ok_answer, ok_not_found, denied_security, none_clarification_needed, none_unsupported, error_internal")
    links: List[dict] = []


class ParseError:
    """
    Represents a parsing error with a message to return to the LLM.
    Used instead of None to provide meaningful feedback.
    """
    def __init__(self, message: str, tool: str = None):
        self.message = message
        self.tool = tool
    
    def __str__(self):
        if self.tool:
            return f"Tool '{self.tool}': {self.message}"
        return self.message

# --- RUNTIME PATCH FOR LIBRARY BUG ---
# The erc3 library enforces non-optional lists for skills/wills in Req_UpdateEmployeeInfo,
# causing empty lists to be sent (and triggering events) even when we only want to update salary.
# We patch the model definition at runtime to make them Optional.
def _patch_update_employee_model(model_class, class_name):
    """Patch a Req_UpdateEmployeeInfo model to make skills/wills/notes/location/department Optional."""
    from typing import Optional, List
    try:
        if hasattr(model_class, 'model_fields'):
            # Pydantic v2
            fields_to_patch = ['skills', 'wills', 'notes', 'location', 'department']
            for field in fields_to_patch:
                if field in model_class.model_fields:
                    model_class.model_fields[field].default = None
                    # For list types, make them Optional
                    if field in ['skills', 'wills']:
                        from erc3.erc3 import dtos
                        model_class.model_fields[field].annotation = Optional[List[dtos.SkillLevel]]
                    else:
                        model_class.model_fields[field].annotation = Optional[str]
            # Rebuild model
            if hasattr(model_class, 'model_rebuild'):
                model_class.model_rebuild()
        else:
            # Pydantic v1
            fields_to_patch = ['skills', 'wills', 'notes', 'location', 'department']
            for field in fields_to_patch:
                if field in model_class.__fields__:
                    model_class.__fields__[field].required = False
                    model_class.__fields__[field].default = None
        print(f"🔧 Patched {class_name} to support optional fields.")
        return True
    except Exception as e:
        print(f"⚠️ Failed to patch {class_name}: {e}")
        return False

try:
    from erc3.erc3 import dtos
    _patch_update_employee_model(dtos.Req_UpdateEmployeeInfo, "dtos.Req_UpdateEmployeeInfo")
except Exception as e:
    print(f"⚠️ Failed to patch dtos.Req_UpdateEmployeeInfo: {e}")

try:
    # Also patch client.Req_UpdateEmployeeInfo since SafeReq inherits from it
    _patch_update_employee_model(client.Req_UpdateEmployeeInfo, "client.Req_UpdateEmployeeInfo")
except Exception as e:
    print(f"⚠️ Failed to patch client.Req_UpdateEmployeeInfo: {e}")

# --- SAFE MODEL WRAPPERS ---
class SafeReq_UpdateEmployeeInfo(client.Req_UpdateEmployeeInfo):
    """
    Wrapper to ensure we don't send null values for optional fields,
    which would overwrite existing data with nulls/defaults in the backend.
    """
    def model_dump(self, **kwargs):
        # Always exclude None to prevent overwriting with nulls
        kwargs['exclude_none'] = True
        data = super().model_dump(**kwargs)
        # Also remove empty lists for skills/wills/notes to prevent clearing them/triggering events
        keys_to_remove = ['skills', 'wills', 'notes', 'location', 'department']
        for k in keys_to_remove:
            if k in data and (data[k] == [] or data[k] == "" or data[k] is None):
                del data[k]
        return data
    
    def dict(self, **kwargs):
        # Fallback for Pydantic v1 or older usage
        kwargs['exclude_none'] = True
        data = super().dict(**kwargs)
        keys_to_remove = ['skills', 'wills', 'notes', 'location', 'department']
        for k in keys_to_remove:
            if k in data and (data[k] == [] or data[k] == "" or data[k] is None):
                del data[k]
        return data

    def model_dump_json(self, **kwargs):
        # Ensure JSON serialization also excludes None and empty lists
        # We can't easily modify the JSON string output of super().model_dump_json
        # So we dump to dict first, then json.dumps?
        # Or rely on the fact that dispatch might use model_dump/dict?
        # If dispatch uses model_dump_json, we are in trouble if we can't hook it.
        # But we can use our model_dump logic!
        import json
        data = self.model_dump(**kwargs)
        return json.dumps(data)
# ---------------------------

def _normalize_args(args: dict) -> dict:
    """Normalize argument keys to handle common LLM hallucinations"""
    normalized = args.copy()
    
    # Common mappings (hallucination -> correct key)
    mappings = {
        # Wiki
        "query_semantic": "query_regex",
        "query": "query_regex",
        "page_filter": "page",
        "page_includes": "page",
        
        # Employees/Time
        "employee_id": "employee",
        "id": "employee", # Context dependent, but handled in specific blocks
        "user_id": "employee",
        "username": "employee",
        
        # Projects
        "project_id": "id",
        "project": "id", # For get_project
        "name": "query", # Common hallucination for search

        # Time Log
        "project_id": "project", # For time_log
    }

    for bad_key, good_key in mappings.items():
        if bad_key in normalized and good_key not in normalized:
            normalized[good_key] = normalized[bad_key]
            
    return normalized

def _inject_context(args: dict, context: Any) -> dict:
    """Inject current user ID into args if missing"""
    if not context or not hasattr(context, 'shared'):
        return args
        
    security_manager = context.shared.get('security_manager')
    if not security_manager or not security_manager.current_user:
        return args
        
    current_user = security_manager.current_user
    
    # Fields that always require the current user acting as the modifier
    user_fields = ["logged_by", "changed_by"]
    
    for field in user_fields:
        if field not in args or not args[field]:
            args[field] = current_user
                
    return args

def _detect_placeholders(args: dict) -> Optional[str]:
    """Detect placeholder values in arguments that indicate the model is trying to use values it doesn't have yet."""
    placeholder_patterns = [
        "<<<", ">>>",           # <<<FILL_FROM_SEARCH>>>
        "FILL_",                # FILL_FROM_SEARCH, etc.
        "{RESULT", "{VALUE",    # Template-style
    ]

    # Skip free-text fields - they may contain natural language with words like "placeholder", "TODO"
    free_text_fields = {"message", "content", "text", "notes", "description", "reason"}

    for key, value in args.items():
        if isinstance(value, str) and key.lower() not in free_text_fields:
            value_upper = value.upper()
            for pattern in placeholder_patterns:
                if pattern in value_upper:
                    return f"Argument '{key}' contains placeholder value '{value}'. You cannot use placeholders! Wait for the previous tool results before calling dependent tools. Execute tools one at a time when values depend on previous results."
    return None


# =============================================================================
# Tool Parsers (registered via @ToolParser.register decorator)
# =============================================================================

# --- Identity ---

@ToolParser.register("whoami", "who_am_i", "me", "identity")
def _parse_who_am_i(ctx: ParseContext) -> Any:
    """Get current user identity and context."""
    return client.Req_WhoAmI()


# --- Employees ---

@ToolParser.register("employees_list", "employeeslist", "listemployees")
def _parse_employees_list(ctx: ParseContext) -> Any:
    """List all employees with pagination."""
    return client.Req_ListEmployees(
        offset=int(ctx.args.get("offset", 0)),
        limit=int(ctx.args.get("limit", 5))
    )


@ToolParser.register("employees_search", "employeessearch", "searchemployees")
def _parse_employees_search(ctx: ParseContext) -> Any:
    """Search employees by query, location, department, or manager."""
    return client.Req_SearchEmployees(
        query=ctx.args.get("query") or ctx.args.get("name") or ctx.args.get("query_regex"),
        location=ctx.args.get("location"),
        department=ctx.args.get("department"),
        manager=ctx.args.get("manager"),
        offset=int(ctx.args.get("offset", 0)),
        limit=int(ctx.args.get("limit", 5))
    )


@ToolParser.register("employees_get", "employeesget", "getemployee")
def _parse_employees_get(ctx: ParseContext) -> Any:
    """Get employee by ID. Falls back to search if only name provided."""
    emp_id = ctx.args.get("id") or ctx.args.get("employee_id") or ctx.args.get("employee")
    username = ctx.args.get("username")

    # Smart dispatch: if ID missing but username/name provided, use search
    if not emp_id and (username or ctx.args.get("name")):
        query = username or ctx.args.get("name")
        return client.Req_SearchEmployees(
            query=query,
            offset=int(ctx.args.get("offset", 0)),
            limit=int(ctx.args.get("limit", 5))
        )

    if not emp_id:
        if ctx.current_user:
            emp_id = ctx.current_user
        else:
            return None  # Will trigger LLM retry

    return client.Req_GetEmployee(id=emp_id)


@ToolParser.register("employees_update", "employeesupdate", "updateemployee", "salary_update", "salaryupdate", "updatesalary")
def _parse_employees_update(ctx: ParseContext) -> Any:
    """Update employee info (salary, notes, location, etc.)."""
    update_args = {
        "employee": ctx.args.get("employee") or ctx.args.get("id") or ctx.args.get("employee_id") or ctx.current_user,
        "notes": ctx.args.get("notes"),
        "salary": ctx.args.get("salary"),
        "location": ctx.args.get("location"),
        "department": ctx.args.get("department"),
        "skills": ctx.args.get("skills"),
        "wills": ctx.args.get("wills"),
        "changed_by": ctx.args.get("changed_by")
    }
    valid_args = {k: v for k, v in update_args.items() if v is not None}
    return SafeReq_UpdateEmployeeInfo(**valid_args)


# --- Wiki ---

@ToolParser.register("wiki_list", "wikilist", "listwiki")
def _parse_wiki_list(ctx: ParseContext) -> Any:
    """List all wiki pages."""
    return client.Req_ListWiki()


@ToolParser.register("wiki_load", "wikiload", "loadwiki", "readwiki")
def _parse_wiki_load(ctx: ParseContext) -> Any:
    """Load a specific wiki page by path."""
    file_arg = ctx.args.get("file") or ctx.args.get("path") or ctx.args.get("page")
    if not file_arg:
        return None  # Will trigger LLM retry
    return client.Req_LoadWiki(file=file_arg)


@ToolParser.register("wiki_search", "wikisearch", "searchwiki")
def _parse_wiki_search(ctx: ParseContext) -> Any:
    """Search wiki pages by regex query."""
    query = (ctx.args.get("query_regex") or ctx.args.get("query") or
             ctx.args.get("query_semantic") or ctx.args.get("search_term"))
    return client.Req_SearchWiki(query_regex=query)


@ToolParser.register("wiki_update", "wikiupdate", "updatewiki")
def _parse_wiki_update(ctx: ParseContext) -> Any:
    """Update or create a wiki page."""
    return client.Req_UpdateWiki(
        file=ctx.args.get("file") or ctx.args.get("path"),
        content=ctx.args.get("content"),
        changed_by=ctx.args.get("changed_by")
    )


# --- Customers ---

@ToolParser.register("customers_list", "customerslist", "listcustomers")
def _parse_customers_list(ctx: ParseContext) -> Any:
    """List all customers with pagination."""
    return client.Req_ListCustomers(
        offset=int(ctx.args.get("offset", 0)),
        limit=int(ctx.args.get("limit", 5))
    )


@ToolParser.register("customers_get", "customersget", "getcustomer")
def _parse_customers_get(ctx: ParseContext) -> Any:
    """Get customer by ID."""
    cust_id = ctx.args.get("id") or ctx.args.get("customer_id")
    if not cust_id:
        return None
    return client.Req_GetCustomer(id=cust_id)


@ToolParser.register("customers_search", "customerssearch", "searchcustomers")
def _parse_customers_search(ctx: ParseContext) -> Any:
    """Search customers by location, deal phase, or account manager."""
    # Handle location -> locations (list)
    locations = ctx.args.get("locations")
    if not locations and ctx.args.get("location"):
        locations = [ctx.args.get("location")]
    elif isinstance(locations, str):
        locations = [locations]

    # Handle status/stage -> deal_phase (list)
    deal_phase = ctx.args.get("deal_phase") or ctx.args.get("status") or ctx.args.get("stage")
    if deal_phase:
        if isinstance(deal_phase, str):
            deal_phase = [deal_phase]
    else:
        deal_phase = None

    # Handle account_manager -> account_managers (list)
    account_managers = ctx.args.get("account_managers")
    if not account_managers and ctx.args.get("account_manager"):
        account_managers = [ctx.args.get("account_manager")]
    elif isinstance(account_managers, str):
        account_managers = [account_managers]

    search_args = {
        "query": ctx.args.get("query") or ctx.args.get("query_regex"),
        "deal_phase": deal_phase,
        "locations": locations,
        "account_managers": account_managers,
        "offset": int(ctx.args.get("offset", 0)),
        "limit": int(ctx.args.get("limit", 5))
    }
    valid_args = {k: v for k, v in search_args.items() if v is not None}
    return client.Req_SearchCustomers(**valid_args)


# --- Projects ---

@ToolParser.register("projects_list", "projectslist", "listprojects")
def _parse_projects_list(ctx: ParseContext) -> Any:
    """List all projects with pagination."""
    return client.Req_ListProjects(
        offset=int(ctx.args.get("offset", 0)),
        limit=int(ctx.args.get("limit", 5))
    )


@ToolParser.register("projects_get", "projectsget", "getproject")
def _parse_projects_get(ctx: ParseContext) -> Any:
    """Get project by ID."""
    proj_id = ctx.args.get("id") or ctx.args.get("project_id")
    if not proj_id:
        return None
    return client.Req_GetProject(id=proj_id)


@ToolParser.register("projects_search", "projectssearch", "searchprojects")
def _parse_projects_search(ctx: ParseContext) -> Any:
    """Search projects by query, status, customer, or team member."""
    status_arg = ctx.args.get("status")
    if isinstance(status_arg, str):
        status = [status_arg]
    elif isinstance(status_arg, list):
        status = status_arg if status_arg else None
    else:
        status = None

    # Handle team filter (member parameter)
    team_filter = None
    member_id = ctx.args.get("member") or ctx.args.get("team_member") or ctx.args.get("employee_id")
    if member_id:
        from erc3.erc3 import dtos
        team_filter = dtos.ProjectTeamFilter(
            employee_id=member_id,
            role=ctx.args.get("role"),
            min_time_slice=float(ctx.args.get("min_time_slice", 0.0))
        )

    # Smart include_archived logic
    include_archived_arg = ctx.args.get("include_archived")
    query_val = ctx.args.get("query") or ctx.args.get("query_regex") or ""
    query_lower = query_val.lower() if query_val else ""

    archive_keywords = ["archived", "archive", "completed", "wrapped up", "finished", "closed", "ended"]
    query_suggests_archived = any(kw in query_lower for kw in archive_keywords)

    if status and "archived" in status:
        include_archived = True
    elif query_suggests_archived:
        include_archived = True
    elif include_archived_arg is not None:
        include_archived = bool(include_archived_arg)
    else:
        include_archived = True

    search_args = {
        "query": ctx.args.get("query") or ctx.args.get("query_regex"),
        "customer_id": ctx.args.get("customer_id") or ctx.args.get("customer"),
        "status": status,
        "team": team_filter,
        "include_archived": include_archived,
        "offset": int(ctx.args.get("offset", 0)),
        "limit": int(ctx.args.get("limit", 5))
    }
    valid_args = {k: v for k, v in search_args.items() if v is not None}
    return client.Req_SearchProjects(**valid_args)


def _normalize_team_roles(team_data: list) -> list:
    """Normalize team role names to valid TeamRole enum values."""
    role_mappings = {
        "tester": "QA", "testing": "QA", "quality": "QA",
        "quality control": "QA", "qc": "QA", "qa": "QA",
        "developer": "Engineer", "dev": "Engineer",
        "devops": "Ops", "operations": "Ops",
        "ui": "Designer", "ux": "Designer",
        "lead": "Lead", "manager": "Lead", "pm": "Lead", "project manager": "Lead",
        "engineer": "Engineer", "designer": "Designer", "ops": "Ops", "other": "Other",
    }
    valid_roles = ["Lead", "Engineer", "Designer", "QA", "Ops", "Other"]

    normalized = []
    for member in team_data:
        if isinstance(member, dict):
            role = member.get("role", "Other")
            normalized_role = role_mappings.get(role.lower(), role) if role else "Other"
            if normalized_role not in valid_roles:
                normalized_role = "Other"
            normalized.append({
                "employee": member.get("employee"),
                "time_slice": member.get("time_slice", 0.0),
                "role": normalized_role
            })
    return normalized


@ToolParser.register("projects_team_update", "projectsteamupdate", "updateprojectteam", "projectsupdateteam", "teamupdate")
def _parse_projects_team_update(ctx: ParseContext) -> Any:
    """Update project team members."""
    team_data = ctx.args.get("team") or []
    normalized_team = _normalize_team_roles(team_data)
    return client.Req_UpdateProjectTeam(
        id=ctx.args.get("id") or ctx.args.get("project_id"),
        team=normalized_team,
        changed_by=ctx.args.get("changed_by")
    )


@ToolParser.register("projects_status_update", "projectsstatusupdate", "updateprojectstatus", "projectssetstatus")
def _parse_projects_status_update(ctx: ParseContext) -> Any:
    """Update project status."""
    status = ctx.args.get("status")
    if not status:
        return ParseError(
            "projects_status_update requires 'status' field. Valid values: 'idea', 'exploring', 'active', 'paused', 'archived'",
            tool="projects_status_update"
        )
    return client.Req_UpdateProjectStatus(
        id=ctx.args.get("id") or ctx.args.get("project_id"),
        status=status,
        changed_by=ctx.args.get("changed_by")
    )


@ToolParser.register("projects_update", "projectsupdate", "updateproject")
def _parse_projects_update(ctx: ParseContext) -> Any:
    """Generic project update - dispatches to team or status update based on args."""
    team_data = ctx.args.get("team")
    team_add = ctx.args.get("team_add")

    # If team_add provided, fetch current team and merge
    if team_add and not team_data:
        project_id = ctx.args.get("id") or ctx.args.get("project_id")
        if ctx.context and hasattr(ctx.context, 'api'):
            try:
                resp = ctx.context.api.get_project(project_id)
                current_team = []
                if hasattr(resp, 'project') and resp.project and hasattr(resp.project, 'team'):
                    for member in resp.project.team:
                        current_team.append({
                            "employee": getattr(member, 'employee', None),
                            "role": getattr(member, 'role', 'Other'),
                            "time_slice": getattr(member, 'time_slice', 0.0)
                        })
                current_team.append(team_add)
                team_data = current_team
            except Exception:
                team_data = [team_add]
        else:
            team_data = [team_add]

    if team_data:
        normalized_team = _normalize_team_roles(team_data)
        return client.Req_UpdateProjectTeam(
            id=ctx.args.get("id") or ctx.args.get("project_id"),
            team=normalized_team,
            changed_by=ctx.args.get("changed_by")
        )
    elif ctx.args.get("status"):
        return client.Req_UpdateProjectStatus(
            id=ctx.args.get("id") or ctx.args.get("project_id"),
            status=ctx.args.get("status"),
            changed_by=ctx.args.get("changed_by")
        )
    else:
        return ParseError(
            f"The requested update operation (args: {list(ctx.args.keys())}) is not supported. Only 'team' and 'status' can be updated.",
            tool="projects_update"
        )


# --- Time ---

@ToolParser.register("time_log", "timelog", "logtime")
def _parse_time_log(ctx: ParseContext) -> Any:
    """Log time entry for an employee on a project."""
    target_emp = ctx.args.get("employee") or ctx.args.get("employee_id")
    if not target_emp:
        target_emp = ctx.current_user

    # Determine date: Explicit > Simulated > Today
    date_val = ctx.args.get("date")
    if not date_val and ctx.context and hasattr(ctx.context, 'shared'):
        sm = ctx.context.shared.get('security_manager')
        if sm and hasattr(sm, 'today') and sm.today:
            date_val = sm.today
    if not date_val:
        date_val = datetime.date.today().isoformat()

    return client.Req_LogTimeEntry(
        employee=target_emp,
        project=ctx.args.get("project") or ctx.args.get("project_id"),
        customer=ctx.args.get("customer"),
        date=date_val,
        hours=float(ctx.args.get("hours", 0)),
        work_category=ctx.args.get("work_category", "dev"),
        notes=ctx.args.get("notes", ""),
        billable=bool(ctx.args.get("billable", True)),
        status=ctx.args.get("status", "draft"),
        logged_by=ctx.args.get("logged_by")
    )


@ToolParser.register("time_get", "timeget", "gettime")
def _parse_time_get(ctx: ParseContext) -> Any:
    """Get time entry by ID."""
    entry_id = ctx.args.get("id")
    if not entry_id:
        return None
    return client.Req_GetTimeEntry(id=entry_id)


@ToolParser.register("time_search", "timesearch", "searchtime")
def _parse_time_search(ctx: ParseContext) -> Any:
    """Search time entries with filters."""
    return client.Req_SearchTimeEntries(
        employee=ctx.args.get("employee") or ctx.args.get("employee_id") or ctx.current_user,
        project=ctx.args.get("project") or ctx.args.get("project_id"),
        date_from=ctx.args.get("date_from"),
        date_to=ctx.args.get("date_to"),
        billable=ctx.args.get("billable", ""),
        offset=int(ctx.args.get("offset", 0)),
        limit=int(ctx.args.get("limit", 5))
    )


@ToolParser.register("time_update", "timeupdate", "updatetime")
def _parse_time_update(ctx: ParseContext) -> Any:
    """Update existing time entry.

    Uses model_construct() to bypass validation since all fields are required
    in SDK but agent only provides fields to update. core.py does fetch-merge.
    """
    hours_raw = ctx.args.get("hours")
    hours = float(hours_raw) if hours_raw is not None else None

    return client.Req_UpdateTimeEntry.model_construct(
        id=ctx.args.get("id"),
        date=ctx.args.get("date"),
        hours=hours,
        work_category=ctx.args.get("work_category"),
        notes=ctx.args.get("notes"),
        billable=ctx.args.get("billable"),
        status=ctx.args.get("status"),
        changed_by=ctx.args.get("changed_by")
    )


@ToolParser.register("time_summary_employee", "timesummaryemployee", "timesummarybyemployee", "employeetimesummary")
def _parse_time_summary_by_employee(ctx: ParseContext) -> Any:
    """Get time summary aggregated by employee."""
    # Handle single values -> lists
    employees = ctx.args.get("employees") or ctx.args.get("employee")
    if employees and isinstance(employees, str):
        employees = [employees]

    projects = ctx.args.get("projects") or ctx.args.get("project")
    if projects and isinstance(projects, str):
        projects = [projects]

    customers = ctx.args.get("customers") or ctx.args.get("customer")
    if customers and isinstance(customers, str):
        customers = [customers]

    return client.Req_TimeSummaryByEmployee(
        date_from=ctx.args.get("date_from"),
        date_to=ctx.args.get("date_to"),
        employees=employees or [],
        projects=projects or [],
        customers=customers or [],
        billable=ctx.args.get("billable", "")
    )


@ToolParser.register("time_summary_project", "timesummaryproject", "timesummarybyproject", "projecttimesummary")
def _parse_time_summary_by_project(ctx: ParseContext) -> Any:
    """Get time summary aggregated by project."""
    # Handle single values -> lists
    employees = ctx.args.get("employees") or ctx.args.get("employee")
    if employees and isinstance(employees, str):
        employees = [employees]

    projects = ctx.args.get("projects") or ctx.args.get("project")
    if projects and isinstance(projects, str):
        projects = [projects]

    customers = ctx.args.get("customers") or ctx.args.get("customer")
    if customers and isinstance(customers, str):
        customers = [customers]

    return client.Req_TimeSummaryByProject(
        date_from=ctx.args.get("date_from"),
        date_to=ctx.args.get("date_to"),
        employees=employees or [],
        projects=projects or [],
        customers=customers or [],
        billable=ctx.args.get("billable", "")
    )


# --- Response ---

@ToolParser.register("respond", "answer", "reply")
def _parse_respond(ctx: ParseContext) -> Any:
    """Submit final response to user. Handles link auto-detection and validation."""
    args = ctx.args

    # Extract query_specificity - agent must declare if query was specific or ambiguous
    # Values: "specific" (clear query with IDs/names), "ambiguous" (vague terms like "cool", "that")
    query_specificity = (args.get("query_specificity") or args.get("querySpecificity") or
                         args.get("specificity") or "unspecified")
    if isinstance(query_specificity, str):
        query_specificity = query_specificity.lower().strip()
    # Store in shared context for middleware to check
    if ctx.context and hasattr(ctx.context, 'shared'):
        ctx.context.shared['query_specificity'] = query_specificity

    # Extract message (handle various field names from different LLMs)
    message = (args.get("message") or args.get("Message") or
               args.get("text") or args.get("Text") or
               args.get("response") or args.get("Response") or
               args.get("answer") or args.get("Answer") or
               args.get("content") or args.get("Content") or
               args.get("details") or args.get("Details") or
               args.get("body") or args.get("Body"))
    if not message:
        message = "No message provided."

    # Extract/infer outcome
    outcome = args.get("outcome") or args.get("Outcome")
    if not outcome:
        msg_lower = str(message).lower()
        if "cannot" in msg_lower or "unable to" in msg_lower or "could not" in msg_lower:
            if "tool" in msg_lower or "system" in msg_lower:
                outcome = "none_unsupported"
            elif any(w in msg_lower for w in ["permission", "access", "allow", "restricted"]):
                outcome = "denied_security"
            else:
                outcome = "none_clarification_needed"
        else:
            outcome = "ok_answer"

    # Extract and normalize links
    links = args.get("links") or args.get("Links") or []
    if links:
        links = [{"kind": l.get("kind") or l.get("Kind", ""),
                  "id": l.get("id") or l.get("ID", "")} for l in links]

    # Auto-detect links from message text
    if not links:
        # Find prefixed IDs (proj_, emp_, cust_)
        ids = re.findall(r'\b((?:proj|emp|cust)_[a-z0-9_]+)\b', str(message))
        type_map = {"proj": "project", "emp": "employee", "cust": "customer"}
        for found_id in ids:
            prefix = found_id.split('_')[0]
            if prefix in type_map:
                links.append({"id": found_id, "kind": type_map[prefix]})

        # Find bare employee usernames (name_surname pattern)
        non_employee_patterns = [
            "cv_engineering", "edge_ai", "machine_learning", "deep_learning",
            "data_engineering", "cloud_architecture", "backend_development",
            "frontend_development", "mobile_development", "devops_engineering",
            "security_engineering", "project_management", "technical_writing",
            "time_slice", "work_category", "deal_phase", "account_manager",
            "employee_id", "project_id", "customer_id", "next_offset",
        ]
        potential_users = re.findall(r'\b([a-z]+(?:_[a-z]+)+)\b', str(message))
        for pu in potential_users:
            if not pu.startswith(('proj_', 'emp_', 'cust_')):
                if pu not in non_employee_patterns:
                    links.append({"id": pu, "kind": "employee"})
            if pu.startswith('emp_'):
                links.append({"id": pu[4:], "kind": "employee"})

    # Validate employee links via API
    if links and ctx.context:
        validated_links = []
        for link in links:
            if link.get("kind") == "employee":
                try:
                    req = client.Req_GetEmployee(id=link.get("id"))
                    ctx.context.api.dispatch(req)
                    validated_links.append(link)
                except Exception as e:
                    if "not found" not in str(e).lower() and "404" not in str(e):
                        validated_links.append(link)
            else:
                validated_links.append(link)
        links = validated_links

    # Add mutation entities to links
    if ctx.context:
        had_mutations = ctx.context.shared.get('had_mutations', False)
        mutation_entities = ctx.context.shared.get('mutation_entities', [])
        if had_mutations:
            for entity in mutation_entities:
                if not any(l.get("id") == entity.get("id") and l.get("kind") == entity.get("kind") for l in links):
                    links.append(entity)
            if ctx.current_user:
                if not any(l.get("id") == ctx.current_user and l.get("kind") == "employee" for l in links):
                    links.append({"id": ctx.current_user, "kind": "employee"})

        # Add search entities to links (for read-only operations)
        # Only add if there were NO mutations (otherwise mutation_entities already has all needed)
        # This captures entities from search filters (e.g., employee in time_search)
        if not had_mutations:
            search_entities = ctx.context.shared.get('search_entities', [])
            for entity in search_entities:
                if not any(l.get("id") == entity.get("id") and l.get("kind") == entity.get("kind") for l in links):
                    links.append(entity)

    # Deduplicate links
    seen = set()
    unique_links = []
    for link in links:
        key = (link.get("id"), link.get("kind"))
        if key not in seen:
            seen.add(key)
            unique_links.append(link)
    links = unique_links

    # Clear links for error/denied outcomes - security best practice
    if outcome in ("error_internal", "denied_security"):
        links = []

    return client.Req_ProvideAgentResponse(
        message=str(message),
        outcome=outcome,
        links=links
    )


# =============================================================================
# Main Parse Function
# =============================================================================

def parse_action(action_dict: dict, context: Any = None) -> Optional[Any]:
    """
    Parse action dict into Pydantic model for Erc3Client.

    Uses ToolParser registry for dispatch. Each tool parser is registered
    via @ToolParser.register decorator above.
    """
    tool = action_dict.get("tool", "").lower().replace("_", "").replace("-", "").replace("/", "")

    # Flatten args - merge args into action_dict to handle both nested and flat structures
    raw_args = action_dict.get("args", {})
    if raw_args:
        combined_args = {**action_dict, **raw_args}
    else:
        combined_args = action_dict

    # Use combined_args for lookups
    args = combined_args.copy()

    # SAFETY: Detect placeholder values before processing
    placeholder_error = _detect_placeholders(args)
    if placeholder_error:
        return ParseError(placeholder_error, tool=tool)

    # Normalize args
    args = _normalize_args(args)

    # Inject Context (Auto-fill user ID for auditing fields)
    if context:
        args = _inject_context(args, context)

    # Get current user for defaults
    current_user = None
    if context and hasattr(context, 'shared'):
        sm = context.shared.get('security_manager')
        if sm:
            current_user = sm.current_user

    # Create parse context
    ctx = ParseContext(
        args=args,
        raw_args=raw_args,
        context=context,
        current_user=current_user
    )

    # Dispatch to registered parser
    parser = ToolParser.get_parser(tool)
    if parser:
        return parser(ctx)

    # Unknown tool - return helpful error
    registered_tools = ", ".join(sorted(set(
        name for name in ToolParser._parsers.keys()
        if "_" not in name  # Show only canonical names
    )))
    return ParseError(
        f"Unknown tool '{tool}'. Available: {registered_tools}. Check spelling.",
        tool=tool
    )
