"""
Project tool parsers.
"""
from typing import Any
from erc3.erc3 import client
from ..registry import ToolParser, ParseContext, ParseError
from ..normalizers import normalize_team_roles


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
