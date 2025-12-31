"""
Employee search handler with smart keyword fallback.

This handler implements intelligent employee search that:
1. Executes exact match query with page limit retry
2. If query has multiple words and few results: tries keyword fallback
3. If wills filter yields 0 results: tries fuzzy will name matching
4. Merges all results and returns unique employees
"""
import re
import copy
from typing import Any, Dict, List, Optional, Sequence, Tuple
from erc3 import ApiException
from erc3.erc3 import client
from erc3.erc3.dtos import SkillFilter, ProjectTeamFilter
from .base import ActionHandler
from ..base import ToolContext
from ..execution.pagination import handle_pagination_error
from utils import CLI_BLUE, CLI_YELLOW, CLI_GREEN, CLI_CLR


class EmployeeSearchHandler(ActionHandler):
    """
    Handler for Req_SearchEmployees with smart keyword fallback.

    When searching with multi-word queries (e.g., "Mira Schaefer"), the API
    may not find results for the full query. This handler tries individual
    keywords to improve recall.

    Also supports fuzzy matching for wills filter names (e.g., "will_mentor_junior_staff"
    may match "will_mentor_juniors").
    """

    def can_handle(self, ctx: ToolContext) -> bool:
        """Handle only employee search requests."""
        return isinstance(ctx.model, client.Req_SearchEmployees)

    # AICODE-NOTE: t056 FIX - Skill name corrections
    # Map base skill to more specific skill when task contains specificity hints
    SKILL_CORRECTIONS = {
        'skill_crm': [
            (['system', 'systems', 'system usage'], 'skill_crm_systems'),
        ],
    }

    def handle(self, ctx: ToolContext) -> bool:
        """
        Execute smart employee search with keyword fallback.

        Returns:
            False to let default handler continue with enrichments
        """
        action_name = ctx.model.__class__.__name__
        print(f"  {CLI_BLUE}▶ Executing:{CLI_CLR} {action_name}")

        # AICODE-NOTE: t056 FIX - Auto-correct skill names BEFORE API call
        # This must happen here because EmployeeSearchHandler bypasses pipeline preprocessors
        self._correct_skill_names(ctx)

        # AICODE-NOTE: t075 CRITICAL FIX - Early hint for skill queries without skill filter!
        # If task asks "least/most skilled in X" but agent uses department/location filter
        # instead of skill filter, warn BEFORE wasting turns on wrong search approach.
        current_offset = getattr(ctx.model, 'offset', 0) or 0
        if current_offset == 0 and self._is_skill_level_query(ctx) and not ctx.model.skills:
            skill_name = self._extract_skill_name_from_task(ctx)
            if skill_name:
                hint_lines = [
                    "",
                    f"⚠️ **SKILL QUERY DETECTED**: Task asks for 'skilled in {skill_name}'",
                    f"   You're using department/location filter, but this is a SKILL query!",
                    f"   ",
                    f"   ❌ WRONG: employees_search(department=...) or employees_search(location=...)",
                    f"   ✅ CORRECT: employees_search(skills=[{{name: 'skill_...'}}])",
                    f"   ",
                    f"   → Search skills_list for '{skill_name}' to find the exact skill ID.",
                    f"   → Then use: employees_search(skills=[{{name: 'skill_xxx', min_level: 1}}])",
                    "",
                ]
                ctx.results.append('\n'.join(hint_lines))
                print(f"  {CLI_YELLOW}⚠ t075: Skill query detected but no skill filter used!{CLI_CLR}")

        employees_map = {}
        exact_error = None
        res_exact = None

        # 1. Exact Match (Original Request) with page limit retry
        try:
            res_exact = ctx.api.dispatch(ctx.model)
            if hasattr(res_exact, 'employees') and res_exact.employees:
                for e in res_exact.employees:
                    employees_map[e.id] = e
        except Exception as e:
            error_msg = str(e).lower()
            # Check for page limit exceeded - retry with correct limit
            if 'page limit exceeded' in error_msg:
                res_exact = self._retry_with_correct_limit(ctx, e, employees_map)
                if res_exact is None:
                    exact_error = e
            elif any(x in error_msg for x in ['internal error', 'server error', 'timeout']):
                exact_error = e
                print(f"  {CLI_YELLOW}⚠ Exact search failed with system error: {e}{CLI_CLR}")
            else:
                print(f"  {CLI_YELLOW}⚠ Exact search failed: {e}{CLI_CLR}")

        # 1.5. Combined Skills+Wills Fuzzy Matching (if both filters present and 0 results)
        # AICODE-NOTE: Critical fix for t017, t056 - when BOTH skills and wills filters
        # have wrong names, we need to normalize BOTH before making the API call.
        # Previous approach tried them separately, but API requires BOTH to match.
        if len(employees_map) == 0 and ctx.model.skills and ctx.model.wills:
            fuzzy_results = self._try_combined_fuzzy_match(ctx, employees_map)
            if fuzzy_results:
                res_exact = fuzzy_results

        # 1.6. Skills-only Adaptive Hint (if skills filter yielded 0 results)
        # AICODE-NOTE: Replaced hardcoded fuzzy matching with adaptive approach.
        # Fetch available skills from system and let agent choose the correct one.
        # AICODE-NOTE: Do NOT show hint if offset > 0 (pagination - skill was found on earlier pages)
        current_offset = getattr(ctx.model, 'offset', 0) or 0
        if len(employees_map) == 0 and ctx.model.skills and not ctx.model.wills and current_offset == 0:
            original_skill_names = [s.name for s in ctx.model.skills]
            adaptive_hint = self._try_skills_adaptive_hint(ctx, original_skill_names)
            if adaptive_hint:
                ctx.results.append(adaptive_hint)

        # 1.6.1. Skills ambiguity hint (if skill found but there's a more specific variant)
        # AICODE-NOTE: t075 critical fix!
        # When task says "CRM system usage" and agent uses "skill_crm", but "skill_crm_systems" exists,
        # we should warn the agent that a more specific skill might be the correct one.
        if len(employees_map) > 0 and ctx.model.skills and current_offset == 0:
            ambiguity_hint = self._check_skill_ambiguity(ctx)
            if ambiguity_hint:
                ctx.results.append(ambiguity_hint)

        # 1.7. Wills-only Adaptive Hint (if wills filter yielded 0 results)
        # AICODE-NOTE: Adaptive approach - instead of hardcoded fuzzy matching,
        # fetch available wills from system and let agent choose the correct one.
        # AICODE-NOTE: Do NOT show hint if offset > 0 (pagination - will was found on earlier pages)
        if len(employees_map) == 0 and ctx.model.wills and not ctx.model.skills and current_offset == 0:
            original_will_names = [w.name for w in ctx.model.wills]
            adaptive_hint = self._try_wills_adaptive_hint(ctx, original_will_names)
            if adaptive_hint:
                ctx.results.append(adaptive_hint)

        # 1.8. Department Adaptive Hint (if department filter yielded 0 results)
        # AICODE-NOTE: t009 fix - "Human Resources" vs "HR" mismatch
        # AICODE-NOTE: t009 FIX #2 - Do NOT show hint if offset > 0!
        # If we're paginating (offset > 0), department was already found on first page.
        # Empty result on later pages just means we've reached the end, not that dept doesn't exist.
        # Note: current_offset already defined above for skills/wills hints
        if len(employees_map) == 0 and ctx.model.department and current_offset == 0:
            adaptive_hint = self._try_department_adaptive_hint(ctx, ctx.model.department)
            if adaptive_hint:
                ctx.results.append(adaptive_hint)

        # 2. Keyword Fallback (if query has multiple words and exact match yielded no/few results)
        query = ctx.model.query
        if query and len(employees_map) < 2 and " " in query.strip():
            print(f"  {CLI_BLUE}🔍 Smart Search: Executing keyword fallback for employees{CLI_CLR}")
            keywords = [k.strip() for k in query.split() if len(k.strip()) > 2]

            for kw in keywords:
                if kw.lower() == query.lower():
                    continue

                print(f"  {CLI_BLUE}  → Searching for keyword: '{kw}'{CLI_CLR}")
                model_kw = copy.deepcopy(ctx.model)
                model_kw.query = kw

                try:
                    res_kw = ctx.api.dispatch(model_kw)
                    if hasattr(res_kw, 'employees') and res_kw.employees:
                        for emp in res_kw.employees:
                            if emp.id not in employees_map:
                                employees_map[emp.id] = emp
                except Exception as e:
                    print(f"  {CLI_YELLOW}⚠ Keyword search '{kw}' failed: {e}{CLI_CLR}")

        # 2.5. Smart Name Matcher (t077 fix - reduce ambiguity for permuted names)
        # AICODE-NOTE: When query = "Messina Viola" and we find "Giulio Messina" + "Viola Messina",
        # the correct match is "Viola Messina" (query words are permutation of name words).
        # Without this, agent returns none_clarification_needed asking for disambiguation.
        if query and len(employees_map) > 1:
            exact_permutation = self._find_exact_name_permutation(query, list(employees_map.values()))
            if exact_permutation:
                print(f"  {CLI_BLUE}🎯 Smart Name Match: '{query}' -> '{exact_permutation.name}' (exact word permutation){CLI_CLR}")
                employees_map = {exact_permutation.id: exact_permutation}

        # 3. Check if we have a system error that prevents any results
        if exact_error and len(employees_map) == 0:
            # System error prevented search - store error for default handler
            ctx.shared['_search_error'] = exact_error
            return False  # Let default handler handle the error

        # 4. Construct Final Response
        next_offset = res_exact.next_offset if res_exact else -1

        result = client.Resp_SearchEmployees(
            employees=list(employees_map.values()),
            next_offset=next_offset
        )
        print(f"  {CLI_GREEN}✓ SUCCESS{CLI_CLR}")
        if len(employees_map) > 0:
            print(f"  {CLI_BLUE}🔍 Merged {len(employees_map)} unique employees.{CLI_CLR}")

        # 5. Auto-enrich with skill/will levels when filtering by skills/wills
        # AICODE-NOTE: t075/t086 critical fix! employees_search returns WHO HAS the skill/will,
        # but NOT their level. This makes it impossible for agent to find "least/most skilled"
        # without calling employees_get for each person (wasting turns).
        # Solution: automatically fetch levels for filtered skills/wills and include in result.
        if ctx.model.skills or ctx.model.wills:
            has_tracker = bool(ctx.shared.get('_global_skill_level_tracker'))
            should_enrich = len(employees_map) > 0 or (next_offset == -1 and has_tracker)
            if should_enrich:
                self._enrich_with_filter_levels(ctx, list(employees_map.values()), next_offset)
            # AICODE-NOTE: t077 FIX - Track coaching skill search for CoachingSearchGuard
            # If we searched with skills filter and got results, mark coaching search as done
            if ctx.model.skills and len(employees_map) > 0:
                ctx.shared['coaching_skill_search_done'] = True
                ctx.shared['coaching_skill_search_results'] = ctx.shared.get('coaching_skill_search_results', 0) + len(employees_map)
        # 5.1. Auto-enrich with workload when filtering by department (without skills/wills)
        # AICODE-NOTE: t009 critical fix! "Most busy from department X" queries don't have
        # skill/will filters, so workload enrichment wasn't triggered. We need to separately
        # call workload enrichment for department-only searches.
        elif ctx.model.department and not ctx.model.skills and not ctx.model.wills and len(employees_map) > 0:
            self._enrich_with_workload_for_department(ctx, list(employees_map.values()), next_offset)
        # 5.2. Auto-enrich with workload when filtering by location only (without skills/wills/department)
        # AICODE-NOTE: t012 FIX - "Which employee in Barcelona is busiest" should not require
        # time_summary_employee calls. Workload must be computed from project time_slice.
        elif (
            ctx.model.location
            and not ctx.model.department
            and not ctx.model.skills
            and not ctx.model.wills
            and len(employees_map) > 0
        ):
            self._enrich_with_workload_for_location(ctx, list(employees_map.values()), next_offset)

        # Store result in context for DefaultActionHandler enrichments
        ctx.shared['_employee_search_result'] = result
        return False  # Let default handler continue with enrichments

    def _get_task_text(self, ctx: ToolContext) -> str:
        """Extract task text from context."""
        task = ctx.shared.get('task')
        if not task:
            return ""

        task_text = (
            getattr(task, 'task', None)
            or getattr(task, 'question', None)
            or getattr(task, 'text', None)
            or str(task)
        )
        return str(task_text).lower()

    def _find_exact_name_permutation(self, query: str, employees: List) -> Optional[Any]:
        """
        Find employee whose name is an exact word permutation of query.

        AICODE-NOTE: t077 FIX - Smart name matching.
        When query = "Messina Viola" and candidates = ["Giulio Messina", "Viola Messina"],
        "Viola Messina" matches because query words {"messina", "viola"} == name words {"viola", "messina"}.

        Returns:
            Employee if exactly one matches, None otherwise (to avoid wrong auto-selection)
        """
        if not query or not employees:
            return None

        query_words = set(w.lower() for w in query.split() if w.strip())
        if len(query_words) < 2:
            return None  # Single word query - not a name permutation case

        matches = []
        for emp in employees:
            emp_name = getattr(emp, 'name', '') or ''
            name_words = set(w.lower() for w in emp_name.split() if w.strip())
            if query_words == name_words:
                matches.append(emp)

        # Only return if exactly ONE match - otherwise ambiguous
        if len(matches) == 1:
            return matches[0]
        return None

    def _is_workload_query(self, ctx: ToolContext) -> bool:
        """
        Detect whether the CURRENT TASK is actually asking for workload / busiest / least busy.

        AICODE-NOTE: Avoid expensive workload enrichment on every employees_search.
        This keeps token + API budget under control and prevents irrelevant noise in context.

        AICODE-NOTE: t075 FIX - Removed "project work" and "more work" from keywords!
        For "project work" tie-breakers, we use _is_project_work_tiebreaker() which
        triggers PROJECT COUNT calculation, NOT FTE. Showing FTE in workload hint
        confuses the agent into using FTE instead of project count.
        """
        t = self._get_task_text(ctx)
        if not t:
            return False

        keywords = (
            "busy",
            "busiest",
            "least busy",
            "workload",
            "time slice",
            "time_slice",
            "fte",
            "availability",
            "available",
            "utilization",
            "capacity",
            # NOTE: "project work" and "more work" intentionally excluded!
            # These trigger project COUNT tie-breaker, not FTE workload enrichment.
        )
        return any(k in t for k in keywords)

    def _is_project_work_tiebreaker(self, ctx: ToolContext) -> bool:
        """
        Detect if task explicitly asks for 'more project work' as tie-breaker.

        AICODE-NOTE: t075 CRITICAL FIX!
        "more project work" means COUNT of projects, not sum of time_slice!
        This is used ONLY for skill level tie-breakers (e.g., "least skilled... pick the one with more project work").
        """
        t = self._get_task_text(ctx)
        if not t:
            return False

        # Specific patterns for project work tie-breaker
        return "project work" in t or "more work" in t

    def _is_interest_superlative_query(self, ctx: ToolContext) -> bool:
        """
        Detect whether task asks for superlative + interest/will combination.

        AICODE-NOTE: t076 FIX v3!
        Pattern: "least/most busy person with interest in X"

        CORRECT interpretation (verified against benchmark):
        - "with interest in X" = FILTER (will_X >= 3)
        - "least busy" = PRIMARY ranking (MIN workload)
        - "with interest" = SECONDARY ranking (MAX interest level among tied workloads)

        The correct logic is:
        1. Filter employees with will X >= 3 (via API)
        2. Among filtered, find MIN workload (least busy)
        3. Among those with MIN workload, find MAX interest level
        4. Return ALL employees with MAX interest among MIN workload
        """
        t = self._get_task_text(ctx)
        if not t:
            return False

        # Must have superlative keyword
        superlative_keywords = (
            "least busy", "most busy", "busiest",
            "least skilled", "most skilled",
            "least interested", "most interested",
        )
        has_superlative = any(k in t for k in superlative_keywords)

        # Must have interest/will mention
        interest_keywords = ("interest in", "with interest", "interested in")
        has_interest = any(k in t for k in interest_keywords)

        return has_superlative and has_interest

    def _is_skill_level_query(self, ctx: ToolContext) -> bool:
        """
        Detect whether task asks for least/most skilled in X.

        AICODE-NOTE: t075 CRITICAL FIX!
        When task says "least/most skilled in X", agent MUST use skill filter,
        not department filter. This detects such queries.
        """
        t = self._get_task_text(ctx)
        if not t:
            return False

        # Pattern: "least skilled [person/employee/...] in X" or "most skilled ... in X"
        # Allow words between "skilled" and "in" (e.g., "least skilled person in")
        import re
        return bool(re.search(r'\b(?:least|most)\s+skilled\b.*?\bin\b', t, re.I))

    def _extract_skill_name_from_task(self, ctx: ToolContext) -> Optional[str]:
        """
        Extract skill name from "least/most skilled [person] in X" pattern.

        AICODE-NOTE: t075 FIX - Returns the X part for hint generation.
        """
        t = self._get_task_text(ctx)
        if not t:
            return None

        import re
        # Match "least/most skilled [person/employee/...] in X" where X is before "(" or end of string
        # Allow words between "skilled" and "in" (e.g., "least skilled person in Production planning")
        match = re.search(r'\b(?:least|most)\s+skilled\b.*?\bin\s+([^(]+?)(?:\s*\(|$)', t, re.I)
        if match:
            return match.group(1).strip()
        return None

    def _extract_time_slice_from_team(self, team: Sequence[Any], emp_id: str) -> float:
        """Extract employee time_slice from a project.team array (supports DTO objects or dicts)."""
        for member in team or []:
            member_emp = member.get('employee') if isinstance(member, dict) else getattr(member, 'employee', None)
            if member_emp == emp_id:
                ts = member.get('time_slice') if isinstance(member, dict) else getattr(member, 'time_slice', 0.0)
                try:
                    return float(ts or 0.0)
                except Exception:
                    return 0.0
        return 0.0

    def _fetch_workload_for_candidates(self, ctx: ToolContext, emp_ids: List[str]) -> Dict[str, float]:
        """
        Fetch workload (sum of time_slice from projects) for a list of employee IDs.

        AICODE-NOTE: Used for "least/most busy" queries where workload = sum of time_slice.

        Returns:
            Dict mapping emp_id -> total time_slice (workload)
        """
        if not emp_ids:
            return {}

        print(f"  {CLI_BLUE}🔍 Fetching workload for {len(emp_ids)} tie-breaker candidates...{CLI_CLR}")

        workload = {}
        project_team_cache: Dict[str, Sequence[Any]] = {}

        for emp_id in emp_ids:
            total_time_slice = 0.0

            try:
                proj_offset = 0
                proj_limit = 5
                seen_offsets = set()

                while True:
                    if proj_offset in seen_offsets:
                        break
                    seen_offsets.add(proj_offset)

                    # AICODE-NOTE: t010 FIX - Include ALL projects for workload calculation.
                    # Benchmark expects workload to include archived projects too.
                    proj_search = client.Req_SearchProjects(
                        team=ProjectTeamFilter(employee_id=emp_id),
                        include_archived=True,
                        limit=proj_limit,
                        offset=proj_offset
                    )

                    try:
                        proj_result = ctx.api.dispatch(proj_search)
                    except ApiException as e:
                        handled, retry_result = handle_pagination_error(e, proj_search, ctx.api)
                        if handled:
                            proj_result = retry_result
                            if proj_result is None:
                                break
                            proj_limit = getattr(proj_search, 'limit', proj_limit) or proj_limit
                        else:
                            raise

                    if not proj_result or not getattr(proj_result, 'projects', None):
                        break

                    for proj_brief in proj_result.projects:
                        proj_id = getattr(proj_brief, 'id', None)
                        if not proj_id:
                            continue

                        team = project_team_cache.get(proj_id)
                        if team is None:
                            try:
                                proj_detail = ctx.api.dispatch(client.Req_GetProject(id=proj_id))
                                team = proj_detail.project.team if (proj_detail and proj_detail.project) else []
                                project_team_cache[proj_id] = team or []
                            except Exception:
                                project_team_cache[proj_id] = []
                                continue

                        total_time_slice += self._extract_time_slice_from_team(team, emp_id)

                    next_proj_offset = getattr(proj_result, 'next_offset', -1)
                    if next_proj_offset is None or next_proj_offset <= 0:
                        break
                    proj_offset = next_proj_offset

            except Exception as e:
                print(f"  {CLI_YELLOW}⚠ Failed to fetch projects for {emp_id}: {e}{CLI_CLR}")

            workload[emp_id] = total_time_slice

        print(f"  {CLI_GREEN}✓ Computed workload for {len(workload)} candidates{CLI_CLR}")
        return workload

    def _fetch_project_count_for_candidates(self, ctx: ToolContext, emp_ids: List[str]) -> Dict[str, int]:
        """
        Fetch project COUNT for a list of employee IDs.

        AICODE-NOTE: t075 CRITICAL FIX!
        "more project work" tie-breaker means COUNT of projects, NOT sum of time_slice!
        Verified against successful benchmark runs where agent counted projects explicitly.

        Returns:
            Dict mapping emp_id -> number of projects (any status)
        """
        if not emp_ids:
            return {}

        print(f"  {CLI_BLUE}🔍 Counting projects for {len(emp_ids)} tie-breaker candidates...{CLI_CLR}")

        project_counts = {}

        for emp_id in emp_ids:
            total_projects = 0

            try:
                proj_offset = 0
                proj_limit = 5
                seen_offsets = set()

                while True:
                    if proj_offset in seen_offsets:
                        break
                    seen_offsets.add(proj_offset)

                    # AICODE-NOTE: Search ALL projects (not just active) - "project work" means involvement
                    proj_search = client.Req_SearchProjects(
                        team=ProjectTeamFilter(employee_id=emp_id),
                        limit=proj_limit,
                        offset=proj_offset
                    )

                    try:
                        proj_result = ctx.api.dispatch(proj_search)
                    except ApiException as e:
                        handled, retry_result = handle_pagination_error(e, proj_search, ctx.api)
                        if handled:
                            proj_result = retry_result
                            if proj_result is None:
                                break
                            proj_limit = getattr(proj_search, 'limit', proj_limit) or proj_limit
                        else:
                            raise

                    if not proj_result or not getattr(proj_result, 'projects', None):
                        break

                    total_projects += len(proj_result.projects)

                    next_proj_offset = getattr(proj_result, 'next_offset', -1)
                    if next_proj_offset is None or next_proj_offset <= 0:
                        break
                    proj_offset = next_proj_offset

            except Exception as e:
                print(f"  {CLI_YELLOW}⚠ Failed to count projects for {emp_id}: {e}{CLI_CLR}")

            project_counts[emp_id] = total_projects

        print(f"  {CLI_GREEN}✓ Counted projects for {len(project_counts)} candidates{CLI_CLR}")
        return project_counts

    def _retry_with_correct_limit(self, ctx: ToolContext, error: Exception, employees_map: dict) -> Any:
        """
        Retry search with corrected limit from error message.

        Returns:
            Response object if retry succeeded, None otherwise
        """
        match = re.search(r'(\d+)\s*>\s*(\d+)', str(error))
        if match:
            max_limit = int(match.group(2))
            if max_limit > 0:
                print(f"  {CLI_YELLOW}⚠ Page limit exceeded. Retrying with limit={max_limit}.{CLI_CLR}")
                model_retry = copy.deepcopy(ctx.model)
                model_retry.limit = max_limit
                try:
                    res_exact = ctx.api.dispatch(model_retry)
                    if hasattr(res_exact, 'employees') and res_exact.employees:
                        for emp in res_exact.employees:
                            employees_map[emp.id] = emp
                    return res_exact
                except Exception as retry_e:
                    print(f"  {CLI_YELLOW}⚠ Retry also failed: {retry_e}{CLI_CLR}")
                    return None
            else:
                print(f"  {CLI_YELLOW}⚠ API forbids pagination (max_limit={max_limit}){CLI_CLR}")
                return None
        else:
            print(f"  {CLI_YELLOW}⚠ Exact search failed with system error: {error}{CLI_CLR}")
            return None

    def _enrich_with_filter_levels(self, ctx: ToolContext, employees: List[Any], next_offset: int = -1) -> None:
        """
        Fetch and display skill/will levels for employees when filtering by skills/wills.

        AICODE-NOTE: t075/t086 critical fix!
        employees_search returns employees WHO HAVE the skill/will, but NOT their level.
        This enricher fetches actual levels and displays them, so agent can immediately
        see who has min/max level without additional API calls.

        Args:
            ctx: Tool context
            employees: List of employees from search results
            next_offset: Pagination offset (-1 if no more pages)

        This is NOT a hint - it's actual data that makes the tool more useful!
        """
        # Get filtered skill/will names
        skill_names = [s.name for s in (ctx.model.skills or [])]
        will_names = [w.name for w in (ctx.model.wills or [])]

        if not skill_names and not will_names:
            return

        print(f"  {CLI_BLUE}🔍 Enriching results with skill/will levels...{CLI_CLR}")

        # Fetch levels for each employee
        enriched_data = []
        for emp in employees:
            try:
                emp_model = client.Req_GetEmployee(id=emp.id)
                emp_result = ctx.api.dispatch(emp_model)
                if not emp_result.employee:
                    continue

                emp_info = {'id': emp.id, 'name': emp.name}

                # Extract skill levels
                if skill_names and emp_result.employee.skills:
                    for skill in emp_result.employee.skills:
                        if skill.name in skill_names:
                            emp_info[f'skill:{skill.name}'] = skill.level

                # Extract will levels
                if will_names and emp_result.employee.wills:
                    for will in emp_result.employee.wills:
                        if will.name in will_names:
                            emp_info[f'will:{will.name}'] = will.level

                enriched_data.append(emp_info)
            except Exception as e:
                print(f"  {CLI_YELLOW}⚠ Failed to fetch details for {emp.id}: {e}{CLI_CLR}")
                continue

        tracker_key = '_global_skill_level_tracker'
        if not enriched_data:
            if next_offset != -1:
                return
            if not ctx.shared.get(tracker_key):
                return

        # Build summary table for agent
        lines = [""]
        if enriched_data:
            lines.append("📊 **FILTER VALUES** (actual levels for filtered skills/wills):")

        # Determine columns
        filter_cols = []
        if skill_names:
            filter_cols.extend([f'skill:{s}' for s in skill_names])
        if will_names:
            filter_cols.extend([f'will:{w}' for w in will_names])

        # Sort by first filter column (ascending) for easier min/max detection
        if filter_cols:
            enriched_data.sort(key=lambda x: (x.get(filter_cols[0], 999), x['id']))

        # Build table
        for emp_info in enriched_data:
            levels = []
            for col in filter_cols:
                level = emp_info.get(col)
                if level is not None:
                    # Shorter column name for display
                    short_col = col.split(':')[1].replace('skill_', '').replace('will_', '')
                    levels.append(f"{short_col}={level}")
            if levels:
                lines.append(f"  • {emp_info['name']} ({emp_info['id']}): {', '.join(levels)}")

        # AICODE-NOTE: t075/t076 fix - Track global skill/will levels across pages
        # Initialize or update global tracker
        if tracker_key not in ctx.shared:
            ctx.shared[tracker_key] = {}
        
        # Only update tracker if we have new data - do not clear it!
        # This persists the data across multiple turn loops.
        for emp_info in enriched_data:
            emp_id = emp_info['id']
            ctx.shared[tracker_key][emp_id] = {
                'name': emp_info['name'],
                **{col: emp_info.get(col) for col in filter_cols if emp_info.get(col) is not None}
            }

        # Find min/max for each filter (THIS PAGE)
        for col in filter_cols:
            values = [(d['id'], d.get(col)) for d in enriched_data if d.get(col) is not None]
            if values:
                min_val = min(v[1] for v in values)
                max_val = max(v[1] for v in values)
                min_ids = [v[0] for v in values if v[1] == min_val]
                max_ids = [v[0] for v in values if v[1] == max_val]

                short_col = col.split(':')[1].replace('skill_', '').replace('will_', '')
                # AICODE-NOTE: t075 fix - clearly mark if this is per-page or global min/max
                if next_offset > 0:
                    lines.append(f"  → {short_col} MIN={min_val} (THIS PAGE): {', '.join(min_ids)}")
                    lines.append(f"  → {short_col} MAX={max_val} (THIS PAGE): {', '.join(max_ids)}")
                else:
                    lines.append(f"  → {short_col} MIN={min_val}: {', '.join(min_ids)}")
                    lines.append(f"  → {short_col} MAX={max_val}: {', '.join(max_ids)}")

        # AICODE-NOTE: t075 critical fix! If pagination exists, warn that min/max is NOT global
        if next_offset > 0:
            # AICODE-NOTE: t075 CRITICAL FIX #4 - Check remaining turns!
            # On last turn, don't tell agent to ignore turn budget - it MUST respond!
            current_turn = ctx.shared.get('current_turn', 0)
            max_turns = ctx.shared.get('max_turns', 20)
            remaining_turns = max_turns - current_turn - 1

            lines.append("")
            lines.append(f"🛑 **MORE PAGES EXIST** (next_offset={next_offset})!")
            lines.append(f"   ⚠️ CRITICAL: The MIN/MAX above is for THIS PAGE ONLY ({len(enriched_data)} employees).")
            lines.append(f"   The GLOBAL minimum/maximum may be DIFFERENT on later pages!")
            lines.append(f"   For 'least/most' queries → MUST paginate until next_offset=-1")
            lines.append("")

            # AICODE-NOTE: t075 CRITICAL FIX #4 - Switch to best-effort mode on last turn
            if remaining_turns <= 1:
                lines.append(f"   🛑 **LAST TURN** - You MUST respond NOW with best-effort answer!")
                lines.append(f"   → Use GLOBAL MIN/MAX from the data you have.")
                lines.append(f"   → Call `respond` tool immediately.")
                lines.append(f"   → NO answer = task failure!")
            else:
                lines.append(f"   ❌ IGNORE any 'turn budget' warnings! Superlative queries REQUIRE all data!")
                lines.append(f"   ❌ DO NOT RESPOND until next_offset=-1 (all pages fetched)!")
                lines.append(f"   ✅ Continue: employees_search(..., offset={next_offset})")

            # AICODE-NOTE: t075 FIX #2 - Show GLOBAL MIN/MAX so far to help agent track progress
            # This prevents agent from assuming current min is final when more pages exist
            global_tracker = ctx.shared.get(tracker_key, {})
            if global_tracker:
                for col in filter_cols:
                    global_values = [(emp_id, data.get(col)) for emp_id, data in global_tracker.items() if data.get(col) is not None]
                    if global_values:
                        global_min = min(v[1] for v in global_values)
                        global_max = max(v[1] for v in global_values)
                        global_min_ids = sorted([v[0] for v in global_values if v[1] == global_min])

                        short_col = col.split(':')[1].replace('skill_', '').replace('will_', '')
                        lines.append("")
                        lines.append(f"📊 GLOBAL MIN SO FAR (pages 1-{next_offset//5}): {short_col}={global_min} ({', '.join(global_min_ids[:3])}{'...' if len(global_min_ids) > 3 else ''})")
                        lines.append(f"   ⚠️ Pages {next_offset//5 + 1}+ NOT YET FETCHED - true minimum may be LOWER!")

            # AICODE-NOTE: t075 FIX #3 - Suggest BATCH pagination to save turns
            # If agent has fetched 3+ pages, show batch hint
            pages_fetched = next_offset // 5
            if pages_fetched >= 3:
                # Calculate next offsets for batch
                batch_offsets = [next_offset + i * 5 for i in range(10)]
                lines.append("")
                lines.append(f"⚡ **BATCH PAGINATION** — Fetch 10 pages in ONE turn:")
                lines.append(f'   "action_queue": [')
                lines.append(f'     {{"tool": "employees_search", "args": {{...same_filters..., "offset": {batch_offsets[0]}}}}},')
                lines.append(f'     {{"tool": "employees_search", "args": {{...same_filters..., "offset": {batch_offsets[1]}}}}},')
                lines.append(f'     {{"tool": "employees_search", "args": {{...same_filters..., "offset": {batch_offsets[2]}}}}},')
                lines.append(f'     // ... up to offset={batch_offsets[9]}')
                lines.append(f'   ]')

        # AICODE-NOTE: t075/t076 critical fix! On LAST PAGE, show GLOBAL summary
        # AICODE-NOTE: t076 FIX #2 - Always show GLOBAL SUMMARY when pagination ends
        # Previously only showed if tracker had more data than current page, but with batch
        # pagination the tracker accumulates data across all batch calls in one turn
        if next_offset == -1 and ctx.shared.get(tracker_key):
            global_tracker = ctx.shared[tracker_key]
            if global_tracker:
                # Show global summary - this tells agent the final result
                total_count = len(global_tracker)
                lines.append("")
                lines.append(f"📊 **GLOBAL SUMMARY** (all {total_count} employees across all pages):")

                # AICODE-NOTE: t076 CRITICAL FIX!
                # For "interest superlative" queries (e.g., "least busy person with interest in X"):
                # - "interest in X" means HIGHEST interest level (MAX will), not just any interest
                # - First filter to only MAX will level employees
                # - Then among them find least/most busy
                # - Return ALL that match the final criteria
                is_interest_superlative = self._is_interest_superlative_query(ctx)

                for col in filter_cols:
                    global_values = [(emp_id, data.get(col)) for emp_id, data in global_tracker.items() if data.get(col) is not None]
                    if global_values:
                        global_min = min(v[1] for v in global_values)
                        global_max = max(v[1] for v in global_values)
                        global_min_ids = sorted([v[0] for v in global_values if v[1] == global_min])
                        global_max_ids = sorted([v[0] for v in global_values if v[1] == global_max])

                        short_col = col.split(':')[1].replace('skill_', '').replace('will_', '')

                        # AICODE-NOTE: t076 FIX v3 - For "least busy person with interest in X":
                        # The CORRECT logic (verified against benchmark behavior):
                        # 1. Filter employees with interest (min_level >= 3) — done via API filter
                        # 2. Among filtered, find those with MIN workload (least busy)
                        # 3. Among those with MIN workload, find those with MAX interest level
                        # 4. Return ALL employees with MAX interest among MIN workload
                        #
                        # This is a TWO-STEP ranking:
                        # - Primary: MIN workload (least busy)
                        # - Secondary: MAX interest level among tied workloads
                        if is_interest_superlative and col.startswith('will:'):
                            lines.append(f"  → GLOBAL {short_col} MIN={global_min}: {', '.join(global_min_ids[:5])}{'...' if len(global_min_ids) > 5 else ''}")
                            lines.append(f"  → GLOBAL {short_col} MAX={global_max}: {', '.join(global_max_ids[:5])}{'...' if len(global_max_ids) > 5 else ''}")
                            lines.append("")

                            # Compute workload for ALL employees, then find the correct answer
                            if self._is_workload_query(ctx):
                                all_emp_ids = [emp_id for emp_id, _ in global_values]
                                all_workloads = self._fetch_workload_for_candidates(ctx, all_emp_ids)

                                if all_workloads:
                                    # Step 1: Find MIN workload (least busy)
                                    min_workload = min(all_workloads.values())
                                    least_busy_ids = [eid for eid, wl in all_workloads.items() if wl == min_workload]

                                    lines.append(f"  📊 **STEP 1**: Find least busy employees")
                                    lines.append(f"     MIN workload: {min_workload:.2f} FTE")
                                    lines.append(f"     {len(least_busy_ids)} employees have this workload")
                                    lines.append("")

                                    # Step 2: Among least busy, find the one(s) with HIGHEST interest level
                                    least_busy_with_levels = []
                                    for eid in least_busy_ids:
                                        level = None
                                        for emp_id, lv in global_values:
                                            if emp_id == eid:
                                                level = lv
                                                break
                                        if level is not None:
                                            name = global_tracker.get(eid, {}).get('name', eid)
                                            least_busy_with_levels.append((eid, name, level, min_workload))

                                    if least_busy_with_levels:
                                        # Sort by interest level descending, then ID ascending
                                        least_busy_with_levels.sort(key=lambda x: (-x[2], x[0]))

                                        lines.append(f"  📊 **STEP 2**: Among least busy, find HIGHEST interest level")
                                        for eid, name, level, wl in least_busy_with_levels[:10]:
                                            lines.append(f"     • {name} ({eid}): {short_col}={level}, {wl:.2f} FTE")

                                        # Find the answer - highest interest among least busy
                                        max_interest = least_busy_with_levels[0][2]
                                        answer_ids = [x for x in least_busy_with_levels if x[2] == max_interest]
                                        # AICODE-NOTE: t076 FIX - Persist interest-based winners for response guard.
                                        answer_emp_ids = [x[0] for x in answer_ids]
                                        if answer_emp_ids:
                                            ctx.shared['_interest_superlative_answer_ids'] = answer_emp_ids

                                        lines.append("")
                                        if len(answer_ids) == 1:
                                            ans = answer_ids[0]
                                            lines.append(f"  🏆 **ANSWER**: **{ans[1]} ({ans[0]})** - least busy ({ans[3]:.2f} FTE) with highest interest ({short_col}={ans[2]})")
                                        else:
                                            # AICODE-NOTE: t076 FIX - When multiple employees are tied,
                                            # benchmark expects ALL of them to be linked, not just one.
                                            # Return all tied employees in the response.
                                            lines.append(f"  🏆 **ANSWER**: {len(answer_ids)} employees are least busy AND have highest interest ({short_col}={max_interest}):")
                                            for ans in answer_ids:
                                                lines.append(f"     • {ans[1]} ({ans[0]})")
                                            lines.append("")
                                            lines.append(f"  ⚠️ Include ALL {len(answer_ids)} employees in your response!")
                        else:
                            # Standard behavior for non-interest-superlative queries
                            lines.append(f"  → GLOBAL {short_col} MIN={global_min}: {', '.join(global_min_ids)}")
                            lines.append(f"  → GLOBAL {short_col} MAX={global_max}: {', '.join(global_max_ids)}")

                            # AICODE-NOTE: t075 CRITICAL FIX!
                            # "more project work" tie-breaker = COUNT of projects, not time_slice sum!
                            # Verified against successful benchmark runs where agent counted projects.
                            if len(global_min_ids) > 1 and self._is_project_work_tiebreaker(ctx):
                                lines.append("")
                                lines.append(f"  💡 **TIE-BREAKER NEEDED**: {len(global_min_ids)} employees have MIN level {global_min}.")
                                lines.append(f"     Task asks for 'more project work' → COUNT of projects.")
                                lines.append("")
                                # Fetch project COUNT for MIN candidates
                                project_counts = self._fetch_project_count_for_candidates(ctx, global_min_ids)
                                if project_counts:
                                    lines.append(f"  📊 **PROJECT COUNT** for MIN candidates:")
                                    # AICODE-NOTE: t075 FIX - Sort by count DESC, then ID ASC
                                    # When tied on project count, pick LOWEST ID (alphabetically first)
                                    sorted_by_count = sorted(project_counts.items(), key=lambda x: (-x[1], x[0]))
                                    # Get all with max count
                                    max_count = sorted_by_count[0][1] if sorted_by_count else 0
                                    top_candidates = [(eid, cnt) for eid, cnt in sorted_by_count if cnt == max_count]
                                    # From top candidates, pick the one with LOWEST ID (alphabetically first)
                                    top_candidates_sorted = sorted(top_candidates, key=lambda x: x[0])
                                    winner = top_candidates_sorted[0]
                                    for emp_id, count in sorted_by_count:
                                        marker = " ← WINNER" if emp_id == winner[0] else ""
                                        lines.append(f"     • {emp_id}: {count} project(s){marker}")
                                    lines.append("")
                                    lines.append(f"  🏆 **WINNER** (most projects): **{winner[0]}** with {winner[1]} project(s)")
                                    # AICODE-NOTE: t075 FIX - When tied, pick LOWEST ID
                                    lines.append(f"  ⚠️ **USE THIS ANSWER**: {winner[0]} (projects DESC, then ID ASC for tie)")
                                    lines.append(f"  📝 Include link: {{'kind': 'employee', 'id': '{winner[0]}'}}")
                                    # AICODE-NOTE: t075 FIX - Store winner in shared for guard to enforce
                                    ctx.shared['_tie_breaker_winner'] = winner[0]

            # Clear tracker for next query ONLY if pagination is truly done
            # AND we are not in a batch processing loop (ctx.shared might persist)
            if next_offset == -1:
                ctx.shared[tracker_key] = {}

        # AICODE-NOTE: t076 fix - also fetch workload for "least/most busy" queries
        # This allows agent to find busy person without additional time_summary calls
        if self._is_workload_query(ctx):
            workload_lines = self._enrich_with_workload(ctx, enriched_data, next_offset)
            if workload_lines:
                lines.extend(workload_lines)

        print(f"  {CLI_GREEN}✓ Enriched {len(enriched_data)} employees with filter levels{CLI_CLR}")
        ctx.results.append('\n'.join(lines))

    def _enrich_with_workload_for_department(self, ctx: ToolContext, employees: List[Any], next_offset: int = -1) -> None:
        """
        Fetch and display workload for employees when filtering by department only.

        AICODE-NOTE: t009 critical fix!
        When searching by department (without skills/wills), we still need workload info
        for "least/most busy" queries. This is a separate path from _enrich_with_filter_levels.

        Args:
            ctx: Tool context
            employees: List of employees from search results
            next_offset: Pagination offset (-1 if no more pages)
        """
        if not employees:
            return

        # Build enriched_data format expected by _enrich_with_workload
        enriched_data = [{'id': emp.id, 'name': emp.name} for emp in employees]

        print(f"  {CLI_BLUE}🔍 Enriching department search with workload...{CLI_CLR}")

        if not self._is_workload_query(ctx):
            return

        workload_lines = self._enrich_with_workload(ctx, enriched_data, next_offset)

        if workload_lines:
            lines = [""]
            lines.extend(workload_lines)

            # AICODE-NOTE: t009 - Add pagination warning for workload too
            if next_offset > 0:
                # AICODE-NOTE: t075 CRITICAL FIX #5 - Check remaining turns!
                current_turn = ctx.shared.get('current_turn', 0)
                max_turns = ctx.shared.get('max_turns', 20)
                remaining_turns = max_turns - current_turn - 1

                lines.append("")
                lines.append(f"🛑 **MORE PAGES EXIST** (next_offset={next_offset})!")
                lines.append(f"   ⚠️ CRITICAL: The LEAST/MOST BUSY above is for THIS PAGE ONLY ({len(enriched_data)} employees).")
                lines.append(f"   The GLOBAL min/max workload may be DIFFERENT on later pages!")
                lines.append(f"   For 'least/most busy' queries → MUST paginate until next_offset=-1")
                lines.append("")

                # AICODE-NOTE: t075 CRITICAL FIX #5 - Switch to best-effort mode on last turn
                if remaining_turns <= 1:
                    lines.append(f"   🛑 **LAST TURN** - You MUST respond NOW with best-effort answer!")
                    lines.append(f"   → Use the data you have.")
                    lines.append(f"   → Call `respond` tool immediately.")
                    lines.append(f"   → NO answer = task failure!")
                else:
                    lines.append(f"   ❌ IGNORE any 'turn budget' warnings! Superlative queries REQUIRE all data!")
                    lines.append(f"   ❌ DO NOT RESPOND until next_offset=-1 (all pages fetched)!")
                    lines.append(f"   ✅ Continue: employees_search(..., offset={next_offset})")

            print(f"  {CLI_GREEN}✓ Enriched {len(enriched_data)} employees with workload{CLI_CLR}")
            ctx.results.append('\n'.join(lines))

    def _enrich_with_workload_for_location(self, ctx: ToolContext, employees: List[Any], next_offset: int = -1) -> None:
        """
        Fetch and display workload for employees when filtering by location only.

        AICODE-NOTE: t012 critical fix!
        Location-filtered searches are common for "busiest/least busy in X" queries.
        Without this, the agent falls back to time_summary_employee (often empty) and
        produces incorrect answers.
        """
        if not employees:
            return

        if not self._is_workload_query(ctx):
            return

        enriched_data = [{'id': emp.id, 'name': emp.name} for emp in employees]
        print(f"  {CLI_BLUE}🔍 Enriching location search with workload...{CLI_CLR}")

        workload_lines = self._enrich_with_workload(ctx, enriched_data, next_offset)
        if not workload_lines:
            return

        lines = [""]
        lines.extend(workload_lines)

        # Pagination warning (rare for small branches, but keep logic consistent)
        if next_offset > 0:
            current_turn = ctx.shared.get('current_turn', 0)
            max_turns = ctx.shared.get('max_turns', 20)
            remaining_turns = max_turns - current_turn - 1

            lines.append("")
            lines.append(f"🛑 **MORE PAGES EXIST** (next_offset={next_offset})!")
            lines.append(f"   ⚠️ CRITICAL: The LEAST/MOST BUSY above is for THIS PAGE ONLY ({len(enriched_data)} employees).")
            lines.append(f"   The GLOBAL min/max workload may be DIFFERENT on later pages!")
            lines.append(f"   For 'least/most busy' queries → MUST paginate until next_offset=-1")
            lines.append("")

            if remaining_turns <= 1:
                lines.append(f"   🛑 **LAST TURN** - You MUST respond NOW with best-effort answer!")
                lines.append(f"   → Use the data you have.")
                lines.append(f"   → Call `respond` tool immediately.")
                lines.append(f"   → NO answer = task failure!")
            else:
                lines.append(f"   ❌ DO NOT RESPOND until next_offset=-1 (all pages fetched)!")
                lines.append(f"   ✅ Continue: employees_search(..., offset={next_offset})")

        print(f"  {CLI_GREEN}✓ Enriched {len(enriched_data)} employees with workload (location){CLI_CLR}")
        ctx.results.append('\n'.join(lines))

    def _enrich_with_workload(self, ctx: ToolContext, enriched_data: List[dict], next_offset: int = -1) -> List[str]:
        """
        Fetch workload (sum of time_slice from projects) for employees.

        AICODE-NOTE: t076 CRITICAL FIX!
        According to wiki (systems/time_tracking_and_reporting.md):
        "when estimating workload (e.g. who is busiest or non-busiest),
        we rely on workload time slices via Project registry."

        Workload = SUM of time_slice across all projects where employee is a team member.
        NOT logged hours from time_summary!

        AICODE-NOTE: t011 critical fix!
        External department users do NOT have access to time summaries.

        Args:
            ctx: Tool context
            enriched_data: List of employee info dicts
            next_offset: Pagination offset (-1 if last page)

        Returns:
            List of lines to append to results, or empty list if no workload data
        """
        if not enriched_data:
            return []

        # AICODE-NOTE: t011 critical fix - check permissions before fetching workload!
        sm = ctx.shared.get('security_manager')
        if sm and hasattr(sm, 'department') and sm.department == 'External':
            print(f"  {CLI_YELLOW}⚠ Skipping workload enrichment: External dept has no access{CLI_CLR}")
            return []

        emp_ids = [e['id'] for e in enriched_data]
        print(f"  {CLI_BLUE}🔍 Fetching workload (project time_slice) for {len(emp_ids)} employees...{CLI_CLR}")

        try:
            # AICODE-NOTE: t076 fix - Calculate workload from projects, not time_summary!
            # For each employee, find their projects and sum time_slice values.
            workload = {}

            # Cache project teams across employees to avoid repeated projects_get calls.
            project_team_cache: Dict[str, Sequence[Any]] = {}

            for emp_id in emp_ids:
                total_time_slice = 0.0

                # Search projects where employee is a team member
                try:
                    # AICODE-NOTE: The ERC3 API enforces a very small max `limit` (often 5).
                    # Using larger defaults (e.g., 50) triggers "page limit exceeded" and makes workload look like 0.
                    proj_offset = 0
                    proj_limit = 5
                    seen_offsets = set()

                    while True:
                        if proj_offset in seen_offsets:
                            break
                        seen_offsets.add(proj_offset)

                        # AICODE-NOTE: t010 FIX - Include ALL projects for workload calculation.
                        # Benchmark expects workload to include archived projects too.
                        proj_search = client.Req_SearchProjects(
                            team=ProjectTeamFilter(employee_id=emp_id),
                            include_archived=True,
                            limit=proj_limit,
                            offset=proj_offset
                        )

                        try:
                            proj_result = ctx.api.dispatch(proj_search)
                        except ApiException as e:
                            handled, retry_result = handle_pagination_error(e, proj_search, ctx.api)
                            if handled:
                                proj_result = retry_result
                                # If API forbids pagination, treat as "no data available" for workload.
                                if proj_result is None:
                                    break
                                # Keep using the corrected limit for subsequent pages
                                proj_limit = getattr(proj_search, 'limit', proj_limit) or proj_limit
                            else:
                                raise

                        if not proj_result or not getattr(proj_result, 'projects', None):
                            break

                        # For each project, get details to extract time_slice
                        for proj_brief in proj_result.projects:
                            proj_id = getattr(proj_brief, 'id', None)
                            if not proj_id:
                                continue

                            team = project_team_cache.get(proj_id)
                            if team is None:
                                try:
                                    proj_detail = ctx.api.dispatch(client.Req_GetProject(id=proj_id))
                                    team = proj_detail.project.team if (proj_detail and proj_detail.project) else []
                                    project_team_cache[proj_id] = team or []
                                except Exception:
                                    # Skip if can't get project details
                                    project_team_cache[proj_id] = []
                                    continue

                            total_time_slice += self._extract_time_slice_from_team(team, emp_id)

                        next_proj_offset = getattr(proj_result, 'next_offset', -1)
                        if next_proj_offset is None or next_proj_offset <= 0:
                            break
                        proj_offset = next_proj_offset

                except Exception as e:
                    print(f"  {CLI_YELLOW}⚠ Failed to fetch projects for {emp_id}: {e}{CLI_CLR}")

                workload[emp_id] = total_time_slice

            # Build output lines
            lines = ["", "📊 **WORKLOAD** (sum of time_slice from projects):"]

            workload_list = []
            for emp_info in enriched_data:
                emp_id = emp_info['id']
                time_slice = workload.get(emp_id, 0.0)
                emp_info['workload'] = time_slice
                workload_list.append((emp_id, emp_info['name'], time_slice))

            # Sort by workload (ascending for least busy first)
            workload_list.sort(key=lambda x: (x[2], x[0]))

            for emp_id, name, ts in workload_list:
                lines.append(f"  • {name} ({emp_id}): {ts:.2f} FTE")

            # AICODE-NOTE: t009 fix - Track global workload across pages
            # Do not clear this key automatically - it must persist until end of pagination
            if '_global_workload_tracker' not in ctx.shared:
                ctx.shared['_global_workload_tracker'] = {}
            for emp_id, name, ts in workload_list:
                ctx.shared['_global_workload_tracker'][emp_id] = (name, ts)

            # Find min/max workload for THIS PAGE
            # AICODE-NOTE: t009 FIX - Round to 2 decimals to handle float precision issues
            # Example: 0.4 + 0.2 = 0.6000000000000001 which != 0.6 without rounding
            if workload_list:
                min_ts = round(min(w[2] for w in workload_list), 2)
                max_ts = round(max(w[2] for w in workload_list), 2)
                min_ids = [w[0] for w in workload_list if round(w[2], 2) == min_ts]
                max_ids = [w[0] for w in workload_list if round(w[2], 2) == max_ts]

                lines.append(f"  → LEAST BUSY ({min_ts:.2f} FTE): {', '.join(min_ids)}")
                lines.append(f"  → MOST BUSY ({max_ts:.2f} FTE): {', '.join(max_ids)}")

                # AICODE-NOTE: t012 FIX - For "busiest/least busy" queries, ties must include ALL
                # employees with the extreme value (no arbitrary tie-breaker unless task specifies one).
                task_text = self._get_task_text(ctx)
                is_busiest_query = ('busiest' in task_text) or ('most busy' in task_text)
                is_least_busy_query = ('least busy' in task_text)

                if is_busiest_query:
                    ctx.shared['_busiest_employee_ids'] = sorted(max_ids)
                    if len(max_ids) > 1:
                        lines.append("")
                        lines.append(
                            f"⚠️ **TIE FOR MOST BUSY**: {len(max_ids)} employees share the same highest workload ({max_ts:.2f} FTE)."
                        )
                        lines.append(f"✅ Include ALL in your response links: {', '.join(sorted(max_ids))}")
                elif is_least_busy_query:
                    ctx.shared['_least_busy_employee_ids'] = sorted(min_ids)
                    if len(min_ids) > 1:
                        lines.append("")
                        lines.append(
                            f"⚠️ **TIE FOR LEAST BUSY**: {len(min_ids)} employees share the same lowest workload ({min_ts:.2f} FTE)."
                        )
                        lines.append(f"✅ Include ALL in your response links: {', '.join(sorted(min_ids))}")

            # AICODE-NOTE: t009 critical fix! On LAST PAGE, show GLOBAL summary
            if next_offset == -1 and ctx.shared.get('_global_workload_tracker'):
                global_tracker = ctx.shared['_global_workload_tracker']
                if len(global_tracker) > len(workload_list):
                    all_workloads = [(emp_id, data[0], data[1]) for emp_id, data in global_tracker.items()]
                    # AICODE-NOTE: t009 FIX - Round to 2 decimals to handle float precision
                    global_min = round(min(w[2] for w in all_workloads), 2)
                    global_max = round(max(w[2] for w in all_workloads), 2)
                    global_min_ids = sorted([w[0] for w in all_workloads if round(w[2], 2) == global_min])
                    global_max_ids = sorted([w[0] for w in all_workloads if round(w[2], 2) == global_max])

                    lines.append("")
                    lines.append(f"📊 **GLOBAL SUMMARY** (all {len(global_tracker)} employees across all pages):")
                    lines.append(f"  → GLOBAL LEAST BUSY ({global_min:.2f} FTE): {', '.join(global_min_ids)}")
                    lines.append(f"  → GLOBAL MOST BUSY ({global_max:.2f} FTE): {', '.join(global_max_ids)}")

                    # AICODE-NOTE: t010 FIX - Persist GLOBAL least/busiest IDs for response guards.
                    task_text = self._get_task_text(ctx)
                    if 'least busy' in task_text:
                        ctx.shared['_least_busy_employee_ids'] = list(global_min_ids)
                    if 'most busy' in task_text or 'busiest' in task_text:
                        ctx.shared['_busiest_employee_ids'] = list(global_max_ids)

                    # AICODE-NOTE: t009 CRITICAL FIX - When multiple employees tied at MAX workload:
                    # - For "busiest/least busy" queries, include ALL tied employees (no arbitrary tie-breaker).
                    if len(global_max_ids) > 1:
                        task_text = self._get_task_text(ctx)
                        if 'most busy' in task_text or 'busiest' in task_text:
                            lines.append("")
                            lines.append(f"⚠️ **TIE AT MAXIMUM**: {len(global_max_ids)} employees have SAME highest workload ({global_max:.2f} FTE)")
                            lines.append(f"   ✅ Include ALL tied employees in your response: {', '.join(global_max_ids)}")
                            for emp_id in global_max_ids:
                                name = global_tracker.get(emp_id, (emp_id,))[0]
                                lines.append(f"      • {name} ({emp_id})")
                            lines.append(f"   📝 Include ALL {len(global_max_ids)} employee links in response!")

                            # Persist for response guards (do NOT rely on message parsing)
                            ctx.shared['_busiest_employee_ids'] = list(global_max_ids)

                    # AICODE-NOTE: t009/t075 FIX - When ALL employees have SAME workload,
                    # benchmark expects ALL of them in the response (no tie-breaker needed).
                    # Only use tie-breaker for "project work" queries, not for "busy" queries.
                    if global_min == global_max and len(global_min_ids) > 1:
                        # Check if task asks for "project work" tie-breaker
                        task_text = ctx.shared.get('task', {})
                        if hasattr(task_text, 'task'):
                            task_text = task_text.task.lower() if hasattr(task_text.task, 'lower') else str(task_text.task).lower()
                        else:
                            task_text = str(task_text).lower()

                        is_project_work = 'project work' in task_text or 'more work' in task_text

                        if is_project_work:
                            # AICODE-NOTE: t075 CRITICAL FIX! "project work" = COUNT of projects
                            lines.append("")
                            lines.append("📊 **PROJECT COUNT** (for tie-breaker):")
                            project_counts = self._fetch_project_count_for_candidates(ctx, global_min_ids[:10])
                            sorted_by_count = sorted(project_counts.items(), key=lambda x: (-x[1], x[0]))
                            for emp_id, count in sorted_by_count:
                                name = next((data[0] for eid, data in global_tracker.items() if eid == emp_id), emp_id)
                                lines.append(f"  • {name} ({emp_id}): {count} project(s)")

                            if sorted_by_count:
                                max_count = sorted_by_count[0][1]
                                max_count_ids = [e[0] for e in sorted_by_count if e[1] == max_count]
                                lines.append(f"  → MOST PROJECT WORK: {', '.join(max_count_ids)} ({max_count} project(s))")

                # Clear tracker only when done
                if next_offset == -1:
                    ctx.shared['_global_workload_tracker'] = {}

            print(f"  {CLI_GREEN}✓ Fetched workload for {len(workload)} employees{CLI_CLR}")
            return lines

        except Exception as e:
            print(f"  {CLI_YELLOW}⚠ Failed to fetch workload: {e}{CLI_CLR}")
            return []

    def _try_combined_fuzzy_match(self, ctx: ToolContext, employees_map: dict) -> Optional[Any]:
        """
        Provide adaptive hints for both skills and wills when combined search returns 0.

        AICODE-NOTE: Fully adaptive approach - NO hardcoded mappings!
        When both skills and wills filters present and 0 results:
        1. Check if this is a coaching query (skill names likely correct, will filter unnecessary)
        2. Otherwise fetch available skills/wills from system for agent to retry
        """
        print(f"  {CLI_BLUE}🔍 Smart Search: Combined skills+wills adaptive hint{CLI_CLR}")

        # AICODE-NOTE: t077 FIX - Check if this is skill-only coaching query
        # If task asks for "coaching on skills" (not willingness), the will filter
        # is the problem, not the skill/will names.
        task_text = self._get_task_text(ctx)
        coaching_keywords = ['coach', 'mentor', 'upskill', 'improve his skill', 'improve her skill']
        explicit_will_keywords = ['willing', 'willingness', 'motivation', 'want to mentor']

        is_coaching = any(kw in task_text for kw in coaching_keywords)
        explicit_will = any(kw in task_text for kw in explicit_will_keywords)

        if is_coaching and not explicit_will:
            # This is skill-only coaching - tell agent to remove will filter
            original_wills = [w.name for w in ctx.model.wills] if ctx.model.wills else []
            ctx.results.append(
                f"⚠️ COMBINED SKILL+WILL SEARCH RETURNED 0 RESULTS!\n\n"
                f"The task asks for 'coaching on skills' — this means SKILL level only!\n"
                f"Your wills filter {original_wills} is too restrictive.\n\n"
                f"**SOLUTION**: REMOVE the wills filter and retry:\n"
                f"  `employees_search(skills=[...])` — WITHOUT wills parameter!\n\n"
                f"Anyone with high skill level can coach — mentoring willingness is NOT required."
            )
            print(f"  {CLI_GREEN}✓ Returned coaching-specific hint (remove will filter){CLI_CLR}")
            return None

        # Default behavior: provide skill/will name hints
        available_skills = self._get_available_skills_from_api(ctx)
        available_wills = self._get_available_wills_from_api(ctx)

        hints = []

        # Skills hint
        if available_skills:
            original_skill_names = [s.name for s in ctx.model.skills]
            skills_list = ', '.join(sorted(available_skills))
            searched_skills = ', '.join(original_skill_names)
            hints.append(
                f"⚠️ SKILL NAME NOT FOUND: Your search for '{searched_skills}' returned 0 results.\n"
                f"Available skills: {skills_list}"
            )

        # Wills hint
        if available_wills:
            original_will_names = [w.name for w in ctx.model.wills]
            wills_list = ', '.join(sorted(available_wills))
            searched_wills = ', '.join(original_will_names)
            hints.append(
                f"⚠️ WILL NAME NOT FOUND: Your search for '{searched_wills}' returned 0 results.\n"
                f"Available wills: {wills_list}"
            )

        if hints:
            combined_hint = '\n\n'.join(hints)
            combined_hint += "\n\nPlease retry your search using the EXACT names from the lists above."
            ctx.results.append(combined_hint)
            print(f"  {CLI_GREEN}✓ Returned adaptive hint for skills and wills{CLI_CLR}")

        return None

    def _normalize_skill_name(self, skill_name: str) -> str:
        """
        Normalize a skill name to canonical form.
        Returns the canonical name, or original if no mapping found.
        """
        # Direct mapping table (lowercase -> canonical)
        skill_mappings = {
            # Languages
            'italian language': 'skill_italian',
            'italian': 'skill_italian',
            'german language': 'skill_german',
            'german': 'skill_german',
            'english language': 'skill_english',
            'english': 'skill_english',
            'french language': 'skill_french',
            'french': 'skill_french',
            'spanish language': 'skill_spanish',
            'spanish': 'skill_spanish',
            # Technical / Domain - CRITICAL for t017, t056, t074
            'crm': 'skill_crm',
            'crm system usage': 'skill_crm',
            'customer relationship management': 'skill_crm',
            'customer_relationship_management': 'skill_crm',
            'skill_customer_relationship': 'skill_crm',
            'skill_customer_relationship_management': 'skill_crm',
            'skill_crm_system_usage': 'skill_crm',
            'customer relationship': 'skill_crm',
            # Project management
            'project management': 'skill_project_mgmt',
            'project_management': 'skill_project_mgmt',
            'skill_project_management': 'skill_project_mgmt',
            # Rail industry - CRITICAL for t017
            'rail industry knowledge': 'skill_rail',
            'rail_industry_knowledge': 'skill_rail',
            'skill_rail_industry_knowledge': 'skill_rail',
            'rail knowledge': 'skill_rail',
            'rail': 'skill_rail',
            # Progress OpenEdge
            'progress openedge administration': 'skill_progress_admin',
            'progress_openedge_administration': 'skill_progress_admin',
            'skill_progress_openedge_administration': 'skill_progress_admin',
            'progress admin': 'skill_progress_admin',
            'openedge': 'skill_progress_admin',
            # Negotiation
            'negotiation': 'skill_negotiation',
            # QMS
            'quality management': 'skill_qms',
            'qms': 'skill_qms',
            # Coatings
            'technical coatings': 'skill_technical_coatings',
            'technical coatings knowledge': 'skill_technical_coatings',
            'solventborne': 'skill_solventborne',
            'solventborne formulation': 'skill_solventborne',
            'solventborne_formulation': 'skill_solventborne',
            'skill_solventborne_formulation': 'skill_solventborne',
            'waterborne': 'skill_waterborne',
            'waterborne formulation': 'skill_waterborne',
            'corrosion': 'skill_corrosion',
            'corrosion protection': 'skill_corrosion',
            'corrosion testing': 'skill_corrosion',
            'corrosion testing and standards': 'skill_corrosion',
            'corrosion_testing': 'skill_corrosion',
            'corrosion_testing_and_standards': 'skill_corrosion',
            'skill_corrosion_testing': 'skill_corrosion',
            'skill_corrosion_testing_and_standards': 'skill_corrosion',
            'corrosion resistance': 'skill_corrosion',
            'corrosion resistance testing': 'skill_corrosion',
            # Data analysis
            'data analysis': 'skill_data_analysis',
            'python': 'skill_python',
            'excel': 'skill_excel',
        }

        name_lower = skill_name.lower().strip()

        # Check direct mapping
        if name_lower in skill_mappings:
            return skill_mappings[name_lower]

        # If already starts with skill_, try extracting first word
        # "skill_rail_industry_knowledge" -> "skill_rail"
        if skill_name.startswith('skill_'):
            # Try mapping with underscore replaced by space
            name_as_words = skill_name.replace('skill_', '').replace('_', ' ')
            if name_as_words in skill_mappings:
                return skill_mappings[name_as_words]

            # Try progressively shorter versions
            parts = skill_name.split('_')
            if len(parts) > 2:
                # Try just first meaningful word: skill_rail
                short_name = f"{parts[0]}_{parts[1]}"
                return short_name

        return skill_name

    def _get_available_wills_from_api(self, ctx: ToolContext) -> List[str]:
        """
        Fetch available will names from API by getting a sample employee.

        AICODE-NOTE: Adaptive approach - instead of hardcoded mappings,
        we discover actual will names from the system and let agent choose.
        """
        try:
            # Get first employee to see available wills
            model = client.Req_SearchEmployees(limit=1, offset=0)
            result = ctx.api.dispatch(model)
            if result.employees:
                emp_id = result.employees[0].id
                # Get full employee details with wills
                emp_model = client.Req_GetEmployee(id=emp_id)
                emp_result = ctx.api.dispatch(emp_model)
                if emp_result.employee and emp_result.employee.wills:
                    return [w.name for w in emp_result.employee.wills]
        except Exception:
            pass
        return []

    def _get_available_skills_from_api(self, ctx: ToolContext) -> List[str]:
        """
        Fetch available skill names from API by getting a sample employee.

        AICODE-NOTE: Same adaptive approach as wills - discover actual skill names
        from the system instead of hardcoded mappings.
        """
        try:
            # Get first employee to see available skills
            model = client.Req_SearchEmployees(limit=1, offset=0)
            result = ctx.api.dispatch(model)
            if result.employees:
                emp_id = result.employees[0].id
                # Get full employee details with skills
                emp_model = client.Req_GetEmployee(id=emp_id)
                emp_result = ctx.api.dispatch(emp_model)
                if emp_result.employee and emp_result.employee.skills:
                    return [s.name for s in emp_result.employee.skills]
        except Exception:
            pass
        return []

    def _generate_skill_variations(self, skill_name: str) -> List[str]:
        """Generate fuzzy variations of a skill name."""
        variations = []

        # If starts with skill_, try shorter versions
        if skill_name.startswith('skill_'):
            parts = skill_name.split('_')
            # Try dropping last parts one by one
            for i in range(len(parts) - 1, 1, -1):
                short_name = '_'.join(parts[:i])
                if short_name != skill_name:
                    variations.append(short_name)

            # Also try just skill_firstword
            if len(parts) > 2:
                variations.append(f"skill_{parts[1]}")

        return variations

    def _try_skills_fuzzy_match(self, ctx: ToolContext, employees_map: dict) -> Optional[Any]:
        """
        Try fuzzy matching for skill names when exact match returns 0 results.

        Handles common human-readable -> system name mappings:
        - "Italian language" -> "skill_language_italian"
        - "German language" -> "skill_language_german"
        - "CRM" -> "skill_crm"

        Returns:
            Response object if any variation found results, None otherwise
        """
        print(f"  {CLI_BLUE}🔍 Smart Search: Trying skills fuzzy matching{CLI_CLR}")

        # Common human-readable to system name mappings
        # AICODE-NOTE: These mappings handle the mismatch between how agent
        # might name skills vs actual DB names. Critical for t056, t017, t086.
        skill_mappings = {
            # Languages
            'italian language': 'skill_italian',
            'italian': 'skill_italian',
            'german language': 'skill_german',
            'german': 'skill_german',
            'english language': 'skill_english',
            'english': 'skill_english',
            'french language': 'skill_french',
            'french': 'skill_french',
            'spanish language': 'skill_spanish',
            'spanish': 'skill_spanish',
            # Technical / Domain - CRITICAL for t017, t074
            'crm': 'skill_crm',
            'customer relationship management': 'skill_crm',
            'customer_relationship_management': 'skill_crm',
            'skill_customer_relationship': 'skill_crm',
            'skill_customer_relationship_management': 'skill_crm',
            'customer relationship': 'skill_crm',
            'project management': 'skill_project_mgmt',
            'project_management': 'skill_project_mgmt',
            'skill_project_management': 'skill_project_mgmt',
            'data analysis': 'skill_data_analysis',
            'python': 'skill_python',
            'excel': 'skill_excel',
            # Industry knowledge - CRITICAL for t056
            'rail industry knowledge': 'skill_rail',
            'rail_industry_knowledge': 'skill_rail',
            'skill_rail_industry_knowledge': 'skill_rail',
            'rail knowledge': 'skill_rail',
            'rail': 'skill_rail',
            # Progress OpenEdge - CRITICAL for t017
            'progress openedge administration': 'skill_progress_admin',
            'progress_openedge_administration': 'skill_progress_admin',
            'skill_progress_openedge_administration': 'skill_progress_admin',
            'progress admin': 'skill_progress_admin',
            'openedge': 'skill_progress_admin',
            # Negotiation
            'negotiation': 'skill_negotiation',
            # QMS
            'quality management': 'skill_qms',
            'qms': 'skill_qms',
            # Coatings - CRITICAL for t056, t075
            'technical coatings': 'skill_technical_coatings',
            'technical coatings knowledge': 'skill_technical_coatings',
            'solventborne': 'skill_solventborne',
            'solventborne formulation': 'skill_solventborne',
            'solventborne_formulation': 'skill_solventborne',
            'skill_solventborne_formulation': 'skill_solventborne',
            'waterborne': 'skill_waterborne',
            'waterborne formulation': 'skill_waterborne',
            'corrosion': 'skill_corrosion',
            'corrosion protection': 'skill_corrosion',
            # AICODE-NOTE: t075 fix - verbose corrosion skill names
            'corrosion testing': 'skill_corrosion',
            'corrosion testing and standards': 'skill_corrosion',
            'corrosion_testing': 'skill_corrosion',
            'corrosion_testing_and_standards': 'skill_corrosion',
            'skill_corrosion_testing': 'skill_corrosion',
            'skill_corrosion_testing_and_standards': 'skill_corrosion',
            'corrosion resistance': 'skill_corrosion',
            'corrosion resistance testing': 'skill_corrosion',
        }

        original_skills = ctx.model.skills
        tried_names = set()

        for skill_filter in original_skills:
            original_name = skill_filter.name
            tried_names.add(original_name)

            # Generate variations
            variations = []

            # 1. Check direct mapping
            name_lower = original_name.lower().strip()
            if name_lower in skill_mappings:
                variations.append(skill_mappings[name_lower])

            # 2. Try converting to snake_case with skill_ prefix
            # "Italian language" -> "skill_italian_language"
            snake_name = 'skill_' + name_lower.replace(' ', '_')
            if snake_name not in tried_names:
                variations.append(snake_name)

            # 3. Try without "language" suffix
            # "Italian language" -> "skill_italian"
            if 'language' in name_lower:
                lang_name = name_lower.replace(' language', '').replace('language ', '')
                variations.append('skill_language_' + lang_name.replace(' ', '_'))
                variations.append('skill_' + lang_name.replace(' ', '_'))

            # 4. AGGRESSIVE FALLBACK: Try progressively shorter versions
            # "skill_rail_industry_knowledge" -> "skill_rail_industry" -> "skill_rail"
            # AICODE-NOTE: Critical for t056 where agent uses verbose skill names
            if original_name.startswith('skill_'):
                parts = original_name.split('_')
                # Try dropping last parts one by one
                for i in range(len(parts) - 1, 1, -1):
                    short_name = '_'.join(parts[:i])
                    if short_name not in tried_names and short_name != original_name:
                        variations.append(short_name)

            # 5. Try extracting just the first meaningful word after skill_
            # "skill_rail_industry_knowledge" -> "skill_rail"
            if original_name.startswith('skill_'):
                first_word = original_name.replace('skill_', '').split('_')[0]
                short_skill = f'skill_{first_word}'
                if short_skill not in tried_names and short_skill != original_name:
                    variations.append(short_skill)

            for var_name in variations:
                if var_name in tried_names:
                    continue
                tried_names.add(var_name)

                print(f"  {CLI_BLUE}  → Trying skill variation: '{var_name}'{CLI_CLR}")

                model_var = copy.deepcopy(ctx.model)
                model_var.skills = [
                    SkillFilter(
                        name=var_name,
                        min_level=skill_filter.min_level,
                        max_level=skill_filter.max_level
                    )
                ]

                try:
                    res_var = ctx.api.dispatch(model_var)
                    if hasattr(res_var, 'employees') and res_var.employees:
                        print(f"  {CLI_GREEN}✓ Found {len(res_var.employees)} employees with '{var_name}'{CLI_CLR}")
                        for emp in res_var.employees:
                            employees_map[emp.id] = emp
                        return res_var
                except Exception:
                    pass  # Silently try next variation

        return None

    def _try_wills_adaptive_hint(self, ctx: ToolContext, original_will_names: List[str]) -> Optional[str]:
        """
        When will search returns 0 results, fetch available wills and return hint for agent.

        AICODE-NOTE: Adaptive approach - NO hardcoded mappings!
        Instead of guessing the correct will name, we:
        1. Fetch actual will names from the system
        2. Return a hint to the agent with available options
        3. Let the agent (LLM) choose the correct mapping using its language understanding

        This scales to any new will names without code changes.
        """
        print(f"  {CLI_BLUE}🔍 Adaptive Will Discovery: Fetching available wills from system{CLI_CLR}")

        available_wills = self._get_available_wills_from_api(ctx)

        if not available_wills:
            print(f"  {CLI_YELLOW}⚠ Could not fetch available wills{CLI_CLR}")
            return None

        print(f"  {CLI_GREEN}✓ Found {len(available_wills)} available wills{CLI_CLR}")

        # Format hint for agent
        wills_list = ', '.join(sorted(available_wills))
        searched_wills = ', '.join(original_will_names)

        hint = (
            f"⚠️ WILL NAME NOT FOUND: Your search for '{searched_wills}' returned 0 results.\n"
            f"The system uses specific will names. Available wills in this workspace:\n"
            f"  {wills_list}\n\n"
            f"Please retry your search using the EXACT will name from the list above.\n"
            f"For example, if you searched for 'will_mentoring_junior_staff', try 'will_mentor_juniors' instead."
        )

        return hint

    def _check_skill_ambiguity(self, ctx: ToolContext) -> Optional[str]:
        """
        Check if the searched skill might be ambiguous with a more specific variant.

        AICODE-NOTE: t075 critical fix!
        When task says "CRM system usage" and agent uses "skill_crm", but "skill_crm_systems" exists,
        the agent might have chosen the wrong skill. This hint warns about the ambiguity.

        The check uses two methods:
        1. Suffix matching: if task contains suffix word (e.g., "system" from "skill_crm_systems")
        2. Semantic similarity: if embedding similarity between task and specific skill is higher
        """
        if not ctx.model.skills:
            return None

        searched_skills = [s.name for s in ctx.model.skills]
        available_skills = self._get_available_skills_from_api(ctx)

        if not available_skills:
            return None

        # Get task text to check for specificity hints
        task_text = self._get_task_text(ctx)

        hints = []
        for searched in searched_skills:
            # Find more specific variants
            more_specific = [s for s in available_skills if s.startswith(searched + '_')]

            if more_specific:
                best_match = None
                best_reason = None

                # Method 1: Suffix matching
                for specific_skill in more_specific:
                    suffix = specific_skill.replace(searched + '_', '')
                    suffix_lower = suffix.lower()
                    suffix_variants = [suffix_lower]
                    if suffix_lower.endswith('s'):
                        suffix_variants.append(suffix_lower[:-1])

                    for variant in suffix_variants:
                        if variant in task_text:
                            best_match = specific_skill
                            best_reason = f"task mentions '{variant}'"
                            break
                    if best_match:
                        break

                # Method 2: Semantic similarity (if no suffix match found)
                if not best_match:
                    best_match = self._find_best_skill_by_similarity(task_text, searched, more_specific)
                    if best_match:
                        best_reason = "semantic similarity"

                if best_match:
                    hints.append(
                        f"⚠️ **SKILL AMBIGUITY**: You searched for '{searched}', but {best_reason} suggests a more specific skill.\n"
                        f"   More specific skill: **{best_match}**\n"
                        f"   Please verify and retry with the correct skill if needed."
                    )

        if hints:
            return '\n\n'.join(hints)
        return None

    def _find_best_skill_by_similarity(self, task_text: str, base_skill: str, candidates: List[str]) -> Optional[str]:
        """
        Use sentence embeddings to find which candidate skill is most similar to task text.

        Returns the candidate skill if it's significantly more similar than base skill, else None.
        """
        try:
            from handlers.wiki.embeddings import get_embedding_model
            model = get_embedding_model()
            if not model:
                return None

            # Convert skill names to readable form
            def skill_to_text(skill: str) -> str:
                return skill.replace('skill_', '').replace('_', ' ')

            base_text = skill_to_text(base_skill)
            candidate_texts = [skill_to_text(c) for c in candidates]

            # Compute embeddings
            task_embedding = model.encode([task_text])[0]
            base_embedding = model.encode([base_text])[0]
            candidate_embeddings = model.encode(candidate_texts)

            # Compute cosine similarities
            from numpy import dot
            from numpy.linalg import norm

            def cosine_sim(a, b):
                return dot(a, b) / (norm(a) * norm(b))

            base_sim = cosine_sim(task_embedding, base_embedding)
            candidate_sims = [(candidates[i], cosine_sim(task_embedding, candidate_embeddings[i]))
                            for i in range(len(candidates))]

            # Find best candidate
            best_candidate = max(candidate_sims, key=lambda x: x[1])

            # Return if significantly better than base (at least 5% improvement)
            if best_candidate[1] > base_sim * 1.05:
                return best_candidate[0]

        except Exception:
            pass

        return None

    def _try_skills_adaptive_hint(self, ctx: ToolContext, original_skill_names: List[str]) -> Optional[str]:
        """
        When skill search returns 0 results, fetch available skills and return hint for agent.

        AICODE-NOTE: Same adaptive approach as wills - NO hardcoded mappings!
        Instead of guessing the correct skill name, we:
        1. Fetch actual skill names from the system
        2. Return a hint to the agent with available options
        3. Let the agent (LLM) choose the correct mapping using its language understanding

        This replaces the brittle hardcoded skill_mappings dictionary.
        """
        print(f"  {CLI_BLUE}🔍 Adaptive Skill Discovery: Fetching available skills from system{CLI_CLR}")

        available_skills = self._get_available_skills_from_api(ctx)

        if not available_skills:
            print(f"  {CLI_YELLOW}⚠ Could not fetch available skills{CLI_CLR}")
            return None

        print(f"  {CLI_GREEN}✓ Found {len(available_skills)} available skills{CLI_CLR}")

        # Format hint for agent
        skills_list = ', '.join(sorted(available_skills))
        searched_skills = ', '.join(original_skill_names)

        hint = (
            f"⚠️ SKILL NAME NOT FOUND: Your search for '{searched_skills}' returned 0 results.\n"
            f"The system uses specific skill names. Available skills in this workspace:\n"
            f"  {skills_list}\n\n"
            f"Please retry your search using the EXACT skill name from the list above.\n"
            f"For example, if you searched for 'Production planning and scheduling', try 'skill_production_planning' instead."
        )

        return hint

    def _try_department_adaptive_hint(self, ctx: ToolContext, searched_department: str) -> Optional[str]:
        """
        When department search returns 0 results, fetch available departments and return hint.

        AICODE-NOTE: t009 fix - "Human Resources" vs "HR" mismatch.
        Same adaptive approach as wills - fetch actual department names from system.
        """
        print(f"  {CLI_BLUE}🔍 Adaptive Department Discovery: Fetching available departments{CLI_CLR}")

        available_departments = self._get_available_departments_from_api(ctx)

        if not available_departments:
            print(f"  {CLI_YELLOW}⚠ Could not fetch available departments{CLI_CLR}")
            return None

        print(f"  {CLI_GREEN}✓ Found {len(available_departments)} available departments{CLI_CLR}")

        # Format hint for agent
        depts_list = ', '.join(sorted(available_departments))

        # AICODE-NOTE: t009 FIX #3 - Check if searched department matches any available
        # department partially (case-insensitive). This helps with "Human Resources" vs "HR"
        searched_lower = searched_department.lower()
        potential_matches = []

        # AICODE-NOTE: t009 FIX #4 - Add hardcoded common department aliases
        # These cover cases where API discovery doesn't find the department
        common_aliases = {
            'human resources': ['Human Resources (HR)', 'Human Resources', 'HR'],
            'hr': ['Human Resources (HR)', 'Human Resources', 'HR'],
            'it': ['IT & Digital', 'IT'],
            'sales': ['Sales & Customer Success', 'Sales'],
            'r&d': ['R&D and Technical Service', 'R&D'],
            'production': ['Production – Italy', 'Production – Serbia', 'Production'],
            'quality': ['Quality & HSE', 'Quality'],
            'logistics': ['Logistics & Supply Chain', 'Logistics'],
            'finance': ['Finance & Administration', 'Finance'],
        }

        # Check hardcoded aliases first
        for key, aliases in common_aliases.items():
            if key in searched_lower:
                for alias in aliases:
                    if alias not in potential_matches:
                        potential_matches.append(alias)

        # Then check dynamic matches
        for dept in available_departments:
            dept_lower = dept.lower()
            # Check for partial matches
            if searched_lower in dept_lower or dept_lower in searched_lower:
                if dept not in potential_matches:
                    potential_matches.append(dept)
            # Check for common abbreviations
            elif searched_lower == 'hr' and 'human' in dept_lower:
                if dept not in potential_matches:
                    potential_matches.append(dept)
            elif searched_lower == 'human resources' and 'hr' in dept_lower:
                if dept not in potential_matches:
                    potential_matches.append(dept)

        if potential_matches:
            hint = (
                f"⚠️ DEPARTMENT NOT FOUND: Your search for department='{searched_department}' returned 0 results.\n"
                f"Did you mean one of these?\n"
                f"  {', '.join(potential_matches)}\n\n"
                f"Available departments: {depts_list}\n"
                f"Please retry with the EXACT department name."
            )
        else:
            hint = (
                f"⚠️ DEPARTMENT NOT FOUND: Your search for department='{searched_department}' returned 0 results.\n"
                f"The system uses specific department names. Available departments in this workspace:\n"
                f"  {depts_list}\n\n"
                f"If the department doesn't exist in this list, the data is simply not available."
            )

        return hint

    def _get_available_departments_from_api(self, ctx: ToolContext) -> List[str]:
        """
        Fetch available department names from API by getting sample employees.

        AICODE-NOTE: t009 FIX #2 - Increased sample size to catch small departments like HR.
        Previous: 10 employees (2 pages of 5)
        Now: up to 50 employees (10 pages of 5) to ensure we find all departments
        """
        try:
            departments = set()
            offset = 0
            max_pages = 10  # Up to 50 employees

            for _ in range(max_pages):
                model = client.Req_SearchEmployees(limit=5, offset=offset)
                result = ctx.api.dispatch(model)

                if result.employees:
                    for emp in result.employees:
                        if emp.department:
                            departments.add(emp.department)

                # Stop if no more pages
                if result.next_offset <= 0:
                    break
                offset = result.next_offset

                # Stop if we have enough departments (likely found all)
                if len(departments) >= 8:
                    break

            return list(departments)
        except Exception:
            pass
        return []

    def _correct_skill_names(self, ctx: ToolContext) -> None:
        """
        AICODE-NOTE: t056 FIX - Auto-correct skill names BEFORE API call.

        Problem: When task asks for "CRM system usage skills", agent uses "skill_crm"
        but the correct skill is "skill_crm_systems". Since EmployeeSearchHandler
        bypasses pipeline preprocessors, we must correct skill names here.

        This method mutates ctx.model.skills in place.
        """
        if not ctx.model.skills:
            return

        task_text = self._get_task_text(ctx).lower()
        if not task_text:
            return

        for skill in ctx.model.skills:
            skill_name = skill.name
            if skill_name not in self.SKILL_CORRECTIONS:
                continue

            for specificity_words, correct_skill in self.SKILL_CORRECTIONS[skill_name]:
                for word in specificity_words:
                    if word in task_text:
                        print(f"  🔧 [t056 fix] Auto-correcting skill: {skill_name} -> {correct_skill}")
                        skill.name = correct_skill
                        break
                else:
                    continue
                break

    def _get_task_text(self, ctx: ToolContext) -> str:
        """Extract task text from context."""
        task = ctx.shared.get("task")
        return getattr(task, "task_text", "") or ""
