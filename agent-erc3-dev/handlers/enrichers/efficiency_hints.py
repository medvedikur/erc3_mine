"""
Efficiency hint enricher for detecting inefficient action patterns.

Detects patterns like:
- Sequential projects_get/employees_get instead of parallel calls
- Excessive pagination when filters would be more efficient
- Missing filter usage when available
- Turn budget exhaustion risk

AICODE-NOTE: This enricher is critical for preventing pagination loops
that exhaust turn budget (see t009, t011, t012 failures).

KEY STRATEGIES:
1. `time_summary_employee(employees=[list])` - accepts MULTIPLE employees in one call!
2. `action_queue` can contain 10-30 parallel calls executed in ONE turn!
3. For "busiest employee" tasks, use batch time_summary, not individual pagination
"""

from typing import Any, Dict, List, Optional, TYPE_CHECKING

import config

if TYPE_CHECKING:
    from ..base import ToolContext


class EfficiencyHintEnricher:
    """
    Provides hints when agent uses inefficient action patterns.

    Tracks action counts and suggests:
    - Parallel calls instead of sequential
    - Filters instead of pagination
    - Batch API calls (time_summary_employee accepts list!)
    - Parallel action_queue execution (10-30 calls per turn)
    """

    # Thresholds for triggering hints
    SEQUENTIAL_GET_THRESHOLD = 2  # After N sequential gets, suggest parallel (lowered)
    PAGINATION_THRESHOLD = 2  # After N pagination calls, suggest alternatives
    CRITICAL_PAGINATION_THRESHOLD = 2  # After N pagination calls, show CRITICAL STOP (lowered from 3 for t076)
    SAMPLE_SIZE_HINT = 15  # Suggest this sample size for "find best" queries

    # Turn budget thresholds
    LOW_BUDGET_TURNS = 5  # Remaining turns to trigger warning
    CRITICAL_BUDGET_TURNS = 3  # Remaining turns to trigger critical warning

    # Actions that benefit from parallel execution
    PARALLELIZABLE_GETS = {'projects_get', 'employees_get', 'customers_get'}

    # Actions that indicate pagination
    PAGINATION_ACTIONS = {'projects_search', 'employees_search', 'customers_search'}

    def __init__(self):
        self._hint_shown: Dict[str, bool] = {}  # Track which hints were shown

    def clear_turn_cache(self) -> None:
        """Clear per-task hint tracking."""
        self._hint_shown.clear()

    def maybe_hint_parallel_calls(
        self,
        ctx: 'ToolContext',
        action_name: str
    ) -> Optional[str]:
        """
        Suggest parallel calls when detecting sequential get operations.

        Args:
            ctx: Tool context with action_counts
            action_name: Current action being executed

        Returns:
            Hint string or None
        """
        if action_name not in self.PARALLELIZABLE_GETS:
            return None

        action_counts = ctx.shared.get('action_counts', {})
        count = action_counts.get(action_name, 0)

        # Only show hint once per action type
        hint_key = f'parallel_{action_name}'
        if self._hint_shown.get(hint_key):
            return None

        if count >= self.SEQUENTIAL_GET_THRESHOLD:
            self._hint_shown[hint_key] = True
            return (
                f"⚡ PARALLEL EXECUTION: You've called `{action_name}` {count} times (1 per turn).\n"
                f"You can execute 10-30 calls in ONE turn by batching in `action_queue`:\n"
                f'```json\n'
                f'"action_queue": [\n'
                f'  {{"tool": "{action_name}", "args": {{"id": "id1"}}}},\n'
                f'  {{"tool": "{action_name}", "args": {{"id": "id2"}}}},\n'
                f'  {{"tool": "{action_name}", "args": {{"id": "id3"}}}},\n'
                f'  // ... up to 20-30 calls\n'
                f']\n'
                f'```\n'
                f"This saves {count - 1} turns! Batch ALL your remaining get calls NOW."
            )

        return None

    # Keywords indicating ALL results are needed (recommendation, list, AND superlative queries)
    # AICODE-NOTE: t075 fix - superlative queries also need ALL results to find the minimum/maximum
    EXHAUSTIVE_QUERY_KEYWORDS = [
        'recommend', 'suggest', 'candidates', 'who would', 'who can', 'who could',
        'list all', 'find all', 'all employees', 'everyone who', 'everyone with',
        # Superlative queries - need ALL to compare
        'least', 'most', 'lowest', 'highest', 'busiest', 'best', 'worst',
        'minimum', 'maximum', 'smallest', 'largest', 'fewest'
    ]

    def maybe_hint_pagination_limit(
        self,
        ctx: 'ToolContext',
        action_name: str,
        task_text: str
    ) -> Optional[str]:
        """
        Suggest efficient approaches when pagination is detected.

        Args:
            ctx: Tool context with action_counts
            action_name: Current action being executed
            task_text: Task instructions

        Returns:
            Hint string or None
        """
        if action_name not in self.PAGINATION_ACTIONS:
            return None

        action_counts = ctx.shared.get('action_counts', {})
        count = action_counts.get(action_name, 0)

        # Get turn budget info
        current_turn = ctx.shared.get('current_turn', 0)
        max_turns = ctx.shared.get('max_turns', config.MAX_TURNS_PER_TASK)
        remaining_turns = max_turns - current_turn - 1

        # Check if this looks like a "find best/most" query
        task_lower = task_text.lower()
        is_superlative_query = any(kw in task_lower for kw in [
            'most', 'least', 'best', 'highest', 'lowest', 'busiest',
            'biggest', 'smallest', 'strongest', 'weakest', 'eager'
        ])

        # Check for list/table queries where sampling is OK
        is_list_query = any(kw in task_lower for kw in [
            'table', 'list all', 'give me a list', 'show all', 'all skills'
        ])

        # AICODE-NOTE: Check if this is a recommendation/exhaustive query that needs ALL results
        is_exhaustive_query = any(kw in task_lower for kw in self.EXHAUSTIVE_QUERY_KEYWORDS)

        # AICODE-NOTE: t076 FIX - Special handling for "busy/workload" queries
        # These need time_summary_employee, not just employee list
        is_workload_query = any(kw in task_lower for kw in [
            'busy', 'busiest', 'workload', 'availability', 'available',
            'free time', 'capacity', 'utilization'
        ])

        # AICODE-NOTE: t076 FIX - For workload queries, suggest BATCH pagination
        # Workload = sum(time_slice) from projects, NOT time_summary_employee!
        # Agent needs ALL employees with the will, then enricher calculates workload automatically.
        if count >= self.CRITICAL_PAGINATION_THRESHOLD and is_workload_query and action_name == 'employees_search':
            items_fetched = count * 5
            next_offsets = [items_fetched + i * 5 for i in range(10)]
            return (
                f"⚠️ WORKLOAD QUERY: You've fetched {items_fetched} items.\n"
                f"**NOTE**: Workload is automatically calculated from projects (time_slice).\n"
                f"To get ALL employees faster, use **⚡ BATCH PAGINATION**:\n"
                f"```json\n"
                f'"action_queue": [\n'
                f'  {{"tool": "{action_name}", "args": {{"...same_filters...", "offset": {next_offsets[0]}}}}},\n'
                f'  {{"tool": "{action_name}", "args": {{"...same_filters...", "offset": {next_offsets[1]}}}}},\n'
                f'  {{"tool": "{action_name}", "args": {{"...same_filters...", "offset": {next_offsets[2]}}}}},\n'
                f'  // ... up to 10-20 calls in ONE turn!\n'
                f']\n'
                f'```\n'
                f"This fetches 50-100 more items in ONE turn!"
            )

        # For exhaustive queries, hint to use PARALLEL pagination
        # AICODE-NOTE: t075/t076 FIX - Agent needs ALL results but should batch pagination calls
        if count >= self.CRITICAL_PAGINATION_THRESHOLD and is_exhaustive_query:
            items_fetched = count * 5
            next_offsets = [items_fetched + i * 5 for i in range(10)]  # Next 10 offsets
            return (
                f"⚠️ SUPERLATIVE QUERY: You've fetched {items_fetched} items.\n"
                f"**⚡ BATCH PAGINATION** — Put MULTIPLE search calls in ONE action_queue:\n"
                f"```json\n"
                f'"action_queue": [\n'
                f'  {{"tool": "{action_name}", "args": {{"...same_filters...", "offset": {next_offsets[0]}}}}},\n'
                f'  {{"tool": "{action_name}", "args": {{"...same_filters...", "offset": {next_offsets[1]}}}}},\n'
                f'  {{"tool": "{action_name}", "args": {{"...same_filters...", "offset": {next_offsets[2]}}}}},\n'
                f'  // ... up to 10-20 calls in ONE turn!\n'
                f']\n'
                f'```\n'
                f"This fetches 50-100 more items in ONE turn instead of 10-20 turns!"
            )

        # CRITICAL STOP for non-exhaustive queries after threshold
        # AICODE-NOTE: Only show stop for superlative/sampling queries, NOT for recommendation queries
        if count >= self.CRITICAL_PAGINATION_THRESHOLD and not is_exhaustive_query:
            items_fetched = count * 5  # Assuming limit=5 per page
            return (
                f"🛑 **CRITICAL: STOP PAGINATING NOW!** ({count} pages = {items_fetched}+ items fetched)\n\n"
                f"You have **{remaining_turns} turns left**. Continuing pagination will exhaust your budget!\n\n"
                f"**ACTION REQUIRED — Choose ONE:**\n"
                f"1. **RESPOND NOW** with data you have (state 'based on {items_fetched}+ sampled records')\n"
                f"2. **Use `time_summary_employee`** with collected IDs for workload analysis\n"
                f"3. **Use SKILL FILTER** `employees_search(skill='X', min_level=N)` for coaching queries\n\n"
                f"**DO NOT** call `{action_name}` with offset={count * 5} — you have ENOUGH data!"
            )

        # Standard pagination warning (show once)
        hint_key = f'pagination_{action_name}'
        if self._hint_shown.get(hint_key):
            return None

        if count >= self.PAGINATION_THRESHOLD:
            self._hint_shown[hint_key] = True

            if action_name == 'employees_search' and is_superlative_query:
                # Special hint for "busiest employee" type queries
                return (
                    f"⚡ EFFICIENT STRATEGY for 'busiest/most' employee queries:\n\n"
                    f"You have {count * 5}+ employees. DON'T paginate through all of them!\n\n"
                    f"**OPTION 1 - BATCH time_summary (BEST)**:\n"
                    f"```json\n"
                    f'{{"tool": "time_summary_employee", "args": {{"employees": ["emp1", "emp2", "emp3", ...]}}}}\n'
                    f"```\n"
                    f"Pass ALL employee IDs in ONE call! Returns total hours per employee.\n\n"
                    f"**OPTION 2 - PARALLEL projects_get**:\n"
                    f"Put 10-20 `projects_get` calls in ONE action_queue to get time_slice data.\n\n"
                    f"**STOP paginating employees_search!** Use the IDs you already have."
                )
            elif action_name == 'projects_search':
                return (
                    f"⚡ EFFICIENT STRATEGY for projects:\n\n"
                    f"You have {count * 5}+ projects. Instead of paginating:\n\n"
                    f"**OPTION 1 - Use filters**:\n"
                    f"  - `member=employee_id` — projects where someone works\n"
                    f"  - `owner=employee_id` — projects owned by someone\n"
                    f"  - `customer=customer_id` — projects for a customer\n"
                    f"  - `status=active` — only active projects\n\n"
                    f"**OPTION 2 - BATCH projects_get**:\n"
                    f"If you need details for multiple projects, put 10-20 `projects_get` calls\n"
                    f"in ONE action_queue — they execute in ONE turn!\n\n"
                    f"**STOP paginating!** Work with what you have or use filters."
                )
            else:
                return (
                    f"⚡ EFFICIENCY TIP: You've paginated `{action_name}` {count} times.\n\n"
                    f"**Use FILTERS** to narrow results:\n"
                    f"  - `department=name` — employees in specific department\n"
                    f"  - `location=name` — employees at specific location\n"
                    f"  - `skill=name` — employees with specific skill\n\n"
                    f"**Use BATCH calls** for details:\n"
                    f"Put multiple `employees_get` or `projects_get` in ONE action_queue.\n\n"
                    f"**STOP paginating!** Use the {count * 5}+ results you already have."
                )

        return None

    def maybe_hint_filter_usage(
        self,
        ctx: 'ToolContext',
        action_name: str,
        result: Any
    ) -> Optional[str]:
        """
        Suggest filters when search returns many results.

        Args:
            ctx: Tool context
            action_name: Current action being executed
            result: API response

        Returns:
            Hint string or None
        """
        if action_name not in self.PAGINATION_ACTIONS:
            return None

        # Check if there are more results (pagination available)
        next_offset = getattr(result, 'next_offset', None)
        if next_offset is None or next_offset <= 0:
            return None

        # Only hint once per action type
        hint_key = f'filter_{action_name}'
        if self._hint_shown.get(hint_key):
            return None

        # Get count of results
        items = []
        if hasattr(result, 'projects'):
            items = result.projects or []
        elif hasattr(result, 'employees'):
            items = result.employees or []
        elif hasattr(result, 'customers'):
            items = result.customers or []

        if len(items) >= 5 and next_offset > 0:
            self._hint_shown[hint_key] = True

            filter_suggestions = {
                'projects_search': 'member=, owner=, customer=, status=',
                'employees_search': 'department=, location=, skill=, manager=',
                'customers_search': 'account_managers=, deal_phase=, locations=',
            }

            filters = filter_suggestions.get(action_name, '')
            return (
                f"FILTER TIP: Large result set with more pages. "
                f"Available filters for `{action_name}`: {filters}. "
                f"Using filters is faster than paginating through all results."
            )

        return None

    def get_total_pagination_warning(self, ctx: 'ToolContext') -> Optional[str]:
        """
        Check if total pagination across all search types is excessive.

        AICODE-NOTE: This catches cases where agent paginates employees AND projects,
        each individually below threshold but combined exhausting budget.
        """
        action_counts = ctx.shared.get('action_counts', {})
        total_pagination = sum(
            action_counts.get(action, 0)
            for action in self.PAGINATION_ACTIONS
        )

        current_turn = ctx.shared.get('current_turn', 0)
        max_turns = ctx.shared.get('max_turns', config.MAX_TURNS_PER_TASK)
        remaining_turns = max_turns - current_turn - 1

        hint_key = 'total_pagination_warning'
        if self._hint_shown.get(hint_key):
            return None

        # Warn if total pagination > 50% of turn budget used
        if total_pagination >= 6 and remaining_turns <= 10:
            self._hint_shown[hint_key] = True
            return (
                f"🛑 STOP PAGINATING! ({total_pagination} search calls, {remaining_turns} turns left)\n\n"
                f"**YOU HAVE ENOUGH DATA!** Use efficient strategies:\n\n"
                f"1. **BATCH time_summary_employee**:\n"
                f"   Pass ALL employee IDs in ONE call to get hours/workload.\n\n"
                f"2. **PARALLEL action_queue**:\n"
                f"   Put 10-30 `projects_get` or `employees_get` calls in ONE action_queue.\n"
                f"   They ALL execute in ONE turn!\n\n"
                f"3. **ANALYZE NOW**:\n"
                f"   Calculate busiest/highest from data you have and call `respond`."
            )

        return None

    def get_turn_warning(self, current_turn: int, max_turns: int = None) -> Optional[str]:
        """
        Generate warning when approaching turn limit.

        Args:
            current_turn: Current turn number (0-indexed)
            max_turns: Maximum turns allowed (defaults to config)

        Returns:
            Warning string or None
        """
        if max_turns is None:
            max_turns = config.MAX_TURNS_PER_TASK

        # Convert to remaining (current_turn is 0-indexed)
        remaining = max_turns - current_turn - 1

        if remaining == 5:
            return (
                f"⏱️ TURN BUDGET: {remaining} turns remaining.\n"
                f"If you're still gathering data, start analyzing what you have.\n"
                f"You should have enough data to formulate an answer."
            )
        elif remaining == 3:
            return (
                f"⚠️ TURN WARNING: Only {remaining} turns remaining!\n"
                f"STOP gathering data. ANALYZE what you have and prepare your `respond` action."
            )
        elif remaining == 1:
            return (
                f"🛑 CRITICAL: LAST TURN!\n"
                f"You MUST call `respond` NOW or task will fail with no answer!\n"
                f"Use your best judgment based on data collected so far."
            )

        return None
