"""
Main tool parsing logic.

Provides the parse_action() function and individual tool parsers.
"""
import datetime
from typing import Any, Optional

from erc3.erc3 import client

from .registry import ToolParser, ParseContext, ParseError
from .patches import SafeReq_UpdateEmployeeInfo
from .normalizers import normalize_args, inject_context, detect_placeholders, normalize_team_roles
from .links import LinkExtractor


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
            return None

    return client.Req_GetEmployee(id=emp_id)


@ToolParser.register("employees_update", "employeesupdate", "updateemployee",
                     "salary_update", "salaryupdate", "updatesalary")
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
        return None
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

    # Handle team filter
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


@ToolParser.register("projects_team_update", "projectsteamupdate", "updateprojectteam",
                     "projectsupdateteam", "teamupdate")
def _parse_projects_team_update(ctx: ParseContext) -> Any:
    """Update project team members."""
    team_data = ctx.args.get("team") or []
    normalized_team = normalize_team_roles(team_data)
    return client.Req_UpdateProjectTeam(
        id=ctx.args.get("id") or ctx.args.get("project_id"),
        team=normalized_team,
        changed_by=ctx.args.get("changed_by")
    )


@ToolParser.register("projects_status_update", "projectsstatusupdate",
                     "updateprojectstatus", "projectssetstatus")
def _parse_projects_status_update(ctx: ParseContext) -> Any:
    """Update project status."""
    status = ctx.args.get("status")
    if not status:
        return ParseError(
            "projects_status_update requires 'status' field. "
            "Valid values: 'idea', 'exploring', 'active', 'paused', 'archived'",
            tool="projects_status_update"
        )
    return client.Req_UpdateProjectStatus(
        id=ctx.args.get("id") or ctx.args.get("project_id"),
        status=status,
        changed_by=ctx.args.get("changed_by")
    )


@ToolParser.register("projects_update", "projectsupdate", "updateproject")
def _parse_projects_update(ctx: ParseContext) -> Any:
    """Generic project update - dispatches to team or status update."""
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
        normalized_team = normalize_team_roles(team_data)
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
            f"The requested update operation (args: {list(ctx.args.keys())}) is not supported. "
            "Only 'team' and 'status' can be updated.",
            tool="projects_update"
        )


# --- Time ---

@ToolParser.register("time_log", "timelog", "logtime")
def _parse_time_log(ctx: ParseContext) -> Any:
    """Log time entry for an employee on a project."""
    target_emp = ctx.args.get("employee") or ctx.args.get("employee_id")
    if not target_emp:
        target_emp = ctx.current_user

    # Determine date
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
    employee_arg = ctx.args.get("employee") or ctx.args.get("employee_id")
    if employee_arg and str(employee_arg).lower() == "me":
        employee_arg = ctx.current_user
    employee_val = employee_arg or ctx.current_user

    return client.Req_SearchTimeEntries(
        employee=employee_val,
        project=ctx.args.get("project") or ctx.args.get("project_id"),
        date_from=ctx.args.get("date_from"),
        date_to=ctx.args.get("date_to"),
        billable=ctx.args.get("billable", ""),
        offset=int(ctx.args.get("offset", 0)),
        limit=int(ctx.args.get("limit", 5))
    )


@ToolParser.register("time_update", "timeupdate", "updatetime")
def _parse_time_update(ctx: ParseContext) -> Any:
    """Update existing time entry."""
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


@ToolParser.register("time_summary_employee", "timesummaryemployee",
                     "timesummarybyemployee", "employeetimesummary")
def _parse_time_summary_by_employee(ctx: ParseContext) -> Any:
    """Get time summary aggregated by employee."""
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


@ToolParser.register("time_summary_project", "timesummaryproject",
                     "timesummarybyproject", "projecttimesummary")
def _parse_time_summary_by_project(ctx: ParseContext) -> Any:
    """Get time summary aggregated by project."""
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
    """Submit final response to user."""
    args = ctx.args
    link_extractor = LinkExtractor()

    # Extract query_specificity
    query_specificity = (args.get("query_specificity") or args.get("querySpecificity") or
                         args.get("specificity") or "unspecified")
    if isinstance(query_specificity, str):
        query_specificity = query_specificity.lower().strip()
    if ctx.context and hasattr(ctx.context, 'shared'):
        ctx.context.shared['query_specificity'] = query_specificity

    # Extract message
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
    raw_links = args.get("links") or args.get("Links") or []
    links = link_extractor.normalize_links(raw_links)

    # Auto-detect links from message
    if not links:
        links = link_extractor.extract_from_message(str(message))

    # Validate employee links
    if links and ctx.context:
        links = link_extractor.validate_employee_links(links, ctx.context.api)

    # Add mutation/search entities
    if ctx.context:
        had_mutations = ctx.context.shared.get('had_mutations', False)
        mutation_entities = ctx.context.shared.get('mutation_entities', [])
        search_entities = ctx.context.shared.get('search_entities', [])

        if had_mutations:
            links = link_extractor.add_mutation_entities(links, mutation_entities, ctx.current_user)
        else:
            links = link_extractor.add_search_entities(links, search_entities)

    # Deduplicate
    links = link_extractor.deduplicate(links)

    # Clear links for error/denied outcomes
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

    Uses ToolParser registry for dispatch.

    Args:
        action_dict: Dict with 'tool' and 'args' keys
        context: Optional context with security_manager

    Returns:
        Parsed request model or ParseError
    """
    tool = action_dict.get("tool", "").lower().replace("_", "").replace("-", "").replace("/", "")

    # Flatten args
    raw_args = action_dict.get("args", {})
    if raw_args:
        combined_args = {**action_dict, **raw_args}
    else:
        combined_args = action_dict

    args = combined_args.copy()

    # Detect placeholders
    placeholder_error = detect_placeholders(args)
    if placeholder_error:
        return ParseError(placeholder_error, tool=tool)

    # Normalize args
    args = normalize_args(args)

    # Inject context
    if context:
        args = inject_context(args, context)

    # Get current user
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

    # Dispatch to parser
    parser = ToolParser.get_parser(tool)
    if parser:
        return parser(ctx)

    # Unknown tool
    registered_tools = ", ".join(sorted(set(
        name for name in ToolParser._parsers.keys()
        if "_" not in name
    )))
    return ParseError(
        f"Unknown tool '{tool}'. Available: {registered_tools}. Check spelling.",
        tool=tool
    )
