"""
Time tracking tool parsers.
"""
import datetime
from typing import Any
from erc3.erc3 import client
from ..registry import ToolParser, ParseContext


def _get_default_date_range(ctx: ParseContext) -> tuple:
    """
    Get default date range for time summary queries.

    Uses a wide range (full year) when dates not specified,
    because the agent typically wants "all time" when asking
    about total hours on a project.
    """
    # Try to get today from security manager
    today = None
    if ctx.context and hasattr(ctx.context, 'shared'):
        sm = ctx.context.shared.get('security_manager')
        if sm and hasattr(sm, 'today') and sm.today:
            today = sm.today

    if not today:
        today = datetime.date.today().isoformat()

    # Parse today to get year
    try:
        today_dt = datetime.date.fromisoformat(today)
        year = today_dt.year
    except (ValueError, TypeError):
        year = 2025

    # Default: from Jan 1 of current year to today
    date_from = f"{year}-01-01"
    date_to = today

    return date_from, date_to


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
    """Get time entry by ID, or fallback to search if search params provided."""
    entry_id = ctx.args.get("id")
    if entry_id:
        return client.Req_GetTimeEntry(id=entry_id)

    # FALLBACK: If agent uses time_get with search params, convert to time_search
    employee = ctx.args.get("employee") or ctx.args.get("employee_id")
    date_from = ctx.args.get("date_from") or ctx.args.get("from_date") or ctx.args.get("from")
    date_to = ctx.args.get("date_to") or ctx.args.get("to_date") or ctx.args.get("to")
    date_single = ctx.args.get("date")
    project = ctx.args.get("project") or ctx.args.get("project_id")

    if employee or date_from or date_to or date_single or project:
        # Agent used time_get as time_search - convert
        if date_single and not date_from:
            date_from = date_single
            date_to = date_single
        return client.Req_SearchTimeEntries(
            employee=employee or ctx.current_user,
            project=project,
            date_from=date_from,
            date_to=date_to,
            billable="",
            offset=0,
            limit=10
        )

    return None


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

    # Get date range - these are required fields
    date_from = ctx.args.get("date_from")
    date_to = ctx.args.get("date_to")

    # If dates not provided, use sensible defaults
    if not date_from or not date_to:
        default_from, default_to = _get_default_date_range(ctx)
        date_from = date_from or default_from
        date_to = date_to or default_to

    return client.Req_TimeSummaryByEmployee(
        date_from=date_from,
        date_to=date_to,
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

    # Get date range - these are required fields
    date_from = ctx.args.get("date_from")
    date_to = ctx.args.get("date_to")

    # If dates not provided, use sensible defaults
    if not date_from or not date_to:
        default_from, default_to = _get_default_date_range(ctx)
        date_from = date_from or default_from
        date_to = date_to or default_to

    return client.Req_TimeSummaryByProject(
        date_from=date_from,
        date_to=date_to,
        employees=employees or [],
        projects=projects or [],
        customers=customers or [],
        billable=ctx.args.get("billable", "")
    )
