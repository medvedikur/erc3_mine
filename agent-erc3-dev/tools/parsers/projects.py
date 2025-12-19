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
    # AICODE-NOTE: Support both page (1-indexed) and offset (0-indexed) pagination (t009 fix)
    # AICODE-NOTE: offset takes precedence over page when both are provided (t009 regression)
    limit = int(ctx.args.get("limit", ctx.args.get("per_page", 5)))
    explicit_offset = ctx.args.get("offset")
    page = ctx.args.get("page")
    if explicit_offset is not None:
        offset = int(explicit_offset)
    elif page is not None:
        offset = (int(page) - 1) * limit
    else:
        offset = 0

    return client.Req_ListProjects(
        offset=offset,
        limit=limit
    )


@ToolParser.register("projects_get", "projectsget", "getproject")
def _parse_projects_get(ctx: ParseContext) -> Any:
    """Get project by ID."""
    proj_id = ctx.args.get("id") or ctx.args.get("project_id") or ctx.args.get("project")
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

    # AICODE-NOTE: Support both page (1-indexed) and offset (0-indexed) pagination (t009 fix)
    # AICODE-NOTE: offset takes precedence over page when both are provided (t009 regression)
    limit = int(ctx.args.get("limit", ctx.args.get("per_page", 5)))
    explicit_offset = ctx.args.get("offset")
    page = ctx.args.get("page")
    if explicit_offset is not None:
        offset = int(explicit_offset)
    elif page is not None:
        offset = (int(page) - 1) * limit
    else:
        offset = 0

    search_args = {
        "query": ctx.args.get("query") or ctx.args.get("query_regex"),
        "customer_id": ctx.args.get("customer_id") or ctx.args.get("customer"),
        "status": status,
        "team": team_filter,
        "include_archived": include_archived,
        "offset": offset,
        "limit": limit
    }
    valid_args = {k: v for k, v in search_args.items() if v is not None}
    return client.Req_SearchProjects(**valid_args)


@ToolParser.register("projects_team_update", "projectsteamupdate", "updateprojectteam",
                     "projectsupdateteam", "teamupdate")
def _parse_projects_team_update(ctx: ParseContext) -> Any:
    """Update project team members."""
    # Accept both "team" and "members" as parameter names
    team_data = ctx.args.get("team") or ctx.args.get("members") or []
    project_id = ctx.args.get("id") or ctx.args.get("project_id") or ctx.args.get("project")

    # AICODE-NOTE: Team update logic (t092, t097 fix)
    # If agent provides team data that covers existing members, use it as-is (allows role/workload changes).
    # Only merge with current team when agent provides PARTIAL new members to add.
    if team_data and ctx.context and hasattr(ctx.context, 'api'):
        try:
            resp = ctx.context.api.get_project(project_id)
            if hasattr(resp, 'project') and resp.project and hasattr(resp.project, 'team'):
                current_ids = set()
                for member in resp.project.team:
                    emp_id = getattr(member, 'employee', None)
                    if emp_id:
                        current_ids.add(emp_id)

                # Check if agent's team_data includes any existing members
                provided_ids = set()
                for m in team_data:
                    m_id = m.get('employee') if isinstance(m, dict) else getattr(m, 'employee', None)
                    if m_id:
                        provided_ids.add(m_id)

                # If agent provides existing members, they want to UPDATE them — use team_data as-is
                has_existing_members = bool(provided_ids & current_ids)
                if has_existing_members:
                    # Agent is updating existing team members — trust their data
                    pass  # Keep team_data unchanged
                else:
                    # Agent is adding NEW members only — merge with current team
                    current_team = []
                    for member in resp.project.team:
                        emp_id = getattr(member, 'employee', None)
                        if emp_id:
                            current_team.append({
                                "employee": emp_id,
                                "role": getattr(member, 'role', 'Other'),
                                "time_slice": getattr(member, 'time_slice', 0.0)
                            })
                    # Add new members
                    for new_member in team_data:
                        new_id = new_member.get('employee') if isinstance(new_member, dict) else getattr(new_member, 'employee', None)
                        if new_id and new_id not in current_ids:
                            current_team.append(new_member)
                    team_data = current_team
        except Exception:
            pass  # Fall back to original team_data

    normalized_team = normalize_team_roles(team_data)
    return client.Req_UpdateProjectTeam(
        id=project_id,
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
    project_id = ctx.args.get("id") or ctx.args.get("project_id") or ctx.args.get("project")
    if not project_id:
        return ParseError(
            "projects_status_update requires project ID. Use 'id' or 'project' parameter.",
            tool="projects_status_update"
        )
    return client.Req_UpdateProjectStatus(
        id=project_id,
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
