"""
Project overlap analyzer.

Analyzes project overlaps between the current user and a target employee
to help disambiguate project references and authorization contexts.
"""
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from utils import CLI_YELLOW, CLI_CLR

if TYPE_CHECKING:
    from ..base import ToolContext


class ProjectOverlapAnalyzer:
    """
    Analyzes project overlaps to provide authorization-aware hints.

    When searching for another employee's projects, this analyzer detects
    shared projects and helps determine which project is the logical choice
    based on the current user's authorization (Lead role).
    """

    def __init__(self):
        self._hint_cache: Set[Tuple[str, str]] = set()
        self._project_cache: Dict[str, List[Any]] = {}
        self._project_detail_cache: Dict[str, Any] = {}

    def clear_caches(self) -> None:
        """Clear all caches. Call at start of each turn."""
        self._hint_cache.clear()
        self._project_cache.clear()
        self._project_detail_cache.clear()

    def analyze(
        self,
        ctx: 'ToolContext',
        search_result: Any,
        task_text: str = ""
    ) -> Optional[str]:
        """
        Analyze project search results for overlaps with current user.

        Args:
            ctx: Tool context with API and shared state
            search_result: Response from projects_search
            task_text: Current task text for keyword filtering

        Returns:
            Hint string or None if no useful overlap found
        """
        # Get team filter from search
        team_filter = getattr(ctx.model, "team", None)
        if not team_filter or not getattr(team_filter, "employee_id", None):
            print(f"  {CLI_YELLOW}📊 Overlap analysis skipped: no team filter{CLI_CLR}")
            return None

        # Get current user
        security_manager = ctx.shared.get("security_manager")
        current_user = getattr(security_manager, "current_user", None) if security_manager else None
        if not current_user:
            return None

        target_employee = team_filter.employee_id
        if not target_employee or target_employee == current_user:
            return None

        target_projects = getattr(search_result, "projects", None) or []
        if not target_projects:
            return None

        # Avoid spamming the same hint multiple times per target/turn
        hint_key = (target_employee, ctx.model.__class__.__name__)
        if hint_key in self._hint_cache:
            return None

        # Gather the current user's projects to detect overlaps
        own_projects = self._fetch_projects_for_member(ctx, current_user)
        if not own_projects:
            return None

        target_project_map = {getattr(p, "id", None): p for p in target_projects if getattr(p, "id", None)}
        overlap = [p for p in own_projects if getattr(p, "id", None) in target_project_map]

        if not overlap:
            return None

        self._hint_cache.add(hint_key)

        # Extract filter keywords from task
        filter_keywords = self._extract_filter_keywords(task_text)

        # If keywords found, do keyword-filtered analysis
        if filter_keywords:
            hint = self._analyze_with_keywords(
                ctx, overlap, target_projects, target_employee,
                current_user, filter_keywords, hint_key
            )
            if hint:
                return hint

        # Fallback to general overlap analysis
        return self._analyze_general_overlap(
            ctx, overlap, target_projects, target_employee, current_user
        )

    def _extract_filter_keywords(self, task_text: str) -> List[str]:
        """Extract project-identifying keywords from task text."""
        task_lower = task_text.lower()
        keywords = []

        # Common project-type patterns
        if "cv " in task_lower or " cv" in task_lower or "cv project" in task_lower:
            keywords.append("cv")

        # Add more patterns as needed (triage, poc, etc.)
        return keywords

    def _analyze_with_keywords(
        self,
        ctx: 'ToolContext',
        overlap: List[Any],
        target_projects: List[Any],
        target_employee: str,
        current_user: str,
        filter_keywords: List[str],
        hint_key: Tuple[str, str]
    ) -> Optional[str]:
        """Analyze overlap with keyword filtering."""
        # Filter overlap by keywords
        filtered_overlap = self._filter_by_keywords(overlap, filter_keywords)

        # Find keyword-matching projects for target
        target_keyword_matches = self._filter_by_keywords(target_projects, filter_keywords)

        if filtered_overlap:
            overlap = filtered_overlap
            print(f"  {CLI_YELLOW}📊 Filtered overlap by keywords {filter_keywords}: {len(overlap)} projects{CLI_CLR}")

        # Find projects where current_user is Lead among keyword matches
        lead_keyword_projects = self._find_lead_projects(ctx, target_keyword_matches, current_user)

        # Print debug info only if there are keyword matches
        if target_keyword_matches:
            print(f"  {CLI_YELLOW}📊 Target keyword projects: {target_employee} works on {len(target_keyword_matches)} '{filter_keywords[0]}' projects{CLI_CLR}")
            for p in target_keyword_matches:
                print(f"     - {self._format_project_label(p)}")
            print(f"  {CLI_YELLOW}📊 Lead check: {current_user} is Lead of {len(lead_keyword_projects)} of them{CLI_CLR}")
        else:
            return None  # No keyword matches - skip hinting

        # Authorization-based disambiguation
        if len(lead_keyword_projects) > 1:
            # Multiple Lead projects - ambiguity!
            lead_labels = [self._format_project_label(p) for p in lead_keyword_projects]
            return (
                f"⚠️ AMBIGUITY: You ({current_user}) are the Lead of {len(lead_keyword_projects)} "
                f"'{filter_keywords[0]}' projects where {target_employee} is a member: "
                f"{', '.join(lead_labels)}. "
                f"Return `none_clarification_needed` asking which project to log time to."
            )
        elif len(lead_keyword_projects) == 1:
            # Exactly 1 Lead project - clear choice!
            return (
                f"💡 AUTHORIZATION MATCH: You ({current_user}) are the Lead of exactly 1 "
                f"'{filter_keywords[0]}' project where {target_employee} works: "
                f"{self._format_project_label(lead_keyword_projects[0])}. "
                f"This is the correct project to log time to. "
                f"IMPORTANT: Include BOTH {target_employee} AND {current_user} (yourself as authorizer) in response links!"
            )

        return None

    def _analyze_general_overlap(
        self,
        ctx: 'ToolContext',
        overlap: List[Any],
        target_projects: List[Any],
        target_employee: str,
        current_user: str
    ) -> str:
        """Analyze overlap without keyword filtering."""
        total_results = len(target_projects)

        if len(overlap) == 1:
            return self._analyze_single_overlap(
                ctx, overlap[0], target_employee, current_user, total_results
            )
        else:
            return self._analyze_multiple_overlap(
                ctx, overlap, target_employee, current_user
            )

    def _analyze_single_overlap(
        self,
        ctx: 'ToolContext',
        project: Any,
        target_employee: str,
        current_user: str,
        total_results: int
    ) -> str:
        """Analyze single overlapping project."""
        project_id = getattr(project, "id", None)
        is_lead = self._check_is_lead(ctx, project_id, current_user)

        if is_lead:
            return (
                f"💡 AUTHORIZATION MATCH: {self._format_project_label(project)} is the ONLY project "
                f"where both you ({current_user}) and {target_employee} are members, "
                f"AND you are the Lead (authorized to log time for others). "
                f"Even though search returned {total_results} total projects for {target_employee}, "
                f"this is the logical choice because it's the only one where you have authorization. "
                f"IMPORTANT: Include BOTH {target_employee} AND {current_user} (yourself as authorizer) in response links!"
            )
        else:
            return (
                f"💡 CONTEXT: Search returned {total_results} projects for {target_employee}. "
                f"You share 1 project with them: {self._format_project_label(project)}, "
                f"but you are NOT the Lead. Check authorization via other means (Account Manager, Direct Manager)."
            )

    def _analyze_multiple_overlap(
        self,
        ctx: 'ToolContext',
        overlap: List[Any],
        target_employee: str,
        current_user: str
    ) -> str:
        """Analyze multiple overlapping projects."""
        lead_projects = self._find_lead_projects(ctx, overlap, current_user)

        if len(lead_projects) == 1:
            return (
                f"💡 AUTHORIZATION MATCH: Found {len(overlap)} shared projects, "
                f"but you are the Lead of only 1: {self._format_project_label(lead_projects[0])}. "
                f"This is the logical choice for logging time. "
                f"IMPORTANT: Include BOTH {target_employee} AND {current_user} (yourself as authorizer) in response links!"
            )
        elif len(lead_projects) > 1:
            lead_labels = [self._format_project_label(p) for p in lead_projects]
            return (
                f"⚠️ AMBIGUITY: You are the Lead of {len(lead_projects)} shared projects with {target_employee}: "
                f"{', '.join(lead_labels)}. "
                f"Return `none_clarification_needed` listing these {len(lead_projects)} projects."
            )
        else:
            overlap_labels = [self._format_project_label(p) for p in overlap]
            return (
                f"💡 CONTEXT: Found {len(overlap)} shared projects but you are NOT the Lead of any: "
                f"{', '.join(overlap_labels)}. "
                f"Check authorization via Account Manager or Direct Manager roles."
            )

    def _filter_by_keywords(self, projects: List[Any], keywords: List[str]) -> List[Any]:
        """Filter projects by keyword presence in id or name."""
        filtered = []
        for p in projects:
            proj_id = (getattr(p, "id", "") or "").lower()
            proj_name = (getattr(p, "name", "") or "").lower()
            for kw in keywords:
                if kw in proj_id or kw in proj_name:
                    filtered.append(p)
                    break
        return filtered

    def _find_lead_projects(
        self,
        ctx: 'ToolContext',
        projects: List[Any],
        user_id: str
    ) -> List[Any]:
        """Find projects where the user is Lead."""
        lead_projects = []
        for proj in projects:
            project_id = getattr(proj, "id", None)
            if project_id and self._check_is_lead(ctx, project_id, user_id):
                lead_projects.append(proj)
        return lead_projects

    def _check_is_lead(self, ctx: 'ToolContext', project_id: str, user_id: str) -> bool:
        """Check if user is Lead of a project."""
        if not project_id:
            return False

        project_detail = self._get_project_detail(ctx, project_id)
        if not project_detail:
            return False

        team = getattr(project_detail, "team", None) or []
        for member in team:
            employee = getattr(member, "employee", getattr(member, "employee_id", None))
            role = getattr(member, "role", None)
            if employee == user_id and role == "Lead":
                return True
        return False

    def _format_project_label(self, project: Any) -> str:
        """Format project for display."""
        proj_id = getattr(project, "id", "unknown-id")
        proj_name = getattr(project, "name", proj_id)
        return f"'{proj_name}' ({proj_id})"

    def _fetch_projects_for_member(self, ctx: 'ToolContext', employee_id: str) -> List[Any]:
        """Fetch all projects where employee is a member."""
        if not employee_id:
            return []

        if employee_id in self._project_cache:
            return self._project_cache[employee_id]

        # Import here to avoid circular imports
        from erc3.erc3 import client, dtos

        projects: List[Any] = []
        limit = 5  # server hard-limit
        max_pages = 4  # up to 20 projects

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

    def _get_project_detail(self, ctx: 'ToolContext', project_id: str) -> Any:
        """Get full project details including team."""
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
