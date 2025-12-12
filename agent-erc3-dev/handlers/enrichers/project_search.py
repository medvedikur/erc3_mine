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

        # 3. Match ranking (disambiguation)
        if ranking_hint := self._ranking.enrich(projects, query):
            hints.append(ranking_hint)

        # 4. Authorization reminder (always for project searches)
        hints.append(self._get_authorization_reminder())

        # 5. Membership confirmation (when searching own projects)
        if member_hint := self._get_member_filter_hint(ctx, projects, team_filter):
            hints.append(member_hint)

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
            "Only Lead, Owner, or Direct Manager of Lead can modify project status. "
            "If not authorized → respond `denied_security`, NOT `ok_not_found`!"
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
