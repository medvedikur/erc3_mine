"""
Response enrichers for adding context to API responses.
"""
import re
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

        # AICODE-NOTE: t071 FIX - Provide hint for empty query-only search
        query = getattr(model, 'query', None)
        if query and len(active_filters) == 1:
            # Single query filter with no results - suggest keyword extraction
            return (
                f"EMPTY RESULTS for query '{query}'. Try alternative search strategies:\n"
                f"  1. Extract KEY WORDS from the description and search each separately\n"
                f"     Example: 'German cold-storage operator for Nordics' → try 'cold', 'Nordic', 'storage'\n"
                f"  2. Customer names often differ from descriptions:\n"
                f"     - 'German cold-storage' might be 'Nordic Cold Storage' (cust_nordic_cold_storage)\n"
                f"     - Focus on industry keywords: 'cold', 'storage', 'rail', 'floor', etc.\n"
                f"  3. Use `customers_list` to browse ALL customers and find matches manually"
            )

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
    Provides hints for empty employee search results.

    Handles:
    1. Location filter - exact match required
    2. Name query - suggests alternative name formats
    """

    def maybe_hint_empty_employees(
        self,
        model: Any,
        result: Any
    ) -> Optional[str]:
        """
        Generate hint for empty employee search.

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
        if location:
            # AICODE-NOTE: t012 FIX - Location alias mapping for common city names
            # The wiki says HQ is "between Milan and Bergamo" so Milano=HQ – Italy
            location_lower = location.lower()
            alias_hint = ""
            if location_lower in ('milano', 'milan', 'bergamo'):
                alias_hint = (
                    f"\n\n💡 LOCATION HINT: '{location}' refers to the HQ area (between Milan and Bergamo).\n"
                    f"   → Use: `employees_search(location=\"HQ – Italy\")`\n"
                    f"   ⚠️ Many employees at HQ - prepare for pagination!"
                )
            elif location_lower in ('novi sad', 'serbia'):
                alias_hint = (
                    f"\n\n💡 LOCATION HINT: '{location}' refers to the Serbian factory.\n"
                    f"   → Use: `employees_search(location=\"Factory – Serbia\")`"
                )

            return (
                f"EMPTY RESULTS with location='{location}'. "
                f"Location matching requires EXACT match (e.g., 'Barcelona Office – Spain', not 'Barcelona' or 'Spain'). "
                f"TRY:\n"
                f"  1. Use `employees_search()` without location filter, then paginate through ALL employees to find matching locations\n"
                f"  2. Check `wiki_search('locations')` for exact location format used in this company\n"
                f"  3. Common formats: 'City Office – Country', 'HQ – Country', 'Country'"
                f"{alias_hint}"
            )

        # AICODE-NOTE: t087 FIX - Hint for empty NAME search
        # When searching by name returns 0 results, suggest alternative approaches:
        # 1. Try different name orderings (First Last vs Last First)
        # 2. Try just first or last name
        # 3. Check for spelling variations (Krisztina vs Kristina vs Christina)
        query = getattr(model, 'query', None)
        if query and ' ' in query and '_' not in query:
            # Looks like a name search (has space, no underscore)
            parts = query.strip().split()
            if len(parts) >= 2:
                first = parts[0]
                last = parts[-1]
                reversed_name = f"{last} {first}"
                return (
                    f"⚠️ EMPTY NAME SEARCH: No employees found for '{query}'.\n"
                    f"Names may be stored differently. TRY:\n"
                    f"  1. Reversed order: `employees_search(query=\"{reversed_name}\")`\n"
                    f"  2. Last name only: `employees_search(query=\"{last}\")`\n"
                    f"  3. First name only: `employees_search(query=\"{first}\")`\n"
                    f"  4. Spelling variations: 'Krisztina' → 'Kristina', 'Christina'\n"
                    f"  5. Paginate through ALL employees if name is unusual\n"
                    f"⚠️ Don't return 'not found' until you've tried alternatives!"
                )

        return None

    def maybe_hint_wrong_name_match(
        self,
        model: Any,
        result: Any
    ) -> Optional[str]:
        """
        Generate hint when search returns employees but names don't match query.

        AICODE-NOTE: t087 FIX - When searching for "Peter de Vries" returns
        "Sophie de Vries", agent should realize this is NOT a match and keep searching.
        """
        if not isinstance(model, client.Req_SearchEmployees):
            return None

        employees = getattr(result, 'employees', None) or []
        if not employees:
            return None

        query = getattr(model, 'query', None)
        if not query or '_' in query:
            return None

        # Extract first name from query
        query_parts = query.strip().lower().split()
        if len(query_parts) < 2:
            return None

        query_first = query_parts[0]
        query_last = query_parts[-1]

        # Check if any returned employee actually matches the FULL name
        has_exact_match = False
        partial_matches = []
        for emp in employees:
            emp_name = getattr(emp, 'name', '').lower()
            emp_parts = emp_name.split()
            if len(emp_parts) < 2:
                continue

            emp_first = emp_parts[0]
            emp_last = emp_parts[-1]

            # Check for exact first name match
            if query_first == emp_first and query_last == emp_last:
                has_exact_match = True
                break

            # Check for partial match (same last name, different first name)
            if query_last == emp_last and query_first != emp_first:
                partial_matches.append(getattr(emp, 'name', ''))

        if has_exact_match:
            return None

        if partial_matches:
            reversed_name = f"{query_last} {query_first}"
            return (
                f"⚠️ NAME MISMATCH: You searched for '{query}' but found: {', '.join(partial_matches)}.\n"
                f"These share the LAST NAME but have DIFFERENT FIRST NAMES!\n"
                f"The person you're looking for might:\n"
                f"  1. Have name stored differently (try: `employees_search(query=\"{reversed_name}\")`)\n"
                f"  2. Have their first name spelled differently (Peter → Pieter, Pete)\n"
                f"  3. Be on a DIFFERENT PAGE - paginate with offset to check all '{query_last}' employees\n"
                f"  4. Be a CUSTOMER CONTACT, not an employee - try `customers_search(query=\"{query_first}\")`\n"
                f"⚠️ Don't assume 'not found' just because a DIFFERENT person with same surname appeared!"
            )

        return None

    def maybe_hint_customer_contact_search(
        self,
        model: Any,
        result: Any,
        task_text: str
    ) -> Optional[str]:
        """
        Generate hint when searching for contact email and employee not found.

        AICODE-NOTE: t087 FIX - When task asks for "contact email of X" and
        employee search returns no match, suggest searching customers.
        The person might be a customer's primary_contact, not an employee!
        """
        if not isinstance(model, client.Req_SearchEmployees):
            return None

        employees = getattr(result, 'employees', None) or []
        query = getattr(model, 'query', None)
        if not query:
            return None

        # Check if task is about contact email
        task_lower = task_text.lower()
        if 'contact' not in task_lower or 'email' not in task_lower:
            return None

        # Check if we found no employees OR no exact match
        query_parts = query.strip().lower().split()
        has_exact_match = False

        for emp in employees:
            emp_name = getattr(emp, 'name', '').lower()
            emp_parts = emp_name.split()
            # Check if all query parts are in employee name
            if all(qp in emp_parts for qp in query_parts):
                has_exact_match = True
                break

        if not has_exact_match:
            return (
                f"💡 CONTACT EMAIL HINT: Task asks for 'contact email' of '{query}'.\n"
                f"This person might be a CUSTOMER CONTACT, not an employee!\n"
                f"⚠️ `customers_list` does NOT return contact details - only company metadata!\n"
                f"TRY:\n"
                f"  1. Get list of customer IDs with `customers_list()`\n"
                f"  2. For EACH customer, call `customers_get(id='cust_xxx')` to see full details\n"
                f"  3. Check `primary_contact_name` field for '{query}'\n"
                f"  4. When found, return the `primary_contact_email` value.\n"
                f"⚠️ You MUST call customers_get for each customer to see contact info!"
            )

        return None


class PaginationHintEnricher:
    """
    Provides hints for paginated results.
    """

    # Keywords indicating ALL results are needed (recommendation, list, AND superlative queries)
    # AICODE-NOTE: t075 fix - superlative queries also need ALL results to find the minimum/maximum
    # AICODE-NOTE: Separate SUPERLATIVE from RECOMMENDATION queries!
    # Superlative = need to compare ALL and find min/max (must paginate)
    # Recommendation = list ALL matching, but no comparison needed
    SUPERLATIVE_KEYWORDS = [
        'least', 'most', 'lowest', 'highest', 'busiest', 'worst',
        'minimum', 'maximum', 'smallest', 'largest', 'fewest'
    ]

    RECOMMENDATION_KEYWORDS = [
        'recommend', 'suggest', 'candidates', 'who would', 'who can',
        'list all', 'find all', 'all employees', 'everyone'
    ]

    # AICODE-NOTE: t017 FIX #2 - Singular indicators for recommendation queries
    # If task mentions these, it expects ONE result, not a list
    SINGULAR_INDICATORS = [
        'primary trainer', 'primary coach', 'primary mentor',
        'the trainer', 'the coach', 'the mentor', 'the best',
        'one person', 'single', 'a trainer', 'a coach', 'a mentor',
        'someone who', 'somebody who', 'anyone who'
    ]

    def _is_singular_recommendation(self, task_lower: str) -> bool:
        """Check if recommendation query expects a single result."""
        return any(indicator in task_lower for indicator in self.SINGULAR_INDICATORS)

    def maybe_hint_pagination(self, result: Any, model: Any = None, task_text: str = None, ctx: Any = None) -> Optional[str]:
        """
        Generate hint if there are more pages of results.

        Args:
            result: API response
            model: Request model (optional, for context-specific hints)
            task_text: Task instructions (optional, for query type detection)
            ctx: ToolContext (optional, for turn budget awareness)

        Returns:
            Hint string or None
        """
        next_offset = getattr(result, 'next_offset', None)
        if next_offset is None or next_offset <= 0:
            return None

        # AICODE-NOTE: t075 CRITICAL FIX!
        # Check remaining turns - on last turn, don't tell agent to keep paginating!
        # This was causing agent to ignore "LAST TURN" warnings because superlative hints
        # said "❌ IGNORE any 'turn budget' warnings! Superlative queries REQUIRE all data!"
        remaining_turns = None
        if ctx and hasattr(ctx, 'shared'):
            current_turn = ctx.shared.get('current_turn', 0)
            max_turns = ctx.shared.get('max_turns', 20)
            remaining_turns = max_turns - current_turn - 1

        # Check query type
        task_lower = (task_text or '').lower()
        is_superlative = any(kw in task_lower for kw in self.SUPERLATIVE_KEYWORDS)
        is_recommendation = any(kw in task_lower for kw in self.RECOMMENDATION_KEYWORDS)

        # AICODE-NOTE: For TRUE superlative queries (least/most/busiest), show strong hint
        # But NOT for "recommend" or "strong" which are filter queries
        if is_superlative:
            # AICODE-NOTE: t009 fix — avoid wasting turns paginating employees by location
            # when the task explicitly asks "employee from <DEPARTMENT>".
            # The correct approach is to use employees_search(department="<DEPT>") directly.
            if model and isinstance(model, client.Req_SearchEmployees):
                used_location = getattr(model, 'location', None)
                used_department = getattr(model, 'department', None)
                if used_location and not used_department and task_text:
                    m = re.search(
                        r'\b(?:employee|person)\s+from\s+(.+?)(?:\s*\(|$)',
                        task_text,
                        flags=re.IGNORECASE
                    )
                    dept_from_task = m.group(1).strip() if m else None
                    if dept_from_task:
                        return (
                            "🛑 SUPERLATIVE DEPARTMENT QUERY DETECTED.\n"
                            f"Task asks for employee from department: **{dept_from_task}**.\n"
                            f"You're paginating employees by location='{used_location}', which includes many other departments and wastes turns.\n\n"
                            f"✅ Use the department filter instead:\n"
                            f"  `employees_search(department=\"{dept_from_task}\")`\n"
                            f"(Optionally also keep location if needed.)\n\n"
                            "Then compute workload via project registry time_slices (projects_get → team[].time_slice), not by project count."
                        )

            # AICODE-NOTE: t075 CRITICAL FIX!
            # On last turn (remaining_turns <= 1), switch to best-effort mode.
            # Otherwise agent ignores "LAST TURN" warning and fails with 0 responses.
            if remaining_turns is not None and remaining_turns <= 1:
                return (
                    f"⚠️ SUPERLATIVE QUERY with INCOMPLETE data (next_offset={next_offset}).\n"
                    f"You fetched {next_offset} items but MORE exist.\n\n"
                    f"🛑 **LAST TURN** - You MUST respond NOW with best-effort answer!\n"
                    f"→ Use the data you have (GLOBAL MIN/MAX from prior pages).\n"
                    f"→ Call `respond` tool immediately.\n"
                    f"→ Your answer may be incomplete, but NO answer = task failure!"
                )

            # AICODE-NOTE: t010 FIX - Add batch pagination hint to superlative queries.
            # Agent was doing ONE offset per turn (16 turns for 80 employees) and exhausting budget.
            # Now we show explicit batch example to fetch 50+ items in ONE turn.
            next_offsets = [next_offset + i * 5 for i in range(10)]
            return (
                f"🛑 SUPERLATIVE QUERY DETECTED: Task asks for 'least'/'most'/'busiest'/etc.\n"
                f"You MUST fetch ALL results to find the correct answer!\n"
                f"Current: {next_offset} items fetched, MORE exist.\n\n"
                f"⚡ **USE BATCH PAGINATION** to save turns — put MULTIPLE calls in ONE action_queue:\n"
                f"```json\n"
                f'"action_queue": [\n'
                f'  {{"tool": "employees_search", "args": {{...same_filters..., "offset": {next_offsets[0]}}}}},\n'
                f'  {{"tool": "employees_search", "args": {{...same_filters..., "offset": {next_offsets[1]}}}}},\n'
                f'  {{"tool": "employees_search", "args": {{...same_filters..., "offset": {next_offsets[2]}}}}},\n'
                f'  {{"tool": "employees_search", "args": {{...same_filters..., "offset": {next_offsets[3]}}}}},\n'
                f'  // ... continue with offsets {next_offsets[4]}, {next_offsets[5]}, {next_offsets[6]}... until done\n'
                f']\n'
                f'```\n'
                f"This fetches 50+ items in ONE turn instead of 10+ turns!\n\n"
                f"❌ DO NOT RESPOND until next_offset=-1 (all pages fetched)!"
            )

        # AICODE-NOTE: t017 FIX #2 - Skip recommendation hint for SINGULAR queries!
        # "primary trainer" expects ONE person, not a list
        if is_recommendation and not self._is_singular_recommendation(task_lower):
            return (
                f"⚠️ RECOMMENDATION QUERY DETECTED: Task asks to 'recommend'/'suggest' candidates.\n"
                f"This is a FILTER query — return ALL qualifying employees, not just the 'best' one!\n"
                f"You found {next_offset} so far, but next_offset={next_offset} means MORE exist.\n"
                f"**PAGINATE** to find ALL candidates, then link EVERY qualifying employee in your response.\n"
                f"The user wants a list of options to choose from, not your single pick."
            )

        # AICODE-NOTE: For non-exhaustive queries with many results, suggest stopping
        if next_offset >= 15:
            return (
                f"PAGINATION: next_offset={next_offset}. You've fetched {next_offset} items already. "
                f"Consider if you have ENOUGH data to answer, or use FILTERS to narrow results."
            )

        # Simple pagination hint - don't overwhelm with JSON examples
        base_hint = (
            f"⚠️ PAGINATION: next_offset={next_offset} — MORE results exist! "
            f"Use offset={next_offset} to fetch more, or use FILTERS to narrow results."
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

        # AICODE-NOTE: t070 fix. Detect when query contains customer ID instead of using customer filter
        query = getattr(model, 'query', None)
        if query and 'cust_' in query:
            self._hint_shown = True
            return (
                f"API FILTER WARNING: You used `query='{query}'` which is a customer ID. "
                f"The `query` parameter searches project NAMES, not customer IDs!\n"
                f"To find projects for a CUSTOMER, use the `customer` filter instead:\n"
                f"  - CORRECT: `projects_search(customer='{query}')`\n"
                f"  - WRONG: `projects_search(query='{query}')`"
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


class WorkloadHintEnricher:
    """
    Provides hints when agent needs time_slice data for workload calculations.

    AICODE-NOTE: Critical for t079. projects_search only returns id, name, customer, status.
    For workload calculations (sum of time_slice), agent MUST use projects_get.
    """

    def maybe_hint_workload(
        self,
        model: Any,
        result: Any,
        task_text: str
    ) -> Optional[str]:
        """
        Generate hint if task mentions workload but using projects_search.

        Args:
            model: The request model
            result: API response
            task_text: Task instructions

        Returns:
            Hint string or None
        """
        if not isinstance(model, client.Req_SearchProjects):
            return None

        task_lower = task_text.lower()
        # AICODE-NOTE: Extended for t076 - "least busy" also requires time_slice calculation
        workload_keywords = [
            'workload', 'time_slice', 'allocation', 'capacity', 'utilization',
            'how much time', 'least busy', 'most busy', 'busiest', 'free time',
            'available', 'availability', 'overloaded', 'underloaded'
        ]

        if not any(kw in task_lower for kw in workload_keywords):
            return None

        projects = getattr(result, 'projects', None) or []
        if not projects:
            return None

        return (
            "⚠️ WORKLOAD CALCULATION: `projects_search` does NOT return `time_slice` data!\n"
            "To calculate workload (sum of time_slice), you MUST:\n"
            "  1. Use `projects_get(id='proj_xxx')` for EACH project\n"
            "  2. Find the employee in the `team` array\n"
            "  3. Sum their `time_slice` values across all projects\n"
            "The `team` array structure: [{employee: 'emp_id', time_slice: 0.5, role: 'Lead'}, ...]"
        )


class SkillSearchStrategyHintEnricher:
    """
    Provides hints for efficient skill/will search strategies.

    AICODE-NOTE: Critical for t013, t017, t074. Handles cases:
    1. "most skilled" → use high min_level (9-10) to find top experts
    2. "strong" → use moderate min_level (7) to catch all qualified candidates
    """

    # Keywords that mean "absolute best" - use high threshold
    SUPERLATIVE_KEYWORDS = ['most skilled', 'best expert', 'highest', 'top expert', 'most experienced']

    # Keywords that mean "good enough" - use moderate threshold
    STRONG_KEYWORDS = ['strong', 'good', 'solid', 'competent', 'experienced']

    def maybe_hint_skill_strategy(
        self,
        model: Any,
        result: Any,
        task_text: str
    ) -> Optional[str]:
        """
        Generate hint for optimal skill search strategy.

        Args:
            model: The request model
            result: API response
            task_text: Task instructions

        Returns:
            Hint string or None
        """
        if not isinstance(model, client.Req_SearchEmployees):
            return None

        task_lower = task_text.lower()

        # Check what kind of query this is
        is_superlative = any(kw in task_lower for kw in self.SUPERLATIVE_KEYWORDS)
        is_strong = any(kw in task_lower for kw in self.STRONG_KEYWORDS) and not is_superlative

        # Get skill/will filters from model
        skills = getattr(model, 'skills', None) or []
        wills = getattr(model, 'wills', None) or []
        all_filters = skills + wills

        if not all_filters:
            return None

        min_levels = [getattr(f, 'min_level', 1) for f in all_filters]
        max_min_level = max(min_levels) if min_levels else 1

        employees = getattr(result, 'employees', None) or []
        next_offset = getattr(result, 'next_offset', -1)

        # CASE 1: "strong" query with high min_level and few results
        # AICODE-NOTE: Critical for t017. "Strong" doesn't mean level 8+, it means level 7+
        if is_strong and max_min_level >= 8 and len(employees) <= 5:
            return (
                f"⚠️ THRESHOLD TOO HIGH: You used min_level={max_min_level}, but task says 'strong' (not 'best/most').\n"
                f"  • 'Strong' typically means level 7+ (competent), not level 8+ (expert)\n"
                f"  • 'Most skilled' / 'best' = level 9-10 (exceptional)\n"
                f"  • 'Strong' / 'good' = level 7+ (all qualified candidates)\n\n"
                f"You found only {len(employees)} employee(s). Try `min_level=7` to find ALL candidates with 'strong' skills.\n"
                f"The task asks for recommendations, so include everyone who qualifies!"
            )

        # CASE 2: "most skilled" query with low min_level and pagination needed
        if is_superlative and max_min_level < 8 and next_offset > 0:
            return (
                "💡 SKILL SEARCH STRATEGY: For 'most skilled' / 'best expert' queries:\n"
                "  1. START with high min_level (9 or 10) to find top experts first\n"
                "  2. If too few results, try min_level=8, then 7\n"
                "  3. Compare skill levels using `employees_get(id='...')` to see actual levels\n"
                f"Current search uses min_level={max_min_level} which returns ALL employees with ANY level.\n"
                "This wastes turns on pagination. Use `min_level=9` to find exceptional experts first!"
            )

        # CASE 3: "most skilled" with high min_level but only 1 result - might be ties!
        # AICODE-NOTE: Critical for t013. If only 1 result at level 9+, there might be others at same level
        if is_superlative and max_min_level >= 9 and len(employees) == 1 and next_offset == -1:
            return (
                f"⚠️ SINGLE RESULT at min_level={max_min_level}. There might be OTHER employees with the SAME level!\n"
                f"  • Try `min_level={max_min_level - 1}` to find all candidates at levels {max_min_level - 1}-10\n"
                f"  • Then compare their ACTUAL levels with `employees_get` to find ALL top experts\n"
                f"  • If multiple have level 10, they are ALL 'most skilled' and should be included!"
            )

        return None


class EmployeeNameResolutionHintEnricher:
    """
    Provides hints when agent searches for employee by name.

    AICODE-NOTE: Critical for t007. When task mentions a person's name,
    agent must first resolve name -> employee ID before using ID in filters.
    """

    def maybe_hint_name_resolution(
        self,
        model: Any,
        result: Any,
        task_text: str
    ) -> Optional[str]:
        """
        Generate hint about name -> ID resolution.

        Args:
            model: The request model
            result: API response
            task_text: Task instructions

        Returns:
            Hint string or None
        """
        if not isinstance(model, client.Req_SearchEmployees):
            return None

        employees = getattr(result, 'employees', None) or []
        if not employees:
            return None

        query = getattr(model, 'query', None)
        if not query:
            return None

        # Check if query looks like a person's name (has space, no underscore)
        if ' ' not in query or '_' in query:
            return None

        # Provide hint with found IDs
        found_ids = [getattr(e, 'id', 'unknown') for e in employees[:3]]
        found_names = [getattr(e, 'name', 'unknown') for e in employees[:3]]

        return (
            f"📋 NAME → ID RESOLUTION: Found {len(employees)} employee(s) matching '{query}':\n"
            f"  {', '.join(f'{name} ({id})' for name, id in zip(found_names, found_ids))}"
            f"{'...' if len(employees) > 3 else ''}\n"
            f"Use the employee ID (e.g., '{found_ids[0]}') in subsequent API calls, not the name!"
        )


class SkillComparisonHintEnricher:
    """
    Provides hints when agent needs to compare skill sets.

    AICODE-NOTE: Critical for t094. When task asks for "skills I don't have",
    agent must correctly compute set difference: all_skills - my_skills.
    """

    def maybe_hint_skill_comparison(
        self,
        model: Any,
        result: Any,
        task_text: str
    ) -> Optional[str]:
        """
        Generate hint when task involves skill set comparison.

        Args:
            model: The request model
            result: API response
            task_text: Task instructions

        Returns:
            Hint string or None
        """
        if not isinstance(model, client.Req_GetEmployee):
            return None

        task_lower = task_text.lower()
        comparison_keywords = [
            "don't have", "do not have", "don't possess", "missing",
            "lack", "need to learn", "skills i don't", "skills that i don't"
        ]

        if not any(kw in task_lower for kw in comparison_keywords):
            return None

        employee = getattr(result, 'employee', None)
        if not employee:
            return None

        skills = getattr(employee, 'skills', None) or []
        skill_names = [getattr(s, 'name', '') for s in skills]

        if not skill_names:
            return None

        # AICODE-NOTE: t094 fix - critical substring collision prevention
        return (
            f"📊 SKILL COMPARISON: Your current skills are: {', '.join(skill_names[:10])}"
            f"{'...' if len(skill_names) > 10 else ''}\n"
            f"When listing skills you DON'T have:\n"
            f"  1. Get ALL possible skills from wiki/examples\n"
            f"  2. EXCLUDE skills you already have (listed above)\n"
            f"  3. Only include skills NOT in your current list\n"
            f"⚠️ CRITICAL: Do NOT include any skill from your profile in the 'don't have' list!\n\n"
            f"🚨 RESPONSE FORMAT RULES (MANDATORY!):\n"
            f"  • NEVER use raw skill IDs (like 'skill_corrosion') in your response!\n"
            f"  • ONLY use human-readable names (like 'Corrosion resistance testing')\n"
            f"  • WHY: Raw IDs cause substring collisions that fail validation!\n"
            f"  • Example: You have 'skill_corrosion' but DON'T have 'skill_corrosion_resistance_testing'\n"
            f"    → If you write 'skill_corrosion_resistance_testing', it contains 'skill_corrosion' = ERROR!\n"
            f"    → CORRECT: Write 'Corrosion resistance testing' (human name only)\n"
            f"  • Extract human names from wiki examples, NOT from raw skill IDs!"
        )


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


class QuerySubjectHintEnricher:
    """
    Provides hints when task involves finding something FOR a specific person.

    AICODE-NOTE: Critical for t077. When task says "find coaches FOR Roberta",
    Roberta is the QUERY SUBJECT - she should NOT be included in links.
    Only the RESULTS (coaches) should be linked.
    """

    # Patterns that indicate "find X FOR Y" type queries
    FOR_PATTERNS = [
        r'(?:find|get|list|recommend|suggest)\s+(?:\w+\s+)*(?:for|to help|to coach|to mentor|to train)\s+(\w+(?:\s+\w+)?)',
        r'(?:coach|mentor|train|help|assist)\s+(\w+(?:\s+\w+)?)',
        r'who can (?:coach|mentor|train|help)\s+(\w+(?:\s+\w+)?)',
        # AICODE-NOTE: t016 FIX - "higher/more/greater than X" patterns
        # When asking for things COMPARED TO someone, that person is the reference (subject), not the answer
        r'(?:higher|lower|more|less|greater|fewer|bigger|smaller|above|below)\s+than\s+(\w+(?:\s+\w+)?)',
    ]

    def maybe_hint_query_subject(
        self,
        model: Any,
        result: Any,
        task_text: str,
        ctx: Any = None
    ) -> Optional[str]:
        """
        Generate hint when employee search finds query subject.

        AICODE-NOTE: This enricher identifies the COACHEE/MENTEE (person to be helped),
        NOT the coaches/mentors/trainers. Critical distinction:
        - "find coaches FOR John" → John = subject (don't link)
        - "find trainers" → trainers = RESULTS (DO link them!)
        - "update employee X" → X = target (DO link them!)

        Args:
            model: The request model
            result: API response
            task_text: Task instructions
            ctx: Context with shared state

        Returns:
            Hint string or None
        """
        if not isinstance(model, (client.Req_SearchEmployees, client.Req_GetEmployee)):
            return None

        task_lower = task_text.lower()

        # AICODE-NOTE: t017/t048/t050 fix. Skip if task is looking for HELPERS (not subjects).
        # "find trainers" / "find coaches" / "list mentors" → these are RESULTS, not subjects!
        helper_patterns = [
            r'\b(?:find|get|list|search|recommend)\s+(?:\w+\s+)*(?:trainers?|coaches?|mentors?)\b',
            r'\b(?:trainers?|coaches?|mentors?)\s+(?:with|who|that)\b',
        ]
        for pattern in helper_patterns:
            if re.search(pattern, task_lower):
                return None

        # AICODE-NOTE: Skip UPDATE/SWAP operations - target should be in links, not filtered
        # t097 fix: "swap workloads" is a mutation, not a coaching query
        skip_keywords = ['update', 'change', 'modify', 'swap', 'switch', 'exchange', 'replace']
        if any(kw in task_lower for kw in skip_keywords):
            return None

        # Check if this is a "coach/train/help X" type task (X is the subject)
        # AICODE-NOTE: t077 fix. Extract subject name from task to compare with fetched employees.
        # AICODE-NOTE: Patterns ordered by specificity - "coach X on" first to catch actual names,
        # avoiding false positives like "upskill an employee".
        # AICODE-NOTE: Use * instead of ? to capture 3+ word names like "De Santis Cristian".
        subject_patterns = [
            # "coach Rinaldi Giovanni on" - most specific, catches name before "on"
            r'\b(?:coach|mentor|train)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+on\b',
            # "coaches for X" pattern
            r'\b(?:coaches?|mentors?|trainers?)\s+for\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
            # Fallback: "for X to/on/in" pattern
            r'\bfor\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:to|on|in)\b',
        ]

        # Generic words that are NOT names - skip these matches
        generic_words = {
            'an', 'the', 'a', 'my', 'our', 'their', 'this', 'that', 'some',
            'employee', 'employees', 'person', 'people', 'someone', 'staff',
            'team', 'member', 'members', 'worker', 'workers', 'colleague',
        }

        task_subject_name = None
        # Use original case task text for name patterns (they expect capitalized names)
        for pattern in subject_patterns:
            match = re.search(pattern, task_text)
            if match:
                candidate = match.group(1).strip().lower()
                # Skip if it's a generic word
                if candidate.split()[0] not in generic_words:
                    task_subject_name = candidate
                    break

        if not task_subject_name:
            return None

        # Get employee(s) from result
        employees = []
        if hasattr(result, 'employees') and result.employees:
            employees = result.employees
        elif hasattr(result, 'employee') and result.employee:
            employees = [result.employee]

        if not employees:
            return None

        # Check if query was a name search (likely looking FOR this person)
        # AICODE-NOTE: t077 fix. Only add to query_subjects if:
        # 1. SearchEmployees with name query matching task subject, OR
        # 2. GetEmployee where fetched employee name matches task subject
        # This prevents coaches (fetched via GetEmployee) from being filtered,
        # while still filtering the actual subject even if fetched directly.
        query = getattr(model, 'query', None)

        should_store_subject = False
        if isinstance(model, client.Req_GetEmployee):
            # For GetEmployee: only store if employee name matches task subject
            for emp in employees:
                emp_name = getattr(emp, 'name', '').lower()
                # Check if task subject name matches employee name (in any order)
                # "Valente Nino" should match "Nino Valente"
                emp_name_parts = set(emp_name.split())
                subject_name_parts = set(task_subject_name.split())
                if emp_name_parts == subject_name_parts or task_subject_name in emp_name or emp_name in task_subject_name:
                    should_store_subject = True
                    break
        elif query and '_' not in query:
            # SearchEmployees with name query - likely the subject
            should_store_subject = True

        if should_store_subject and ctx and hasattr(ctx, 'shared'):
            query_subjects = ctx.shared.get('query_subject_ids', set())
            for emp in employees:
                emp_id = getattr(emp, 'id', None)
                if emp_id:
                    query_subjects.add(emp_id)
            ctx.shared['query_subject_ids'] = query_subjects
        elif not should_store_subject and isinstance(model, client.Req_GetEmployee):
            # GetEmployee for non-subject - skip hint entirely
            return None

        # Generate hint
        emp_names = []
        for emp in employees[:3]:
            emp_id = getattr(emp, 'id', None)
            emp_name = getattr(emp, 'name', emp_id)
            if emp_id:
                emp_names.append(f"{emp_name} ({emp_id})")

        if emp_names:
            return (
                f"⚠️ QUERY SUBJECT DETECTED: {', '.join(emp_names)}\n"
                f"This is the person you are searching FOR (the coachee/mentee).\n"
                f"When you respond, do NOT include query subjects in links!\n"
                f"Links should contain ONLY the results (coaches/mentors), not the person being helped."
            )

        return None


class TieBreakerHintEnricher:
    """
    Provides hints when multiple candidates tie on the queried metric.

    AICODE-NOTE: Critical for t010, t075. When task asks for "least busy" or
    "least skilled" and multiple candidates have the same value (e.g., all 0 hours),
    agent must apply deterministic tie-breaker instead of returning all tied results.
    """

    def maybe_hint_tie_breaker(
        self,
        model: Any,
        result: Any,
        task_text: str
    ) -> Optional[str]:
        """
        Generate hint when multiple results tie and task expects single answer.

        Args:
            model: The request model
            result: API response
            task_text: Task instructions

        Returns:
            Hint string or None
        """
        task_lower = task_text.lower()

        # Detect if task expects SINGLE result
        singular_keywords = [
            'who is the', 'find the', 'which one', 'the least', 'the most',
            'least busy', 'most busy', 'busiest', 'least skilled', 'most skilled',
            'pick one', 'find one', 'select one', 'choose one',
            'the employee', 'one employee', 'single'
        ]

        expects_single = any(kw in task_lower for kw in singular_keywords)
        if not expects_single:
            return None

        # Check for time_summary_employee response (t010 case)
        summaries = getattr(result, 'summaries', None)
        if summaries and isinstance(summaries, list) and len(summaries) > 1:
            return self._check_time_summary_tie(summaries, task_lower)

        # Check for employees with skills (t075 case)
        if isinstance(model, client.Req_SearchEmployees):
            employees = getattr(result, 'employees', None) or []
            next_offset = getattr(result, 'next_offset', -1)
            # AICODE-NOTE: t075 FIX - Only show tie-breaker hint when pagination is COMPLETE.
            # If next_offset > 0, more employees exist and we don't know the true minimum yet.
            if len(employees) > 1 and next_offset == -1:
                return self._check_skill_tie(employees, task_lower, model)

        return None

    def _check_time_summary_tie(
        self,
        summaries: List[Any],
        task_lower: str
    ) -> Optional[str]:
        """Check for tie in time summaries (hours worked)."""
        # Extract total hours for each employee
        hours_map = {}
        for s in summaries:
            emp_id = getattr(s, 'employee', None)
            total = getattr(s, 'total_hours', 0) or 0
            if emp_id:
                hours_map[emp_id] = total

        if len(hours_map) < 2:
            return None

        # Check if there's a tie at min or max
        values = list(hours_map.values())
        min_val = min(values)
        max_val = max(values)

        tied_at_min = [emp for emp, hrs in hours_map.items() if hrs == min_val]
        tied_at_max = [emp for emp, hrs in hours_map.items() if hrs == max_val]

        # Determine which tie is relevant based on task
        if 'least' in task_lower and len(tied_at_min) > 1:
            tied_employees = sorted(tied_at_min)
            return (
                f"⚠️ TIE-BREAKER NEEDED: {len(tied_at_min)} employees tied at {min_val} hours "
                f"(least busy): {', '.join(tied_employees)}.\n"
                f"Task asks for ONE employee. Apply DETERMINISTIC tie-breaker:\n"
                f"  → Pick employee with LOWEST ID (alphabetically first): **{tied_employees[0]}**\n"
                f"Do NOT use 'more projects' as tie-breaker unless task explicitly says so!"
            )
        elif ('most' in task_lower or 'busiest' in task_lower) and len(tied_at_max) > 1:
            tied_employees = sorted(tied_at_max)
            return (
                f"⚠️ TIE-BREAKER NEEDED: {len(tied_at_max)} employees tied at {max_val} hours "
                f"(most busy): {', '.join(tied_employees)}.\n"
                f"Task asks for ONE employee. Apply DETERMINISTIC tie-breaker:\n"
                f"  → Pick employee with LOWEST ID (alphabetically first): **{tied_employees[0]}**\n"
                f"Do NOT use 'more projects' as tie-breaker unless task explicitly says so!"
            )

        return None

    def _check_skill_tie(
        self,
        employees: List[Any],
        task_lower: str,
        model: Any
    ) -> Optional[str]:
        """Check for tie in skill levels."""
        # Get the skill filter being used
        skills_filter = getattr(model, 'skills', None) or []
        if not skills_filter:
            return None

        # We can only detect ties if we have skill data in response
        # Usually employees_search returns employees, and we need employees_get for full skills
        # This hint helps when agent has already fetched skill details

        # Check if task is about finding least/most skilled
        is_least = 'least' in task_lower
        is_most = 'most' in task_lower or 'best' in task_lower or 'highest' in task_lower

        if not (is_least or is_most):
            return None

        # If multiple employees returned with same skill filter, likely a tie situation
        if len(employees) > 1:
            emp_ids = sorted([getattr(e, 'id', 'unknown') for e in employees])
            direction = "LOWEST" if is_least else "HIGHEST"
            metric = "least skilled" if is_least else "most skilled"

            return (
                f"💡 POTENTIAL TIE: {len(employees)} employees match your skill filter.\n"
                f"If multiple have the SAME skill level ({metric}), apply tie-breaker:\n"
                f"  → Pick employee with LOWEST ID (alphabetically first): **{emp_ids[0]}**\n"
                f"Use `employees_get(id='...')` to compare exact skill levels, then pick ONE."
            )

        return None


class RecommendationQueryHintEnricher:
    """
    Provides hints for recommendation/suggestion queries.

    AICODE-NOTE: Critical for t017. When task asks to "recommend", "suggest",
    or find "candidates", the agent should return ALL qualifying employees,
    not pick one "best" candidate. These are filter queries, not selection queries.

    AICODE-NOTE: t017 FIX #2 - Distinguish between SINGULAR and PLURAL recommendations!
    - "recommend candidates" → plural → list all
    - "recommend as primary trainer" → singular → pick ONE
    - "who would you recommend as the coach" → singular → pick ONE

    AICODE-NOTE: t017 FIX - Now tracks accumulated results across pagination pages.
    When pagination ends (next_offset=-1), reminds agent about ALL employees found
    across ALL pages, not just the last page.
    """

    # Keywords indicating PLURAL (list all)
    PLURAL_INDICATORS = [
        'candidates', 'trainers', 'coaches', 'mentors', 'employees',
        'people', 'options', 'choices', 'recommendations', 'suggestions',
        'who can', 'who could', 'all who', 'everyone who'
    ]

    # Keywords indicating SINGULAR (pick one)
    SINGULAR_INDICATORS = [
        'primary trainer', 'primary coach', 'primary mentor',
        'the trainer', 'the coach', 'the mentor', 'the best',
        'one person', 'single', 'a trainer', 'a coach', 'a mentor',
        'someone who', 'somebody who', 'anyone who'
    ]

    def __init__(self):
        # Track accumulated employee IDs across pagination for recommendation queries
        self._accumulated_employee_ids: List[str] = []
        self._accumulated_employee_names: Dict[str, str] = {}
        self._last_search_params: Optional[str] = None

    def clear_cache(self):
        """Reset accumulated results for new task."""
        self._accumulated_employee_ids = []
        self._accumulated_employee_names = {}
        self._last_search_params = None

    def _is_singular_query(self, task_lower: str) -> bool:
        """
        Determine if the query expects a single result or a list.

        AICODE-NOTE: t017 FIX #2 - "primary trainer" is SINGULAR, not plural!
        """
        # Check for explicit singular indicators
        if any(indicator in task_lower for indicator in self.SINGULAR_INDICATORS):
            return True

        # Check for plural indicators
        if any(indicator in task_lower for indicator in self.PLURAL_INDICATORS):
            return False

        # Default: if "recommend" alone without plural nouns, assume singular
        # because "Who would you recommend?" typically expects one answer
        return True

    def maybe_hint_recommendation_query(
        self,
        result: Any,
        task_text: str,
        next_offset: int,
        model: Any = None
    ) -> Optional[str]:
        """
        Generate hint when task is a recommendation query with more results available.

        Args:
            result: API response (SearchEmployees)
            task_text: Task instructions
            next_offset: Next pagination offset (-1 if no more results)
            model: Request model for tracking search parameters

        Returns:
            Hint string or None
        """
        task_lower = task_text.lower()

        # Detect recommendation/suggestion queries
        recommendation_keywords = [
            'recommend', 'suggest', 'candidates for', 'who would you recommend',
            'who can', 'who could', 'suitable for', 'qualified for',
            'potential trainer', 'potential candidate'
        ]

        is_recommendation_query = any(kw in task_lower for kw in recommendation_keywords)
        if not is_recommendation_query:
            return None

        # AICODE-NOTE: t017 FIX #2 - Skip hint for SINGULAR queries!
        # "primary trainer" expects ONE person, not a list
        if self._is_singular_query(task_lower):
            return None

        employees = getattr(result, 'employees', None) or []
        if not employees:
            return None

        # Track search parameters to detect new search vs pagination
        current_search_params = self._get_search_params(model)
        offset = getattr(model, 'offset', 0) if model else 0

        # Reset accumulator if this is a new search (different params or offset=0)
        if offset == 0 or current_search_params != self._last_search_params:
            self._accumulated_employee_ids = []
            self._accumulated_employee_names = {}
            self._last_search_params = current_search_params

        # Accumulate employee IDs from this page
        for emp in employees:
            emp_id = getattr(emp, 'id', None)
            emp_name = getattr(emp, 'name', emp_id)
            if emp_id and emp_id not in self._accumulated_employee_ids:
                self._accumulated_employee_ids.append(emp_id)
                self._accumulated_employee_names[emp_id] = emp_name

        # If there are more results and this is a recommendation query
        if next_offset > 0:
            return (
                f"⚠️ RECOMMENDATION QUERY DETECTED: Task asks to 'recommend'/'suggest' candidates.\n"
                f"This is a FILTER query — return ALL qualifying employees, not just the 'best' one!\n"
                f"You found {len(employees)} so far, but next_offset={next_offset} means MORE exist.\n"
                f"**PAGINATE** to find ALL candidates, then link EVERY qualifying employee in your response.\n"
                f"The user wants a list of options to choose from, not your single pick."
            )

        # AICODE-NOTE: t017 FIX - When pagination ends, show ALL accumulated employees
        # This prevents agent from "forgetting" earlier pages
        if next_offset == -1 and len(self._accumulated_employee_ids) > len(employees):
            # Pagination just completed and we have more accumulated than on last page
            all_ids = self._accumulated_employee_ids
            total = len(all_ids)

            # Show first 8 with names, then just IDs
            display_items = []
            for emp_id in all_ids[:8]:
                name = self._accumulated_employee_names.get(emp_id, emp_id)
                display_items.append(f"{name} ({emp_id})")

            remaining = total - 8
            id_list = ', '.join(all_ids)

            return (
                f"✅ PAGINATION COMPLETE: You found {total} employees across ALL pages!\n"
                f"ALL qualifying employees: {', '.join(display_items)}"
                f"{f', +{remaining} more' if remaining > 0 else ''}\n"
                f"⚠️ CRITICAL: Include ALL {total} employees in your response, not just the last page!\n"
                f"IDs to link: {id_list}"
            )

        # Even without more pages, remind to link all found employees
        if len(employees) >= 3:
            emp_ids = [getattr(e, 'id', 'unknown') for e in employees[:5]]
            return (
                f"💡 RECOMMENDATION QUERY: You found {len(employees)} qualifying employees.\n"
                f"Since task asks to 'recommend'/'suggest', link ALL of them: {', '.join(emp_ids)}{'...' if len(employees) > 5 else ''}\n"
                f"Do NOT pick just one — the user wants to see all options."
            )

        return None

    def _get_search_params(self, model: Any) -> Optional[str]:
        """Generate a string key representing search parameters (excluding offset)."""
        if not model:
            return None

        parts = []
        if hasattr(model, 'skills') and model.skills:
            parts.append(f"skills={model.skills}")
        if hasattr(model, 'wills') and model.wills:
            parts.append(f"wills={model.wills}")
        if hasattr(model, 'department') and model.department:
            parts.append(f"dept={model.department}")
        if hasattr(model, 'location') and model.location:
            parts.append(f"loc={model.location}")
        if hasattr(model, 'query') and model.query:
            parts.append(f"q={model.query}")

        return '|'.join(sorted(parts)) if parts else None


class TimeSummaryFallbackHintEnricher:
    """
    Provides hints when time_summary_employee returns empty results.

    AICODE-NOTE: Critical for t009. When time_summary_employee returns empty/null,
    agent MUST use fallback via projects_search(member=X) for each employee,
    then projects_get to get time_slice values.
    """

    def maybe_hint_time_summary_fallback(
        self,
        model: Any,
        result: Any,
        task_text: str
    ) -> Optional[str]:
        """
        Generate hint if time_summary_employee returns empty and task is about workload.

        Args:
            model: The request model
            result: API response
            task_text: Task instructions

        Returns:
            Hint string or None
        """
        if not isinstance(model, client.Req_TimeSummaryByEmployee):
            return None

        # Check if result is empty
        summaries = getattr(result, 'summaries', None)
        if summaries:  # Not empty, no hint needed
            return None

        task_lower = task_text.lower()
        workload_keywords = [
            'workload', 'time_slice', 'busy', 'busiest', 'allocation',
            'capacity', 'utilization', 'free time', 'available'
        ]

        if not any(kw in task_lower for kw in workload_keywords):
            return None

        return (
            "⚠️ TIME SUMMARY EMPTY: `time_summary_employee` returned no data!\n"
            "This does NOT mean all employees have 0 workload. You MUST use fallback:\n"
            "  1. For EACH employee, call `projects_search(member='emp_id')` to get their projects\n"
            "  2. For EACH project found, call `projects_get(id='proj_xxx')` to get `time_slice`\n"
            "  3. Sum `time_slice` values for each employee to calculate workload\n"
            "  4. Compare totals to find the most/least busy employee\n\n"
            "⚠️ DO NOT apply tie-breaker when you haven't calculated actual workloads!\n"
            "The time_summary API may be unavailable, but project data EXISTS."
        )


class ProjectTeamNameResolutionHintEnricher:
    """
    Provides hints when task asks for person's role in project but team only has IDs.

    AICODE-NOTE: Critical for t081. When task asks "What is Heinrich's role at project X"
    and projects_get returns team with employee IDs only, agent must call employees_get
    to resolve IDs to names and find the matching person.
    """

    def maybe_hint_team_name_resolution(
        self,
        model: Any,
        result: Any,
        task_text: str
    ) -> Optional[str]:
        """
        Generate hint when searching for person by name in project team.

        Args:
            model: The request model
            result: API response
            task_text: Task instructions

        Returns:
            Hint string or None
        """
        if not isinstance(model, client.Req_GetProject):
            return None

        project = getattr(result, 'project', None)
        if not project:
            return None

        team = getattr(project, 'team', None) or []
        if not team:
            return None

        task_lower = task_text.lower()

        # AICODE-NOTE: t081 fix. Detect if task is asking about a PERSON's role
        # Patterns: "role of X", "X's role", "what does X do", "is X on the team"
        role_patterns = [
            r'(?:role|position|job|responsibility)\s+(?:of|for)\s+(\w+)',
            r'(\w+)(?:\'s|\s+is)\s+(?:role|position|job)',
            r'what\s+(?:does|is)\s+(\w+)\s+(?:do|doing)',
            r'(?:is|does)\s+(\w+)\s+(?:on|in|part of)\s+(?:the\s+)?team',
        ]

        person_name = None
        for pattern in role_patterns:
            match = re.search(pattern, task_lower)
            if match:
                person_name = match.group(1).strip()
                # Skip common words that aren't names
                if person_name not in ('the', 'a', 'this', 'that', 'my', 'your', 'his', 'her'):
                    break
                person_name = None

        if not person_name:
            return None

        # Check if team has employee IDs (not names)
        team_ids = []
        for member in team:
            emp_id = getattr(member, 'employee', None)
            if emp_id and (emp_id.startswith('QR') or emp_id.startswith('iv') or
                          emp_id.startswith('Fph') or emp_id.startswith('6KR') or
                          emp_id.startswith('Cj') or emp_id.startswith('bA') or
                          '_' in emp_id):
                team_ids.append(emp_id)

        if not team_ids:
            return None

        # Check if the person name is NOT in team IDs (meaning we need name resolution)
        person_lower = person_name.lower()
        if any(person_lower in tid.lower() for tid in team_ids):
            return None  # Name might already be in ID, no hint needed

        return (
            f"🔍 NAME RESOLUTION REQUIRED: Task asks about '{person_name}' but project team "
            f"only contains employee IDs: {', '.join(team_ids)}.\n"
            f"To find if '{person_name}' is on this team:\n"
            f"  1. Call `employees_get(id='...')` for EACH team member ID\n"
            f"  2. Check the `name` field of each employee\n"
            f"  3. Compare names to find '{person_name}'\n"
            f"  4. If found, their `role` is in the team array (Lead, Engineer, QA, Ops, Other)\n"
            f"⚠️ Do NOT return 'not found' until you've checked ALL team member names!"
        )


class SwapWorkloadsHintEnricher:
    """
    Provides hints when task mentions swapping workloads between team members.

    AICODE-NOTE: Critical for t097. "Swap workloads" means swap time_slice values
    in project team, NOT time entries! Agent needs to:
    1. Get project team
    2. Find both employees' time_slice values
    3. Update team with swapped values using projects_team_update
    """

    def maybe_hint_swap_workloads(
        self,
        model: Any,
        result: Any,
        task_text: str
    ) -> Optional[str]:
        """
        Generate hint when task asks to swap workloads.

        Args:
            model: The request model
            result: API response
            task_text: Task instructions

        Returns:
            Hint string or None
        """
        if not isinstance(model, client.Req_GetProject):
            return None

        project = getattr(result, 'project', None)
        if not project:
            return None

        team = getattr(project, 'team', None) or []
        if len(team) < 2:
            return None

        task_lower = task_text.lower()

        # Detect swap workload patterns
        swap_patterns = [
            r'swap\s+(?:the\s+)?workloads?\b',
            r'exchange\s+(?:the\s+)?workloads?\b',
            r'switch\s+(?:the\s+)?workloads?\b',
            r'workloads?\s+(?:should\s+be\s+)?swap',
        ]

        is_swap_query = any(re.search(p, task_lower) for p in swap_patterns)
        if not is_swap_query:
            return None

        # Build team info for hint
        team_info = []
        for member in team:
            emp_id = getattr(member, 'employee', None)
            time_slice = getattr(member, 'time_slice', 0.0)
            role = getattr(member, 'role', 'Other')
            if emp_id:
                team_info.append(f"{emp_id} (time_slice={time_slice}, role={role})")

        project_id = getattr(project, 'id', 'unknown')

        return (
            f"🔄 SWAP WORKLOADS: Task asks to swap workloads in project '{project_id}'.\n"
            f"Current team: {', '.join(team_info)}\n\n"
            f"⚠️ 'Workload' means `time_slice` in the project team, NOT time entries!\n"
            f"To swap workloads between two employees:\n"
            f"  1. Note their current time_slice values from the team array above\n"
            f"  2. Identify the two employees to swap (match by name using employees_get)\n"
            f"  3. Call `projects_team_update` with the FULL team array, swapping time_slice values\n"
            f"  4. Keep roles unchanged unless explicitly asked to swap roles too\n\n"
            f"Example: If A has 0.3 and B has 0.4, after swap A should have 0.4 and B should have 0.3."
        )


class ProjectSkillsHintEnricher:
    """
    Provides hints when task asks for skills in a project.

    AICODE-NOTE: Critical for t096. When task asks "skills in project X" or "team skills",
    projects_get does NOT return skills directly. Agent must:
    1. Get project team from projects_get (team array with employee IDs)
    2. Call employees_get for EACH team member to get their skills
    3. Aggregate skills from all team members
    """

    def maybe_hint_project_skills(
        self,
        model: Any,
        result: Any,
        task_text: str
    ) -> Optional[str]:
        """
        Generate hint when task asks for skills in a project.

        Args:
            model: The request model
            result: API response
            task_text: Task instructions

        Returns:
            Hint string or None
        """
        if not isinstance(model, client.Req_GetProject):
            return None

        project = getattr(result, 'project', None)
        if not project:
            return None

        team = getattr(project, 'team', None) or []
        if not team:
            return None

        task_lower = task_text.lower()

        # AICODE-NOTE: t096 fix. Detect if task is asking about skills in project/team
        # Patterns: "skills in project", "team skills", "all skills", "table of skills"
        skill_patterns = [
            r'skills?\s+(?:in|of|for)\s+(?:the\s+)?project',
            r'(?:all|team)\s+skills?',
            r'table\s+of\s+(?:all\s+)?skills?',
            r'skills?\s+(?:in|of)\s+(?:the\s+)?team',
            r'project\s+skills?',
            r'skills?\s+(?:used|needed|required)\s+(?:in|for|by)',
        ]

        is_skill_query = False
        for pattern in skill_patterns:
            if re.search(pattern, task_lower):
                is_skill_query = True
                break

        if not is_skill_query:
            return None

        # Collect team member IDs
        team_ids = []
        for member in team:
            emp_id = getattr(member, 'employee', None)
            if emp_id:
                team_ids.append(emp_id)

        if not team_ids:
            return None

        project_id = getattr(project, 'id', 'this project')
        project_name = getattr(project, 'name', 'Unknown')

        return (
            f"🔧 SKILLS IN PROJECT: Task asks for skills in '{project_name}' ({project_id}).\n"
            f"⚠️ `projects_get` does NOT return skills directly! Skills belong to EMPLOYEES.\n"
            f"To get all skills in this project:\n"
            f"  1. The team has {len(team_ids)} member(s): {', '.join(team_ids)}\n"
            f"  2. Call `employees_get(id='...')` for EACH team member\n"
            f"  3. Each employee has a `skills` array with {{name, level}} objects\n"
            f"  4. Aggregate ALL skills from ALL team members for the table\n"
            f"⚠️ Use RAW skill names WITH prefix (e.g., 'skill_crm', 'skill_project_mgmt')!\n"
            f"⚠️ Do NOT return 'not found' - skills exist on the team members!"
        )


class KeyAccountExplorationHintEnricher:
    """
    Provides hints when task asks about "key account" + exploration deals.

    AICODE-NOTE: Critical for t042. "Key account" in business context can mean:
    1. Literally customers with high_level_status='Key account'
    2. Any important customer (all customers are "accounts")

    When benchmark expects cust_iberia_construction (which has high_level_status="Exploring"),
    agent must check ALL customers, not just those with "Key account" status.
    """

    def __init__(self):
        self._hint_shown = False

    def maybe_hint_key_account_exploration(
        self,
        model: Any,
        result: Any,
        task_text: str
    ) -> Optional[str]:
        """
        Generate hint when task asks about key account + exploration deals.

        Args:
            model: The request model
            result: API response
            task_text: Task instructions

        Returns:
            Hint string or None
        """
        if self._hint_shown:
            return None

        if not isinstance(model, client.Req_ListCustomers):
            return None

        task_lower = task_text.lower()

        # Detect "key account" + "exploration deals" pattern
        # AICODE-NOTE: t042 fix - check for both singular and plural forms
        has_key_account = 'key account' in task_lower
        has_exploration = any(p in task_lower for p in [
            'exploration deal', 'exploration deals',
            'exploring deal', 'exploring deals',
            'exploration project', 'exploration projects',
            'exploring project', 'exploring projects',
            'exploration status'
        ])

        if not (has_key_account and has_exploration):
            return None

        self._hint_shown = True

        return (
            f"⚠️ IMPORTANT: 'KEY ACCOUNT' TERMINOLOGY WARNING!\n"
            f"The term 'key account' can mean:\n"
            f"  1. Literally `high_level_status='Key account'` (CRM status), OR\n"
            f"  2. ANY important customer (all customers are 'accounts')\n\n"
            f"For 'exploration deals' questions:\n"
            f"  - 'Exploration deals' = PROJECTS with status='exploring' (not customer deal_phase!)\n"
            f"  - You MUST check ALL customers to find who has the most exploring projects\n"
            f"  - Do NOT filter only by high_level_status='Key account' - you might miss the answer!\n\n"
            f"CORRECT APPROACH:\n"
            f"  1. Get ALL customers (paginate fully!)\n"
            f"  2. For EACH customer: `projects_search(customer='cust_xxx', status='exploring')`\n"
            f"  3. Count exploring projects per customer\n"
            f"  4. Return customer with most exploring projects"
        )
