"""
Pagination Guards - enforce pagination completeness.

Guards:
- PaginationEnforcementMiddleware: Blocks analysis tools when pagination is incomplete
- ProjectSearchOffsetGuard: Validates sequential offsets for projects_search
- CustomerContactPaginationMiddleware: Blocks customers_get when customers_list incomplete
- CoachingTimeoutGuard: Forces respond on last turns for coaching queries
"""
import re
from typing import Set
from erc3.erc3 import client
from ..base import Middleware, get_task_text
from ...base import ToolContext
from utils import CLI_YELLOW, CLI_CLR


class PaginationEnforcementMiddleware(Middleware):
    """
    Blocks switching to analysis tools (projects_search, time_summary) 
    when employee pagination is incomplete for SUPERLATIVE queries.

    Problem (t075): Agent finds some employees (page 1), then immediately switches
    to projects_search to check their workload/projects for tie-breaking,
    IGNORING the fact that more employees exist on page 2+ who might be better candidates.

    Solution:
    1. Detect superlative queries (least/most skilled/busy).
    2. Detect if `employees_search` pagination is pending.
    3. Block analysis tools if pending.
    """

    # Tools used for analysis/tie-breaking that should be blocked
    # AICODE-NOTE: t075 fix - also block 'respond' to prevent premature answers
    # when pagination is incomplete for superlative queries
    ANALYSIS_TOOLS = {
        'projects_search',
        'projects_get',
        'time_summary_employee',
        'time_summary_project',
        'time_search',
        'respond',  # Block final response until pagination complete
        'answer',   # Alias for respond
        'reply',    # Alias for respond
    }

    # Superlatives MUST be exhaustive
    SUPERLATIVE_KEYWORDS = [
        r'\bmost\s+(?:busy|skilled|experienced|likely|interested)\b',
        r'\bleast\s+(?:busy|skilled|experienced|likely|interested)\b',
        r'\bbusiest\b',
        r'\bbest\b',
        r'\btop\s+\d+\b',
        r'\bfind\s+(?:the\s+)?(?:employee|person)\s+who\s+(?:is|has)\b',
    ]

    def __init__(self):
        self._superlative_re = re.compile('|'.join(self.SUPERLATIVE_KEYWORDS), re.IGNORECASE)

    def process(self, ctx: ToolContext) -> None:
        # Check tool type - we intercept analysis tools
        tool_name = ctx.raw_action.get('tool', '')
        if tool_name not in self.ANALYSIS_TOOLS:
            return

        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Check if task is superlative
        if not self._superlative_re.search(task_text):
            return

        # AICODE-NOTE: t076/t075 CRITICAL FIX!
        # On last turn, allow best-effort response instead of blocking.
        # Blocking on last turn causes "agent should provide 1 response, found 0" failures.
        current_turn = ctx.shared.get('current_turn', 0)
        max_turns = ctx.shared.get('max_turns', 20)
        remaining_turns = max_turns - current_turn - 1
        if remaining_turns <= 1:
            # Last turn - let the agent respond with what it has
            from utils import CLI_GREEN, CLI_CLR
            print(f"  {CLI_GREEN}PaginationEnforcement: Last turn - allowing best-effort response{CLI_CLR}")
            return

        # Check for pending employee pagination
        # AICODE-NOTE: t075 fix — pipeline.py uses type(ctx.model).__name__ as key (e.g., 'Req_SearchEmployees')
        # not the tool name 'employees_search'. Check both for robustness.
        pending = ctx.shared.get('pending_pagination', {})
        emp_pending = pending.get('Req_SearchEmployees') or pending.get('employees_search')

        if not emp_pending:
            return

        next_off = emp_pending.get('next_offset', 0)
        current_count = emp_pending.get('current_count', 0)

        # Only block if there are actually more pages
        if next_off > 0:
            # AICODE-NOTE: Different message for respond vs analysis tools
            if tool_name in ('respond', 'answer', 'reply'):
                block_msg = (
                    f"⛔ PREMATURE RESPONSE: You cannot answer this superlative query yet!\n\n"
                    f"**Problem**: You haven't fetched ALL employees.\n"
                    f"  • employees_search: fetched {current_count}, next_offset={next_off}\n\n"
                    f"**Why this matters for 'least skilled'/'most busy' queries:**\n"
                    f"  The TRUE minimum/maximum might be on a page you haven't fetched yet!\n"
                    f"  If you answer now, you might pick the wrong person.\n\n"
                    f"**REQUIRED**: Continue pagination until `next_offset=-1` (no more pages).\n"
                    f"  → Use: employees_search(..., offset={next_off})\n\n"
                    f"**THEN**: The GLOBAL SUMMARY will show you:\n"
                    f"  • Which employees truly have the MIN/MAX level\n"
                    f"  • The TIE-BREAKER (most project work) if multiple match\n"
                    f"  • The correct answer to respond with"
                )
                # AICODE-NOTE: t076 FIX - For most/least queries, respond must be HARD blocked
                # until next_offset == -1 (except on the last turn safety escape above).
                print(f"  {CLI_YELLOW}🛑 PaginationEnforcement: HARD blocking respond until employee pagination complete{CLI_CLR}")
                ctx.stop_execution = True
                ctx.results.append(block_msg)
                return
            else:
                block_msg = (
                    f"⛔ PREMATURE ANALYSIS: You are switching to `{tool_name}` to analyze candidates, "
                    f"but you haven't finished finding ALL employees yet!\n\n"
                    f"**Pending Pagination:**\n"
                    f"  • employees_search: fetched {current_count}, next_offset={next_off}\n\n"
                    f"**REQUIRED**: Finish fetching ALL employees first (until `next_offset=-1`).\n"
                    f"For superlative queries ('most/least'), you cannot identify the correct candidates "
                    f"to analyze until you have the FULL list.\n\n"
                    f"Continue with `employees_search(..., offset={next_off})`."
                )

            self._soft_block(
                ctx,
                warning_key='pagination_enforcement_warned',
                log_msg=f"PaginationEnforcement: Blocking {tool_name} due to incomplete employee search",
                block_msg=block_msg
            )

    def _soft_block(self, ctx: ToolContext, warning_key: str, log_msg: str, block_msg: str) -> bool:
        """
        Block first time, allow on repeat.
        """
        if ctx.shared.get(warning_key):
            # If already warned, allow through (maybe they know what they're doing)
            return False

        print(f"  {CLI_YELLOW}🛑 {log_msg}{CLI_CLR}")
        ctx.shared[warning_key] = True
        ctx.stop_execution = True
        ctx.results.append(block_msg)
        return True


class ProjectSearchOffsetGuard(Middleware):
    """
    AICODE-NOTE: t069 FIX - Validates projects_search offset is sequential.

    Problem: LLM sometimes uses offset=0, 50, 100 instead of 0, 5, 10, 15...
    This skips most results and causes failures in exhaustive queries.

    Solution: Track last successful offset and block non-sequential offsets.
    """

    # Keywords indicating exhaustive project query
    EXHAUSTIVE_KEYWORDS = [
        'every lead', 'all leads', 'every project', 'all projects',
        'for each lead', 'for each project', 'create wiki',
        'each project lead', 'team leads across'
    ]
    PAGE_SIZE = 5  # API page size for projects

    def process(self, ctx: ToolContext) -> None:
        tool_name = ctx.raw_action.get('tool', '')
        if tool_name != 'projects_search':
            return

        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Only enforce for exhaustive queries
        task_lower = task_text.lower()
        is_exhaustive = any(kw in task_lower for kw in self.EXHAUSTIVE_KEYWORDS)
        if not is_exhaustive:
            return

        # Get requested offset
        args = ctx.raw_action.get('args', {})
        requested_offset = args.get('offset', 0)

        # Get last known next_offset from pending_pagination
        pending = ctx.shared.get('pending_pagination', {})
        proj_pending = pending.get('Req_SearchProjects') or pending.get('projects_search')

        if proj_pending:
            expected_offset = proj_pending.get('next_offset', 0)
            # If expected_offset is 0 or -1, pagination is complete - allow any offset
            if expected_offset > 0 and requested_offset != expected_offset:
                # Check if offset skips pages
                if requested_offset > expected_offset:
                    print(f"  {CLI_YELLOW}🛑 ProjectSearchOffsetGuard: offset={requested_offset} but expected {expected_offset}{CLI_CLR}")
                    ctx.stop_execution = True
                    ctx.results.append(
                        f"⛔ WRONG OFFSET: You requested offset={requested_offset} but the next page starts at offset={expected_offset}!\n\n"
                        f"**API page_size is 5** — offsets must be: 0, 5, 10, 15, 20...\n"
                        f"You're skipping pages {expected_offset} to {requested_offset - self.PAGE_SIZE}!\n\n"
                        f"**CORRECT**: Use offset={expected_offset} for the next page.\n"
                        f"**OR**: Batch multiple sequential offsets in one action_queue:\n"
                        f"  offset={expected_offset}, {expected_offset + 5}, {expected_offset + 10}..."
                    )
                    return

        # For first call (offset=0), just let it through
        # The response will set pending_pagination for future calls


class CustomerContactPaginationMiddleware(Middleware):
    """
    Blocks customers_get when customers_list pagination is incomplete
    for contact email searches.

    Problem (t087): Agent is asked for contact email of "Erik Larsen".
    Agent does customers_list (gets 15 customers), then immediately starts
    doing customers_get for those 15, IGNORING next_offset=15 which means
    more customers exist. Erik Larsen is on page 2.

    Solution:
    1. Detect contact email queries.
    2. Block customers_get if customers_list pagination is pending.
    3. Force agent to finish customers_list pagination first.
    """

    # AICODE-NOTE: t087 fix - detect contact email search patterns
    CONTACT_EMAIL_PATTERNS = [
        r'contact\s+email',
        r'email\s+(?:of|for|address)',
        r"(?:what|give|find|get).*email.*(?:of|for)",
    ]

    def __init__(self):
        self._contact_email_re = re.compile(
            '|'.join(self.CONTACT_EMAIL_PATTERNS), re.IGNORECASE
        )

    def process(self, ctx: ToolContext) -> None:
        tool_name = ctx.raw_action.get('tool', '')

        # Only intercept customers_get when searching for contacts
        if tool_name != 'customers_get':
            return

        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Check if task asks for contact email
        if not self._contact_email_re.search(task_text):
            return

        # Check for pending customer pagination
        pending = ctx.shared.get('pending_pagination', {})
        cust_pending = pending.get('Req_ListCustomers') or pending.get('customers_list')

        if not cust_pending:
            return

        next_off = cust_pending.get('next_offset', 0)
        current_count = cust_pending.get('current_count', 0)

        # Only block if there are actually more pages
        if next_off > 0:
            # AICODE-NOTE: t087 FIX - Do NOT block customers_get.
            # Blocking here caused the agent to defer all customers_get into a huge batch,
            # increasing malformed JSON risk and wasting turns. We instead enforce correctness
            # at response time (see contact-email response guards).
            if not ctx.shared.get('customer_pagination_hint_shown'):
                ctx.shared['customer_pagination_hint_shown'] = True
                hint_msg = (
                    f"💡 CONTACT SEARCH NOTE: customers_list has MORE pages (next_offset={next_off}).\n"
                    f"You MAY start checking customers_get for already listed customers, but:\n"
                    f"  - Do NOT conclude 'not found' until customers_list pagination reaches next_offset=-1\n"
                    f"  - Ensure you also check customers from later pages"
                )
                ctx.results.append(hint_msg)

    def _soft_block(self, ctx: ToolContext, warning_key: str, log_msg: str, block_msg: str) -> bool:
        """Block first time, allow on repeat."""
        if ctx.shared.get(warning_key):
            return False

        print(f"  {CLI_YELLOW}🛑 {log_msg}{CLI_CLR}")
        ctx.shared[warning_key] = True
        ctx.stop_execution = True
        ctx.results.append(block_msg)
        return True


class CoachingTimeoutGuard(Middleware):
    """
    AICODE-NOTE: t077 FIX - Forces respond on last turns for coaching queries.

    Problem: Agent gets coaching query (find coaches for X), starts searching
    for employees with skill level >= 7 across 15 skills. This requires many
    pagination calls. Agent runs out of turns doing pagination and never responds.

    Root cause: LLM sometimes generates malformed JSON with 15 action items,
    parser fails, action_queue becomes empty, agent wastes turn(s), then
    restarts pagination from scratch. By turn 17-20, still paginating.

    Solution: Multi-stage intervention:
    1. Remaining <= 5 turns: WARN that time is running low
    2. Remaining <= 3 turns AND have some coaches: HARD BLOCK employees_search
    3. Remaining <= 1 turn: Force best-effort respond

    This is a HARD block (no repeat allowed) because on last turns we MUST respond.
    """

    COACHING_PATTERNS = [
        r'\bcoach(?:es|ing)?\b',  # AICODE-NOTE: t077 FIX - Match "coach", "coaches", "coaching"
        r'\bmentor(?:s|ing)?\b',   # Match "mentor", "mentors", "mentoring"
        r'\bupskill(?:ing)?\b',    # Match "upskill", "upskilling"
        r'\bimprove\s+(?:his|her|their)?\s*skills?\b',
    ]

    # Turn thresholds for progressive intervention
    WARN_THRESHOLD = 5      # Start warning at 5 turns remaining
    SOFT_BLOCK_THRESHOLD = 3  # Soft block at 3 turns remaining
    HARD_BLOCK_THRESHOLD = 2  # Hard block at 2 turns remaining

    def __init__(self):
        self._coaching_re = re.compile(
            '|'.join(self.COACHING_PATTERNS), re.IGNORECASE
        )

    def process(self, ctx: ToolContext) -> None:
        tool_name = ctx.raw_action.get('tool', '')

        # Only intercept employees_search when doing coaching
        if tool_name != 'employees_search':
            return

        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Check if this is a coaching query
        if not self._coaching_re.search(task_text):
            return

        # Check turn budget
        current_turn = ctx.shared.get('current_turn', 0)
        max_turns = ctx.shared.get('max_turns', 20)
        remaining_turns = max_turns - current_turn - 1

        # Check if we have coaching search results
        coaching_results = ctx.shared.get('coaching_skill_search_results', 0)
        query_subject_ids = ctx.shared.get('query_subject_ids', set())
        coachee_list = ', '.join(sorted(query_subject_ids)[:3]) if query_subject_ids else '(unknown)'

        # AICODE-NOTE: t077 FIX - Progressive intervention for coaching queries
        # Stage 1: Warning at 5 turns remaining
        if remaining_turns <= self.WARN_THRESHOLD and remaining_turns > self.SOFT_BLOCK_THRESHOLD:
            # Only warn once
            warn_key = 'coaching_turn_budget_warned'
            if not ctx.shared.get(warn_key):
                ctx.shared[warn_key] = True
                # Don't block, just add warning hint
                ctx.results.append(
                    f"⚠️ COACHING QUERY: Only {remaining_turns} turns remaining!\n\n"
                    f"**Current progress**: {coaching_results}+ potential coaches found.\n"
                    f"**Coachee**: {coachee_list}\n\n"
                    f"Consider wrapping up soon. If you have enough coaches (3-5+), "
                    f"you can start preparing your response."
                )
            return  # Don't block yet

        # Stage 2: Soft block at 3 turns remaining (if we have results)
        if remaining_turns <= self.SOFT_BLOCK_THRESHOLD and remaining_turns > self.HARD_BLOCK_THRESHOLD:
            # Only block if we have some results
            if coaching_results >= 3:
                from utils import CLI_RED
                print(f"  {CLI_RED}🛑 CoachingTimeoutGuard: Soft blocking search at {remaining_turns} turns{CLI_CLR}")
                ctx.stop_execution = True
                ctx.results.append(
                    f"⚠️ TIME RUNNING LOW - PREPARE RESPONSE!\n\n"
                    f"You have {remaining_turns} turns left and {coaching_results}+ potential coaches found.\n\n"
                    f"**Coachee**: {coachee_list}\n\n"
                    f"**RECOMMENDED**: Stop searching for more coaches and prepare your `respond`.\n"
                    f"You have ENOUGH data to provide a useful answer.\n\n"
                    f"Use `respond` with:\n"
                    f"  • outcome: \"ok_answer\"\n"
                    f"  • message: List the coaches you found\n"
                    f"  • links: Include ALL employee IDs with level >= 7"
                )
                return
            # Not enough results - add warning but allow search
            ctx.results.append(
                f"⚠️ LOW TURN BUDGET: Only {remaining_turns} turns left, {coaching_results} coaches found.\n"
                f"Continue searching but prepare to respond soon!"
            )
            return

        # Stage 3: Hard block at 2 turns or less
        if remaining_turns <= self.HARD_BLOCK_THRESHOLD:
            # HARD BLOCK - force respond now regardless of results
            from utils import CLI_RED
            print(f"  {CLI_RED}🛑 CoachingTimeoutGuard: HARD blocking search - must respond now!{CLI_CLR}")
            ctx.stop_execution = True
            ctx.results.append(
                f"⛔ TIME LIMIT REACHED - RESPOND NOW!\n\n"
                f"You have only {remaining_turns} turns left. You MUST respond immediately.\n\n"
                f"**Data collected so far:**\n"
                f"  • Coachee: {coachee_list}\n"
                f"  • Potential coaches found: {coaching_results}+\n\n"
                f"**REQUIRED ACTION:**\n"
                f"Use `respond` tool NOW with:\n"
                f"  • outcome: \"ok_answer\"\n"
                f"  • message: List all employees found as potential coaches\n"
                f"  • links: Include ALL employee IDs you found with level >= 7\n\n"
                f"⚠️ DO NOT call employees_search again. Respond with what you have!"
            )


class SkillExtremaTimeoutGuard(Middleware):
    """
    AICODE-NOTE: t075 FIX - Forces respond on last turns for skill extrema queries.

    Problem: Agent gets query like "find least skilled person in English".
    Agent starts paginating through ALL employees with skill_english, looking
    for the minimum level. This can take 15+ turns. Agent runs out of turns
    doing pagination and never responds.

    Root cause: Unlike coaching (where we need level >= 7), extrema queries
    need to find MIN/MAX across ALL employees. But after ~10 turns of pagination,
    agent likely has enough data to answer.

    Solution: Multi-stage intervention (similar to CoachingTimeoutGuard):
    1. Remaining <= 5 turns: WARN that time is running low
    2. Remaining <= 3 turns AND have results: SOFT BLOCK employees_search
    3. Remaining <= 2 turns: HARD BLOCK - must respond now
    """

    # AICODE-NOTE: t075 - Patterns for skill extrema queries
    SKILL_EXTREMA_PATTERNS = [
        r'\bleast\s+skilled\b',
        r'\bmost\s+skilled\b',
        r'\blowest\s+(?:skill\s+)?level\b',
        r'\bhighest\s+(?:skill\s+)?level\b',
        r'\bwho\s+has\s+(?:the\s+)?lowest\b',
        r'\bwho\s+has\s+(?:the\s+)?highest\b',
        r'\bminimum\s+(?:skill\s+)?level\b',
        r'\bmaximum\s+(?:skill\s+)?level\b',
    ]

    # Turn thresholds
    WARN_THRESHOLD = 5
    SOFT_BLOCK_THRESHOLD = 3
    HARD_BLOCK_THRESHOLD = 2

    def __init__(self):
        self._skill_extrema_re = re.compile(
            '|'.join(self.SKILL_EXTREMA_PATTERNS), re.IGNORECASE
        )

    def process(self, ctx: ToolContext) -> None:
        tool_name = ctx.raw_action.get('tool', '')

        # Only intercept employees_search
        if tool_name != 'employees_search':
            return

        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Check if this is a skill extrema query
        if not self._skill_extrema_re.search(task_text):
            return

        # Check turn budget
        current_turn = ctx.shared.get('current_turn', 0)
        max_turns = ctx.shared.get('max_turns', 20)
        remaining_turns = max_turns - current_turn - 1

        # Track skill search progress
        skill_search_count = ctx.shared.get('skill_extrema_search_count', 0)
        ctx.shared['skill_extrema_search_count'] = skill_search_count + 1

        # Stage 1: Warning at 5 turns remaining
        if remaining_turns <= self.WARN_THRESHOLD and remaining_turns > self.SOFT_BLOCK_THRESHOLD:
            warn_key = 'skill_extrema_turn_budget_warned'
            if not ctx.shared.get(warn_key):
                ctx.shared[warn_key] = True
                ctx.results.append(
                    f"⚠️ SKILL EXTREMA QUERY: Only {remaining_turns} turns remaining!\n\n"
                    f"**Search progress**: {skill_search_count}+ employee search calls made.\n\n"
                    f"Consider wrapping up soon. Review the employees you've found so far "
                    f"and pick the one with the lowest/highest level."
                )
            return

        # Stage 2: Soft block at 3 turns remaining
        if remaining_turns <= self.SOFT_BLOCK_THRESHOLD and remaining_turns > self.HARD_BLOCK_THRESHOLD:
            if skill_search_count >= 5:  # Have done some searching
                from utils import CLI_RED
                print(f"  {CLI_RED}🛑 SkillExtremaTimeoutGuard: Soft blocking at {remaining_turns} turns{CLI_CLR}")
                ctx.stop_execution = True
                ctx.results.append(
                    f"⚠️ TIME RUNNING LOW - PREPARE RESPONSE!\n\n"
                    f"You have {remaining_turns} turns left and have done {skill_search_count}+ skill searches.\n\n"
                    f"**RECOMMENDED**: Stop searching and respond with what you have.\n"
                    f"Review the employees found so far and pick the one with the "
                    f"lowest/highest skill level. Apply tie-breaker if needed.\n\n"
                    f"Use `respond` with:\n"
                    f"  • outcome: \"ok_answer\"\n"
                    f"  • message: Name the person with lowest/highest level\n"
                    f"  • links: Include the employee ID"
                )
                return
            ctx.results.append(
                f"⚠️ LOW TURN BUDGET: Only {remaining_turns} turns left.\n"
                f"Prepare to respond soon with the data you have!"
            )
            return

        # Stage 3: Hard block at 2 turns or less
        if remaining_turns <= self.HARD_BLOCK_THRESHOLD:
            from utils import CLI_RED
            print(f"  {CLI_RED}🛑 SkillExtremaTimeoutGuard: HARD blocking - must respond now!{CLI_CLR}")
            ctx.stop_execution = True
            ctx.results.append(
                f"⛔ TIME LIMIT REACHED - RESPOND NOW!\n\n"
                f"You have only {remaining_turns} turns left. You MUST respond immediately.\n\n"
                f"**REQUIRED ACTION:**\n"
                f"Review ALL employees you found so far. Pick the one with the "
                f"lowest/highest skill level. If tie, apply the tie-breaker from the task.\n\n"
                f"Use `respond` tool NOW with:\n"
                f"  • outcome: \"ok_answer\"\n"
                f"  • message: Name the winning employee\n"
                f"  • links: Include the employee ID\n\n"
                f"⚠️ DO NOT call employees_search again. Respond with what you have!"
            )


class EmployeeSearchOffsetGuard(Middleware):
    """
    AICODE-NOTE: t075 FIX - Validates employees_search offset is sequential.

    Problem: LLM sometimes skips offsets (e.g., offset=120, 130 instead of 120, 125).
    This misses employees and causes failures in superlative queries.

    Solution: Track last successful offset and block non-sequential offsets.
    """

    # Superlative keywords that require exhaustive search
    SUPERLATIVE_KEYWORDS = [
        r'\bmost\s+(?:busy|skilled|experienced|likely|interested)\b',
        r'\bleast\s+(?:busy|skilled|experienced|likely|interested)\b',
        r'\bbusiest\b',
        r'\bbest\b',
        r'\btop\s+\d+\b',
    ]
    PAGE_SIZE = 5  # API page size for employees

    def __init__(self):
        self._superlative_re = re.compile('|'.join(self.SUPERLATIVE_KEYWORDS), re.IGNORECASE)

    def process(self, ctx: ToolContext) -> None:
        tool_name = ctx.raw_action.get('tool', '')
        if tool_name != 'employees_search':
            return

        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Only enforce for superlative queries
        if not self._superlative_re.search(task_text):
            return

        # AICODE-NOTE: t075 FIX - On last 2 turns, allow any offset to avoid blocking
        # the agent from finishing pagination and responding.
        current_turn = ctx.shared.get('current_turn', 0)
        max_turns = ctx.shared.get('max_turns', 20)
        remaining_turns = max_turns - current_turn - 1
        if remaining_turns <= 2:
            return

        # Get requested offset
        args = ctx.raw_action.get('args', {})
        requested_offset = args.get('offset', 0)

        # Get last known next_offset from pending_pagination
        pending = ctx.shared.get('pending_pagination', {})
        emp_pending = pending.get('Req_SearchEmployees') or pending.get('employees_search')

        if emp_pending:
            expected_offset = emp_pending.get('next_offset', 0)
            # If expected_offset is 0 or -1, pagination is complete - allow any offset
            if expected_offset > 0 and requested_offset != expected_offset:
                # Check if offset skips pages
                if requested_offset > expected_offset:
                    print(f"  {CLI_YELLOW}🛑 EmployeeSearchOffsetGuard: offset={requested_offset} but expected {expected_offset}{CLI_CLR}")
                    ctx.stop_execution = True
                    ctx.results.append(
                        f"⛔ WRONG OFFSET: You requested offset={requested_offset} but the next page starts at offset={expected_offset}!\n\n"
                        f"**API page_size is 5** — offsets must be: 0, 5, 10, 15, 20...\n"
                        f"You're skipping pages {expected_offset} to {requested_offset - self.PAGE_SIZE}!\n\n"
                        f"**CORRECT**: Use offset={expected_offset} for the next page.\n"
                        f"**OR**: Batch multiple sequential offsets in one action_queue:\n"
                        f"  offset={expected_offset}, {expected_offset + 5}, {expected_offset + 10}..."
                    )
                    return

        # For first call (offset=0), just let it through
        # The response will set pending_pagination for future calls
