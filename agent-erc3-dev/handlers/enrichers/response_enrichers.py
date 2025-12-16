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
                    f"For time logging authorization, check:\n"
                    f"  - Account Manager: `customers_get(id='cust_xxx')` to see if you manage the customer\n"
                    f"  - Direct Manager: `employees_search(manager='{current_user}')` to see if the employee reports to you"
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


class EmployeeSearchHintEnricher:
    """
    Provides hints for empty employee search results with location filter.

    When searching by location returns 0 results, hints that location matching
    is exact and suggests alternative approaches.
    """

    def maybe_hint_empty_employees(
        self,
        model: Any,
        result: Any
    ) -> Optional[str]:
        """
        Generate hint for empty employee search with location filter.

        Args:
            model: The request model
            result: API response

        Returns:
            Hint string or None
        """
        if not isinstance(model, client.Req_SearchEmployees):
            return None

        employees = getattr(result, 'employees', None) or []
        if employees:
            return None

        # Check if location filter was used
        location = getattr(model, 'location', None)
        if not location:
            return None

        return (
            f"EMPTY RESULTS with location='{location}'. "
            f"Location matching requires EXACT match (e.g., 'Barcelona Office – Spain', not 'Barcelona' or 'Spain'). "
            f"TRY:\n"
            f"  1. Use `employees_search()` without location filter, then paginate through ALL employees to find matching locations\n"
            f"  2. Check `wiki_search('locations')` for exact location format used in this company\n"
            f"  3. Common formats: 'City Office – Country', 'HQ – Country', 'Country'"
        )


class PaginationHintEnricher:
    """
    Provides hints for paginated results.
    """

    def maybe_hint_pagination(self, result: Any, model: Any = None) -> Optional[str]:
        """
        Generate hint if there are more pages of results.

        Args:
            result: API response
            model: Request model (optional, for context-specific hints)

        Returns:
            Hint string or None
        """
        next_offset = getattr(result, 'next_offset', None)
        if next_offset is None or next_offset <= 0:
            return None

        base_hint = (
            f"PAGINATION: next_offset={next_offset} means there are MORE results! "
            f"Use offset={next_offset} in your next search to get the remaining items. "
            f"Do NOT assume you found everything!"
        )

        # Add filter-specific hint for employees_search
        if model and isinstance(model, client.Req_SearchEmployees):
            # Check for manager filter - CRITICAL for time logging authorization
            has_manager_filter = getattr(model, 'manager', None)
            if has_manager_filter:
                base_hint += (
                    f"\n  🛑 CRITICAL: You're checking direct reports (manager='{has_manager_filter}'). "
                    f"The employee you need may be on the NEXT PAGE! "
                    f"You MUST paginate with offset={next_offset} to check ALL direct reports before concluding!"
                )
            else:
                # Check for skills or wills filter
                has_skill_filter = getattr(model, 'skills', None)
                has_will_filter = getattr(model, 'wills', None)
                has_department_filter = getattr(model, 'department', None)

                if (has_skill_filter or has_will_filter) and has_department_filter:
                    base_hint += (
                        f"\n  FILTER TIP: You searched with department='{has_department_filter}'. "
                        f"For COMPANY-WIDE skill/will ranking (e.g., 'find least skilled in X'), "
                        f"search WITHOUT department filter and paginate through ALL results!"
                    )
                elif has_skill_filter or has_will_filter:
                    base_hint += (
                        f"\n  FILTER TIP: Large result set with more pages. Available filters for "
                        f"`employees_search`: department=, location=, skill=, manager=. "
                        f"Using filters is faster than paginating through all results."
                    )

        return base_hint


class CustomerProjectsHintEnricher:
    """
    Provides hints for correct API usage when searching customer projects.

    Addresses the common confusion between `owner` and `customer` filter.
    """

    def __init__(self):
        self._hint_shown = False

    def maybe_hint_customer_filter(
        self,
        model: Any,
        task_text: str
    ) -> Optional[str]:
        """
        Generate hint when searching projects with owner filter but task mentions customer.

        Args:
            model: The request model
            task_text: Task instructions

        Returns:
            Hint string or None
        """
        if not isinstance(model, client.Req_SearchProjects):
            return None

        # Only show once
        if self._hint_shown:
            return None

        task_lower = task_text.lower()
        owner_filter = getattr(model, 'owner', None)

        # Detect if task is about customer projects but agent used owner filter
        customer_keywords = ['customer', 'client', 'account']
        is_customer_query = any(kw in task_lower for kw in customer_keywords)

        if is_customer_query and owner_filter:
            # Check if owner looks like a customer ID
            if owner_filter.startswith('cust_'):
                self._hint_shown = True
                return (
                    f"API FILTER WARNING: You used `owner={owner_filter}` but `owner` is for employee IDs (project lead). "
                    f"To find projects for a CUSTOMER, use `customer={owner_filter}` instead!\n"
                    f"  - `owner=employee_id` — projects where employee is Lead\n"
                    f"  - `customer=customer_id` — projects for specific customer"
                )

        return None

    def clear_cache(self):
        """Reset hint shown flag for new task."""
        self._hint_shown = False


class SearchResultExtractionHintEnricher:
    """
    Provides hints when agent might be generating IDs instead of extracting them.

    Addresses the pattern where agents guess entity IDs like 'cust_carpathia_mw'
    instead of extracting the actual ID from search results.
    """

    def maybe_hint_id_extraction(
        self,
        model: Any,
        result: Any,
        action_name: str
    ) -> Optional[str]:
        """
        Generate hint if a get request fails and ID looks auto-generated.

        Args:
            model: The request model
            result: API response
            action_name: Name of the action

        Returns:
            Hint string or None
        """
        # Only for get operations that failed
        if 'get' not in action_name.lower():
            return None

        # Check for error in result
        error = getattr(result, 'error', None)
        if not error:
            return None

        # Get the ID that was used
        entity_id = getattr(model, 'id', None)
        if not entity_id:
            return None

        # Patterns that suggest auto-generated IDs
        # Real IDs: cust_adriatic_marine_services, proj_acme_line3_cv_poc
        # Guessed IDs: cust_carpathia_mw (abbreviation), cust_centralsteel_eng (truncated)
        guessed_patterns = [
            '_mw', '_eng', '_sys', '_corp', '_inc', '_ltd',  # Common abbreviations
        ]

        looks_guessed = any(pat in entity_id.lower() for pat in guessed_patterns)
        if not looks_guessed:
            # Also check if ID is suspiciously short
            parts = entity_id.split('_')
            if len(parts) >= 2 and len(parts[-1]) <= 3:
                looks_guessed = True

        if looks_guessed:
            return (
                f"ID EXTRACTION WARNING: '{entity_id}' returned an error. "
                f"Did you EXTRACT this ID from search results or GENERATE it yourself?\n"
                f"ALWAYS use the exact ID from the search response, e.g.:\n"
                f'  Search result: {{"id": "cust_adriatic_marine_services", ...}}\n'
                f'  Correct: customers_get(id="cust_adriatic_marine_services")\n'
                f"  WRONG: customers_get(id=\"cust_adriatic_ms\")  // guessed abbreviation"
            )

        return None


class ProjectNameNormalizationHintEnricher:
    """
    Provides hints for project name search normalization.

    Addresses issues where project search fails due to format differences
    like dashes vs spaces: "HV-anti-corrosion" vs "HV anti corrosion"
    """

    def maybe_hint_name_normalization(
        self,
        model: Any,
        result: Any
    ) -> Optional[str]:
        """
        Generate hint if project search returns empty with special characters in query.

        Args:
            model: The request model
            result: API response

        Returns:
            Hint string or None
        """
        if not isinstance(model, client.Req_SearchProjects):
            return None

        projects = getattr(result, 'projects', None) or []
        if projects:
            return None

        query = getattr(model, 'query', None)
        if not query:
            return None

        # Check for special characters that might cause matching issues
        has_dashes = '-' in query
        has_underscores = '_' in query

        if has_dashes or has_underscores:
            normalized_query = query.replace('-', ' ').replace('_', ' ')
            return (
                f"EMPTY SEARCH: No projects found for '{query}'. "
                f"Project names might use different formats:\n"
                f"  - Try with spaces: `projects_search(query=\"{normalized_query}\")`\n"
                f"  - Try partial match: `projects_search(query=\"{query.split('-')[0]}\")`\n"
                f"  - Try without special chars: search for key words separately"
            )

        return None
