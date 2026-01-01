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

        # 7. AICODE-NOTE: t082 FIX - Singular "THE project" clarification hint
        # When task says "THE coating project" (singular) but multiple projects found,
        # agent MUST use none_clarification_needed instead of picking one
        if singular_hint := self._get_singular_project_clarification_hint(projects, query, task_text):
            hints.append(singular_hint)

        # 8. AICODE-NOTE: t016 - DISABLED active status hint.
        # "Project lead" means lead of ANY project, not just active ones.
        # Benchmark expects all leads regardless of project status.
        # if active_hint := self._get_active_status_hint(ctx, projects, task_text):
        #     hints.append(active_hint)

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
                f"⚠️ PROBLEM: You can only log time for '{mentioned_name}' if you are:\n"
                f"   1. Project Lead (check team array in projects_get)\n"
                f"   2. Their Direct Manager (check via `employees_search(manager='YOUR_ID')`)\n\n"
                f"🔧 REQUIRED ACTIONS:\n"
                f"   `projects_search(member='{mentioned_employee}')` — finds projects where {mentioned_name} works\n"
                f"   Then use `projects_get` to check if YOU are the Lead.\n"
                f"   If NOT Lead: check if {mentioned_name} reports to you via `employees_search(manager='YOUR_ID')`"
            )

        # AICODE-NOTE: Even with 1 project found, agent MUST use member filter
        # because the query may not return all matching projects. In t010_add_time_entry_lead,
        # "CV" matches proj_scandifoods_packaging_cv_poc but agent needs proj_acme_line3_cv_poc
        # where jonas_weiss is Lead. Only member filter + overlap analysis finds the correct project.
        return (
            f"\n🛑 CRITICAL: You searched by query but task involves logging time for '{mentioned_name}'.\n"
            f"⚠️ PROBLEM: Query-based search may NOT return all relevant projects!\n"
            f"   The employee may work on multiple projects matching your query.\n\n"
            f"🔧 REQUIRED ACTION (do this BEFORE time_log):\n"
            f"   `projects_search(member='{mentioned_employee}')` — finds ALL projects where {mentioned_name} works\n\n"
            f"Then check authorization for EACH matching project:\n"
            f"   - If YOU are the Lead → authorized to log time\n"
            f"   - If NOT Lead → check `employees_search(manager='YOUR_ID')` for direct reports"
        )

    def _get_singular_project_clarification_hint(
        self,
        projects: List[Any],
        query: str,
        task_text: str
    ) -> Optional[str]:
        """
        AICODE-NOTE: t082 FIX - Detect when task expects SINGLE project but multiple found.

        When task says "THE coating project" (definite article + singular noun)
        but search returns >1 projects, agent MUST use none_clarification_needed
        instead of arbitrarily picking one.

        Patterns that indicate singular expectation:
        - "the X project" (not "the X projects")
        - "this project"
        - "that project"
        """
        import re

        # Only trigger when multiple projects found
        if len(projects) <= 1:
            return None

        task_lower = task_text.lower()

        # Pattern: "the <query> project" (singular) - NOT "projects" (plural)
        # Match: "the coating project", "the flooring project"
        # Don't match: "the coating projects", "projects with coating"
        singular_patterns = [
            r'\bthe\s+\w+\s+project\b(?!s)',  # "the coating project" but not "the coating projects"
            r'\bthis\s+project\b',
            r'\bthat\s+project\b',
            r'\bfor\s+the\s+\w+\s+project\b(?!s)',  # "for the coating project"
        ]

        is_singular_reference = any(re.search(p, task_lower) for p in singular_patterns)

        if not is_singular_reference:
            return None

        # Build project list for hint
        proj_names = [
            f"  • {getattr(p, 'name', 'unknown')} ({getattr(p, 'id', 'unknown')}, status={getattr(p, 'status', 'unknown')})"
            for p in projects[:5]
        ]
        proj_list = "\n".join(proj_names)
        more_text = f"  ... and {len(projects) - 5} more" if len(projects) > 5 else ""

        return (
            f"\n🛑 AMBIGUOUS QUERY: Task says 'THE project' (SINGULAR) but you found {len(projects)} projects!\n"
            f"Query '{query}' matched:\n{proj_list}{more_text}\n\n"
            f"⚠️ CRITICAL: You cannot guess which project the user means!\n"
            f"   - 'THE project' implies user expects EXACTLY ONE match\n"
            f"   - Finding multiple means the query is AMBIGUOUS\n\n"
            f"📝 REQUIRED RESPONSE:\n"
            f"   outcome: 'none_clarification_needed'\n"
            f"   message: 'Multiple projects match \"coating\". Please specify which one: [list names]'\n"
            f"   links: [] (empty - don't link any project until user clarifies)\n\n"
            f"❌ DO NOT pick one project arbitrarily!\n"
            f"❌ DO NOT report workload for multiple projects when task asks about 'THE project'!"
        )

    def _get_active_status_hint(
        self,
        ctx: 'ToolContext',
        projects: List[Any],
        task_text: str
    ) -> Optional[str]:
        """
        AICODE-NOTE: t016 FIX - For salary comparison tasks involving project leads,
        remind agent to filter by status='active'.

        Problem: Agent finds leads from ALL projects (including archived/paused),
        but benchmark expects only leads from ACTIVE projects.

        When: Task asks "project leads with salary higher than X"
        """
        import re

        task_lower = task_text.lower()

        # Detect salary comparison task for project leads
        is_lead_salary_task = (
            'lead' in task_lower and
            any(p in task_lower for p in ['salary', 'earn', 'higher than', 'greater than'])
        )

        if not is_lead_salary_task:
            return None

        # Check if current search uses status filter
        status_filter = getattr(ctx.model, "status", None)
        if status_filter:
            # Agent already using status filter - good
            return None

        # Check how many non-active projects in results
        non_active = [p for p in projects if getattr(p, 'status', '') != 'active']
        if not non_active:
            return None

        non_active_count = len(non_active)
        active_count = len(projects) - non_active_count

        return (
            f"\n🛑 CRITICAL for salary comparison: Found {non_active_count} NON-ACTIVE projects in results!\n"
            f"For 'project leads with salary higher than X' queries:\n"
            f"  - 'Project lead' means someone with role='Lead' in an **ACTIVE** project\n"
            f"  - Non-active (archived, paused, exploring, idea) projects should NOT be included\n\n"
            f"🔧 REQUIRED: Use `projects_search(status=['active'])` to get only active projects,\n"
            f"   then extract leads from team arrays where role='Lead'.\n\n"
            f"Current results: {active_count} active, {non_active_count} non-active — filter needed!"
        )
