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
from typing import Any, List, Optional
from erc3.erc3 import client
from erc3.erc3.dtos import SkillFilter, ProjectTeamFilter
from .base import ActionHandler
from ..base import ToolContext
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

    def handle(self, ctx: ToolContext) -> bool:
        """
        Execute smart employee search with keyword fallback.

        Returns:
            False to let default handler continue with enrichments
        """
        action_name = ctx.model.__class__.__name__
        print(f"  {CLI_BLUE}▶ Executing:{CLI_CLR} {action_name}")

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
        if (ctx.model.skills or ctx.model.wills) and len(employees_map) > 0:
            self._enrich_with_filter_levels(ctx, list(employees_map.values()), next_offset)
        # 5.1. Auto-enrich with workload when filtering by department (without skills/wills)
        # AICODE-NOTE: t009 critical fix! "Most busy from department X" queries don't have
        # skill/will filters, so workload enrichment wasn't triggered. We need to separately
        # call workload enrichment for department-only searches.
        elif ctx.model.department and not ctx.model.skills and not ctx.model.wills and len(employees_map) > 0:
            self._enrich_with_workload_for_department(ctx, list(employees_map.values()), next_offset)

        # Store result in context for DefaultActionHandler enrichments
        ctx.shared['_employee_search_result'] = result
        return False  # Let default handler continue with enrichments

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

        if not enriched_data:
            return

        # Build summary table for agent
        lines = ["", "📊 **FILTER VALUES** (actual levels for filtered skills/wills):"]

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
        tracker_key = '_global_skill_level_tracker'
        if tracker_key not in ctx.shared:
            ctx.shared[tracker_key] = {}
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
            lines.append("")
            lines.append(f"🛑 **MORE PAGES EXIST** (next_offset={next_offset})!")
            lines.append(f"   ⚠️ CRITICAL: The MIN/MAX above is for THIS PAGE ONLY ({len(enriched_data)} employees).")
            lines.append(f"   The GLOBAL minimum/maximum may be DIFFERENT on later pages!")
            lines.append(f"   For 'least/most' queries → MUST paginate until next_offset=-1")
            lines.append("")
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

                for col in filter_cols:
                    global_values = [(emp_id, data.get(col)) for emp_id, data in global_tracker.items() if data.get(col) is not None]
                    if global_values:
                        global_min = min(v[1] for v in global_values)
                        global_max = max(v[1] for v in global_values)
                        global_min_ids = sorted([v[0] for v in global_values if v[1] == global_min])
                        global_max_ids = sorted([v[0] for v in global_values if v[1] == global_max])

                        short_col = col.split(':')[1].replace('skill_', '').replace('will_', '')
                        lines.append(f"  → GLOBAL {short_col} MIN={global_min}: {', '.join(global_min_ids)}")
                        lines.append(f"  → GLOBAL {short_col} MAX={global_max}: {', '.join(global_max_ids)}")

            # Clear tracker for next query
            ctx.shared[tracker_key] = {}

        # AICODE-NOTE: t076 fix - also fetch workload for "least/most busy" queries
        # This allows agent to find busy person without additional time_summary calls
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

        workload_lines = self._enrich_with_workload(ctx, enriched_data, next_offset)

        if workload_lines:
            lines = [""]
            lines.extend(workload_lines)

            # AICODE-NOTE: t009 - Add pagination warning for workload too
            if next_offset > 0:
                lines.append("")
                lines.append(f"🛑 **MORE PAGES EXIST** (next_offset={next_offset})!")
                lines.append(f"   ⚠️ CRITICAL: The LEAST/MOST BUSY above is for THIS PAGE ONLY ({len(enriched_data)} employees).")
                lines.append(f"   The GLOBAL min/max workload may be DIFFERENT on later pages!")
                lines.append(f"   For 'least/most busy' queries → MUST paginate until next_offset=-1")
                lines.append("")
                lines.append(f"   ❌ IGNORE any 'turn budget' warnings! Superlative queries REQUIRE all data!")
                lines.append(f"   ❌ DO NOT RESPOND until next_offset=-1 (all pages fetched)!")
                lines.append(f"   ✅ Continue: employees_search(..., offset={next_offset})")

            print(f"  {CLI_GREEN}✓ Enriched {len(enriched_data)} employees with workload{CLI_CLR}")
            ctx.results.append('\n'.join(lines))

    def _enrich_with_workload(self, ctx: ToolContext, enriched_data: List[dict], next_offset: int = -1) -> List[str]:
        """
        Fetch workload (sum of time_slice from projects) for employees.

        AICODE-NOTE: t076 CRITICAL FIX!
        According to wiki (systems/time_tracking_and_reporting.md):
        "when estimating workload (e.g. who is busiest or non-busiest),
        we rely on workload time slices via Project registry."

        Workload = SUM of time_slice across all ACTIVE projects where employee is a team member.
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

            for emp_id in emp_ids:
                total_time_slice = 0.0

                # Search projects where employee is a team member
                try:
                    proj_search = client.Req_SearchProjects(
                        team=ProjectTeamFilter(employee_id=emp_id),
                        status=['active'],  # Only active projects count toward workload
                        limit=50,
                        offset=0
                    )
                    proj_result = ctx.api.dispatch(proj_search)

                    if proj_result.projects:
                        # For each project, get details to extract time_slice
                        for proj_brief in proj_result.projects:
                            try:
                                proj_detail = ctx.api.dispatch(client.Req_GetProject(id=proj_brief.id))
                                if proj_detail.project and proj_detail.project.team:
                                    for member in proj_detail.project.team:
                                        if member.employee == emp_id:
                                            total_time_slice += member.time_slice
                                            break
                            except Exception:
                                pass  # Skip if can't get project details

                except Exception as e:
                    print(f"  {CLI_YELLOW}⚠ Failed to fetch projects for {emp_id}: {e}{CLI_CLR}")

                workload[emp_id] = total_time_slice

            # Build output lines
            lines = ["", "📊 **WORKLOAD** (sum of time_slice from active projects):"]

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
            if '_global_workload_tracker' not in ctx.shared:
                ctx.shared['_global_workload_tracker'] = {}
            for emp_id, name, ts in workload_list:
                ctx.shared['_global_workload_tracker'][emp_id] = (name, ts)

            # Find min/max workload for THIS PAGE
            if workload_list:
                min_ts = min(w[2] for w in workload_list)
                max_ts = max(w[2] for w in workload_list)
                min_ids = [w[0] for w in workload_list if w[2] == min_ts]
                max_ids = [w[0] for w in workload_list if w[2] == max_ts]

                lines.append(f"  → LEAST BUSY ({min_ts:.2f} FTE): {', '.join(min_ids)}")
                lines.append(f"  → MOST BUSY ({max_ts:.2f} FTE): {', '.join(max_ids)}")

            # AICODE-NOTE: t009 critical fix! On LAST PAGE, show GLOBAL summary
            if next_offset == -1 and ctx.shared.get('_global_workload_tracker'):
                global_tracker = ctx.shared['_global_workload_tracker']
                if len(global_tracker) > len(workload_list):
                    all_workloads = [(emp_id, data[0], data[1]) for emp_id, data in global_tracker.items()]
                    global_min = min(w[2] for w in all_workloads)
                    global_max = max(w[2] for w in all_workloads)
                    global_min_ids = sorted([w[0] for w in all_workloads if w[2] == global_min])
                    global_max_ids = sorted([w[0] for w in all_workloads if w[2] == global_max])

                    lines.append("")
                    lines.append(f"📊 **GLOBAL SUMMARY** (all {len(global_tracker)} employees across all pages):")
                    lines.append(f"  → GLOBAL LEAST BUSY ({global_min:.2f} FTE): {', '.join(global_min_ids)}")
                    lines.append(f"  → GLOBAL MOST BUSY ({global_max:.2f} FTE): {', '.join(global_max_ids)}")

                    # AICODE-NOTE: t009/t075 FIX - When workload is tied, provide additional data for tie-breaker
                    # Let agent decide based on task wording (singular vs plural)
                    if global_min == global_max and len(global_min_ids) > 1:
                        lines.append("")
                        lines.append(f"⚠️ **TIE: {len(global_min_ids)} EMPLOYEES HAVE SAME WORKLOAD** ({global_min:.2f} FTE)")
                        lines.append(f"   Tied employee IDs: {', '.join(global_min_ids)}")

                        # AICODE-NOTE: t075 FIX - Fetch logged hours for tie-breaker
                        # Task may say "pick the one with more project work" which means logged hours
                        task_text = ctx.shared.get('task', {})
                        if hasattr(task_text, 'task'):
                            task_text = task_text.task.lower() if hasattr(task_text.task, 'lower') else str(task_text.task).lower()
                        else:
                            task_text = str(task_text).lower()

                        # AICODE-NOTE: t009/t075 FIX - For "most busy" or "project work" tie-breaker,
                        # fetch logged hours to determine who has done more work
                        is_busy_query = 'busy' in task_text or 'busiest' in task_text
                        is_project_work = 'project work' in task_text or 'more work' in task_text

                        if is_busy_query or is_project_work:
                            # Fetch logged hours for tied employees
                            lines.append("")
                            lines.append("📊 **LOGGED HOURS** (for tie-breaker):")
                            logged_hours = {}
                            for emp_id in (global_max_ids if is_busy_query else global_min_ids)[:10]:
                                try:
                                    time_result = ctx.api.dispatch(client.Req_TimeSummaryByEmployee(employee=emp_id))
                                    if hasattr(time_result, 'total_hours'):
                                        logged_hours[emp_id] = time_result.total_hours
                                    else:
                                        logged_hours[emp_id] = 0
                                except:
                                    logged_hours[emp_id] = 0

                            # Sort by logged hours (descending for most busy)
                            sorted_by_hours = sorted(logged_hours.items(), key=lambda x: (-x[1], x[0]))
                            for emp_id, hours in sorted_by_hours:
                                name = next((data[0] for eid, data in global_tracker.items() if eid == emp_id), emp_id)
                                lines.append(f"  • {name} ({emp_id}): {hours:.1f} hours")

                            if sorted_by_hours:
                                max_hours = sorted_by_hours[0][1]
                                max_hours_ids = [e[0] for e in sorted_by_hours if e[1] == max_hours]
                                if is_busy_query:
                                    lines.append(f"  → MOST BUSY (by logged hours): {', '.join(max_hours_ids)} ({max_hours:.1f} hours)")
                                else:
                                    lines.append(f"  → MOST PROJECT WORK: {', '.join(max_hours_ids)} ({max_hours:.1f} hours)")
                        else:
                            lines.append(f"   If task asks for singular (one person), use tie-breaker from wiki/task.")
                            lines.append(f"   If task asks for plural (all/list), include all tied employees.")

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
        1. Fetch available skills from system
        2. Fetch available wills from system
        3. Return combined hint with both lists for agent to retry
        """
        print(f"  {CLI_BLUE}🔍 Smart Search: Combined skills+wills adaptive hint{CLI_CLR}")

        # Fetch available skills and wills from system
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
        for dept in available_departments:
            dept_lower = dept.lower()
            # Check for partial matches
            if searched_lower in dept_lower or dept_lower in searched_lower:
                potential_matches.append(dept)
            # Check for common abbreviations
            elif searched_lower == 'hr' and 'human' in dept_lower:
                potential_matches.append(dept)
            elif searched_lower == 'human resources' and 'hr' in dept_lower:
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
