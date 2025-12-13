"""
Response enrichers for adding context to API responses.
"""
from typing import Any, Dict, List, Optional

from erc3.erc3 import client

from utils import CLI_YELLOW, CLI_CLR


class RoleEnricher:
    """
    Enriches project responses with user role information.
    Helps weaker models understand their permissions without extra API calls.
    """

    def enrich_projects_with_user_role(
        self,
        result: Any,
        current_user: str
    ) -> Optional[str]:
        """
        Analyze project response and add YOUR_ROLE hint for current user.

        Args:
            result: API response (SearchProjects or GetProject)
            current_user: Current user's employee ID

        Returns:
            Hint string to append to results, or None
        """
        projects = []

        # Handle both SearchProjects (list) and GetProject (single)
        if hasattr(result, 'projects') and result.projects:
            projects = result.projects
        elif hasattr(result, 'project') and result.project:
            projects = [result.project]

        if not projects:
            return None

        lead_projects = []
        member_projects = []

        for proj in projects:
            proj_id = getattr(proj, 'id', None)
            proj_name = getattr(proj, 'name', proj_id)
            team = getattr(proj, 'team', None) or []

            user_role = None
            for member in team:
                employee = getattr(member, 'employee', getattr(member, 'employee_id', None))
                if employee == current_user:
                    user_role = getattr(member, 'role', 'Member')
                    break

            if user_role:
                if user_role == 'Lead':
                    lead_projects.append(f"'{proj_name}' ({proj_id})")
                else:
                    member_projects.append(f"'{proj_name}' ({proj_id}) as {user_role}")

        # Build concise hint
        hints = []
        if lead_projects:
            if len(lead_projects) == 1:
                hints.append(
                    f"YOUR_ROLE: You ({current_user}) are the LEAD of {lead_projects[0]}. "
                    f"As PROJECT Lead, you CAN change status (archive, pause, etc.) - this IS 'specifically allowed' per rulebook! "
                    f"Proceed with `projects_status_update`."
                )
            else:
                hints.append(
                    f"YOUR_ROLE: You ({current_user}) are the LEAD of {len(lead_projects)} projects: "
                    f"{', '.join(lead_projects)}."
                )

        if member_projects and len(member_projects) <= 3:
            hints.append(f"YOUR_ROLE: You are a team member of: {', '.join(member_projects)}")
        elif member_projects:
            hints.append(f"YOUR_ROLE: You are a team member of {len(member_projects)} projects.")

        if not hints:
            # User is not in any of these projects - also useful info!
            if len(projects) == 1:
                hints.append(
                    f"YOUR_ROLE: You ({current_user}) are NOT a member of this project. "
                    f"Check if you have Account Manager or Direct Manager authorization."
                )

        return "\n".join(hints) if hints else None


class ArchiveHintEnricher:
    """
    Provides hints when searching archived projects for time logging.
    """

    def maybe_hint_archived_logging(
        self,
        request_model: Any,
        response: Any,
        task_text: str
    ) -> Optional[str]:
        """
        Generate hint if searching archived projects for time logging.

        Args:
            request_model: The request model
            response: API response
            task_text: Task instructions

        Returns:
            Hint string or None
        """
        instructions = task_text.lower()
        # Check for time-related keywords: "log" AND ("time" OR "hour")
        if "log" not in instructions:
            return None
        if "time" not in instructions and "hour" not in instructions:
            return None

        projects: List[Any] = []
        if isinstance(request_model, client.Req_SearchProjects):
            projects = getattr(response, "projects", None) or []
        elif isinstance(request_model, client.Req_GetProject):
            project = getattr(response, "project", None)
            if project:
                projects = [project]
        else:
            return None

        archived = [
            p for p in projects
            if getattr(p, "status", "").lower() == "archived"
        ]

        if not archived:
            return None

        project_labels = ", ".join(self._format_project_label(p) for p in archived)
        return (
            f"AUTO-HINT: {project_labels} is archived, yet your instructions explicitly ask to log time. "
            "Use `/time/log` with the provided project ID to backfill the requested hours - archival usually "
            "means delivery wrapped up, not that historical time entries are disallowed."
        )

    def _format_project_label(self, project: Any) -> str:
        """Format project for display in hints."""
        proj_id = getattr(project, "id", "unknown-id")
        proj_name = getattr(project, "name", proj_id)
        return f"'{proj_name}' ({proj_id})"


class TimeEntryHintEnricher:
    """
    Provides hints for time entry operations.
    """

    def maybe_hint_time_update(
        self,
        result: Any,
        task_text: str
    ) -> Optional[str]:
        """
        Generate hint if found time entries and task suggests modification.

        Args:
            result: API response with entries
            task_text: Task instructions

        Returns:
            Hint string or None
        """
        entries = getattr(result, 'entries', None) or []
        if not entries:
            return None

        task_lower = task_text.lower()
        update_keywords = ['change', 'fix', 'update', 'correct', 'modify', 'edit', 'adjust']
        is_update_task = any(kw in task_lower for kw in update_keywords)

        if not is_update_task:
            return None

        entry_ids = [getattr(e, 'id', 'unknown') for e in entries[:3]]
        return (
            f"TIME UPDATE HINT: You found {len(entries)} time entries. "
            f"To MODIFY an existing entry, use `time_update(id='...', hours=X, ...)`. "
            f"Entry IDs: {', '.join(entry_ids)}{'...' if len(entries) > 3 else ''}. "
            f"Do NOT use `time_log` to fix existing entries - that creates duplicates!"
        )


class CustomerSearchHintEnricher:
    """
    Provides hints for empty customer search results.
    """

    def maybe_hint_empty_customers(
        self,
        model: Any,
        result: Any
    ) -> Optional[str]:
        """
        Generate hint for empty customer search with multiple filters.

        Args:
            model: The request model
            result: API response

        Returns:
            Hint string or None
        """
        if not isinstance(model, client.Req_SearchCustomers):
            return None

        customers = getattr(result, 'customers', None) or []
        if customers:
            return None

        # Count how many filters are active
        active_filters = []
        if getattr(model, 'locations', None):
            active_filters.append(f"locations={model.locations}")
        if getattr(model, 'deal_phase', None):
            active_filters.append(f"deal_phase={model.deal_phase}")
        if getattr(model, 'account_managers', None):
            active_filters.append(f"account_managers={model.account_managers}")
        if getattr(model, 'query', None):
            active_filters.append(f"query={model.query}")

        if len(active_filters) < 2:
            return None

        return (
            f"EMPTY RESULTS with {len(active_filters)} filters: {', '.join(active_filters)}. "
            f"BROADEN YOUR SEARCH! Try:\n"
            f"  1. Remove location filter (API may use different names like 'DK' vs 'Denmark' vs 'Danmark')\n"
            f"  2. Search with fewer filters, then manually inspect results\n"
            f"  3. Try `customers_search(account_managers=['your_id'])` to see ALL your customers, then filter yourself"
        )


class PaginationHintEnricher:
    """
    Provides hints for paginated results.
    """

    def maybe_hint_pagination(self, result: Any) -> Optional[str]:
        """
        Generate hint if there are more pages of results.

        Args:
            result: API response

        Returns:
            Hint string or None
        """
        next_offset = getattr(result, 'next_offset', None)
        if next_offset is None or next_offset <= 0:
            return None

        return (
            f"PAGINATION: next_offset={next_offset} means there are MORE results! "
            f"Use offset={next_offset} in your next search to get the remaining items. "
            f"Do NOT assume you found everything!"
        )
