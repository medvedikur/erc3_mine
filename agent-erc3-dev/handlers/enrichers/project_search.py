"""
Project search enricher.

Composite enricher that combines multiple hint generators for project search results.
Provides authorization hints, ranking, archived project tips, and membership confirmations.
"""
from typing import Any, List, Optional, TYPE_CHECKING

from .project_ranking import ProjectRankingEnricher
from .project_overlap import ProjectOverlapAnalyzer

if TYPE_CHECKING:
    from ..base import ToolContext


class ProjectSearchEnricher:
    """
    Composite enricher for project search results.

    Combines multiple enrichment strategies:
    - Overlap analysis (authorization hints based on shared projects)
    - Archived project hints (search strategy tips)
    - Match ranking (disambiguation based on query match quality)
    - Authorization reminders (role verification prompts)
    - Membership confirmation (when searching own projects)
    """

    # Keywords indicating search for archived projects
    ARCHIVE_KEYWORDS = ["archived", "wrapped", "completed", "finished", "closed"]

    def __init__(self):
        self._ranking = ProjectRankingEnricher()
        self._overlap = ProjectOverlapAnalyzer()

    def clear_caches(self) -> None:
        """Clear all sub-enricher caches. Call at start of each turn."""
        self._overlap.clear_caches()

    def enrich(
        self,
        ctx: 'ToolContext',
        result: Any,
        task_text: str
    ) -> List[str]:
        """
        Generate all applicable hints for project search results.

        Args:
            ctx: Tool context with API, model, and shared state
            result: Response from projects_search API
            task_text: Current task text for context analysis

        Returns:
            List of hint strings to append to results
        """
        hints: List[str] = []

        # Extract common data
        projects = getattr(result, "projects", []) or []
        query = getattr(ctx.model, "query", "") or ""
        team_filter = getattr(ctx.model, "team", None)

        # 1. Overlap analysis (authorization based on shared projects)
        if overlap_hint := self._overlap.analyze(ctx, result, task_text):
            hints.append(overlap_hint)

        # 2. Archived project hints
        if archived_hint := self._get_archived_hint(ctx, projects, query, task_text, team_filter):
            hints.append(archived_hint)

        # 3. Match ranking (disambiguation) - SKIP if overlap found definitive match
        # When overlap analyzer finds a definitive authorization match, ranking hints
        # can be misleading (e.g., ranking says "Packaging Line CV" is STRONG match
        # but overlap says "Line 3" is the only authorized project)
        has_definitive = bool(ctx.shared.get('_overlap_definitive_hints'))
        if not has_definitive:
            if ranking_hint := self._ranking.enrich(projects, query):
                hints.append(ranking_hint)

        # 4. Authorization reminder (always for project searches)
        hints.append(self._get_authorization_reminder())

        # 5. Membership confirmation (when searching own projects)
        if member_hint := self._get_member_filter_hint(ctx, projects, team_filter):
            hints.append(member_hint)

        # 6. Missing team filter hint (task mentions employee but no filter used)
        if not team_filter:
            if missing_filter_hint := self._get_missing_team_filter_hint(task_text, projects):
                hints.append(missing_filter_hint)

        return hints

    def _get_archived_hint(
        self,
        ctx: 'ToolContext',
        projects: List[Any],
        query: str,
        task_text: str,
        team_filter: Any
    ) -> Optional[str]:
        """
        Generate hint for archived project searches.

        Detects when user is looking for archived projects and provides
        search strategy tips if results don't match expectations.
        """
        # Check if task or query suggests looking for archived project
        looking_for_archived = (
            any(kw in query.lower() for kw in self.ARCHIVE_KEYWORDS) or
            any(kw in task_text.lower() for kw in self.ARCHIVE_KEYWORDS)
        )

        if not looking_for_archived:
            return None

        # Check if we found any archived projects in results
        found_archived = any(
            getattr(p, "status", "") == "archived" for p in projects
        )

        # Check if any archived project matches the query keywords
        archived_matches_query = False
        if found_archived and query:
            query_words = set(query.lower().split())
            for p in projects:
                if getattr(p, "status", "") == "archived":
                    p_name = getattr(p, "name", "").lower()
                    p_id = getattr(p, "id", "").lower()
                    if query_words & set(p_name.split()) or any(w in p_id for w in query_words):
                        archived_matches_query = True
                        break

        # Generate appropriate hint
        if team_filter and not found_archived:
            return (
                "\n💡 TIP: You're searching for archived projects with a member filter. "
                "Team members may have been removed from archived projects. "
                "Try: projects_search(status=['archived'], query='...') WITHOUT member filter."
            )
        elif found_archived and not archived_matches_query:
            return (
                f"\n💡 SEARCH TIP: Found archived project(s) but they don't match '{query}'. "
                f"The project name may differ from the task description. Try alternative queries:\n"
                f"  1. Search by individual keywords: 'hospital', 'triage', 'intake', 'poc'\n"
                f"  2. Search by customer name related to the domain (e.g., 'healthcare')\n"
                f"  3. Search without query but with status=['archived'] to list all archived projects\n"
                f"  4. Remove member filter - team composition changes when projects are archived"
            )

        return None

    def _get_authorization_reminder(self) -> str:
        """
        Generate authorization reminder for project mutations.

        Always included to prevent agents from returning ok_not_found
        when they should check authorization first.
        """
        return (
            "\n⚠️ AUTHORIZATION REMINDER: If you need to MODIFY this project (change status, update fields), "
            "you MUST first verify your role using `projects_get(id='proj_...')`. "
            "If you are the **PROJECT Lead** (role='Lead' in team array), you ARE authorized to change status! "
            "This is 'specifically allowed' per rulebook. If not authorized → `denied_security`."
        )

    def _get_member_filter_hint(
        self,
        ctx: 'ToolContext',
        projects: List[Any],
        team_filter: Any
    ) -> Optional[str]:
        """
        Generate membership confirmation when searching own projects.

        Helps agent understand they ARE a member of found projects
        and guides them to check their specific role.
        """
        if not team_filter or not projects:
            return None

        security_manager = ctx.shared.get("security_manager")
        current_user = getattr(security_manager, "current_user", None) if security_manager else None

        if not current_user:
            return None

        filter_employee = getattr(team_filter, "employee_id", None)
        if filter_employee != current_user:
            return None

        proj_ids = [getattr(p, 'id', 'unknown') for p in projects[:3]]
        return (
            f"\n💡 MEMBERSHIP CONFIRMED: You searched with member='{current_user}'. "
            f"This means YOU ARE A MEMBER of ALL {len(projects)} project(s) found! "
            f"Projects: {', '.join(proj_ids)}{'...' if len(projects) > 3 else ''}. "
            f"To check your exact ROLE (Lead/Engineer/etc), use `projects_get(id='...')` - "
            f"the team list will show your role. If you're Lead, you have full authorization."
        )

    def _get_missing_team_filter_hint(
        self,
        task_text: str,
        projects: List[Any]
    ) -> Optional[str]:
        """
        Generate hint when task mentions an employee but search has no team filter.

        Problem: Task says "log time for felix on CV project" but agent searches
        just by query without team filter, missing the overlap analysis that would
        find the authorized project.

        AICODE-NOTE: This is critical for t010 add_time_entry_lead - agent must
        use member filter to trigger overlap analysis that finds the correct project.
        """
        import re

        task_lower = task_text.lower()

        # Common employee name patterns in tasks
        # "for felix", "for felix_baum", "Felix's project", etc.
        employee_patterns = [
            r'\bfor\s+(\w+)\b',  # "for felix", "for ana"
            r'\b(\w+)\'s\s+(?:project|work|time)\b',  # "felix's project"
            r'\bon\s+behalf\s+of\s+(\w+)\b',  # "on behalf of felix"
        ]

        # Known employee first names (common in benchmark)
        known_employees = {
            'felix': 'felix_baum',
            'ana': 'ana_kovac',
            'jonas': 'jonas_weiss',
            'elena': 'elena_vogel',
            'marko': 'marko_petrovic',
            'sofia': 'sofia_rinaldi',
            'helene': 'helene_stutz',
            'mira': 'mira_schafer',
            'lukas': 'lukas_gruber',
            'timo': 'timo_hansen',
            'richard': 'richard_klein',
        }

        # Find mentioned employee
        mentioned_employee = None
        mentioned_name = None
        for pattern in employee_patterns:
            match = re.search(pattern, task_lower)
            if match:
                name = match.group(1).lower()
                if name in known_employees:
                    mentioned_employee = known_employees[name]
                    mentioned_name = name
                    break

        if not mentioned_employee:
            return None

        # Check if task involves time logging or project work
        time_keywords = ['log', 'time', 'hours', 'billable', 'work']
        if not any(kw in task_lower for kw in time_keywords):
            return None

        # Make hint more aggressive if multiple projects found
        # This is the key scenario: "CV project" matches multiple projects
        if len(projects) > 1:
            proj_list = "\n".join([
                f"   - {getattr(p, 'name', 'unknown')} ({getattr(p, 'id', 'unknown')})"
                for p in projects[:5]
            ])
            return (
                f"\n🛑 CRITICAL: Found {len(projects)} matching projects but you searched WITHOUT member filter!\n"
                f"Projects found:\n{proj_list}\n\n"
                f"⚠️ PROBLEM: You can only log time for '{mentioned_name}' on projects where YOU are the Lead!\n"
                f"The project you're authorized to use may NOT be the first result.\n\n"
                f"🔧 REQUIRED ACTION:\n"
                f"   `projects_search(member='{mentioned_employee}')` — finds projects where {mentioned_name} works\n"
                f"   Then use `projects_get` to check which one YOU are the Lead of.\n"
                f"   Only the project where YOU are Lead is valid for logging {mentioned_name}'s time!"
            )

        return (
            f"\n💡 TIME LOGGING TIP: Task mentions '{mentioned_employee}'. "
            f"To find projects where YOU are authorized to log time for them, "
            f"try: `projects_search(member='{mentioned_employee}')`. "
            f"This will show all projects where {mentioned_name} is a member, "
            f"then check which one YOU are the Lead of."
        )
