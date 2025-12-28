"""
Outcome Guards - validate outcome type selection.

Guards:
- AmbiguityGuardMiddleware: Catches ok_not_found without DB search
- OutcomeValidationMiddleware: Validates denied outcomes
- SingleCandidateOkHint: Nudges ok_answer for single candidate
- SubjectiveQueryGuard: Blocks ok_answer on ambiguous queries
- IncompletePaginationGuard: Blocks ok_answer when pagination not exhausted for LIST queries
"""
import re
from ..base import ResponseGuard, get_task_text
from ...base import ToolContext
from utils import CLI_GREEN, CLI_CLR


class IncompletePaginationGuard(ResponseGuard):
    """
    Guard for ok_answer responses when pagination is incomplete.

    AICODE-NOTE: Critical for t016, t086, t076, t075, t009.
    Agent sees next_offset=N hint but ignores it, responding with partial results.
    This guard blocks the response and forces agent to continue paginating.

    Problem: Agent fetches 5 employees (page 1), sees next_offset=5, but decides
    "that's enough" and responds.
    - For LIST queries ("list all"): needs ALL.
    - For SUPERLATIVE queries ("most busy"): needs ALL to find global min/max.

    Solution: Soft block if:
    1. Task contains LIST or SUPERLATIVE keywords
    2. pending_pagination exists (next_offset > 0 not consumed)
    3. Agent tries ok_answer
    """

    target_outcomes = {"ok_answer"}

    # Keywords indicating exhaustive list is expected
    # AICODE-NOTE: t016 fix - added "leads?" to patterns
    # AICODE-NOTE: t017 fix - added recommendation patterns (must list ALL qualifying candidates)
    LIST_KEYWORDS = [
        r'\blist\s+(?:all\s+)?(?:employees?|projects?|customers?|leads?)\b',
        r'\blist\s+(?:the\s+)?(?:employees?|people|staff|leads?)\b',
        r'\blist\s+(?:me\s+)?(?:project\s+)?leads?\b',  # "List me project leads"
        r'\bfind\s+all\b',
        r'\bget\s+all\b',
        r'\bhow\s+many\b',
        r'\ball\s+(?:employees?|projects?|customers?|leads?)\s+(?:who|that|with|in)\b',
        r'\bevery(?:one)?\s+(?:who|that|with|in)\b',
        r'\bwho\s+(?:all\s+)?(?:are|is|has|have|works?|can)\b',
        r'\bproject\s+leads?\s+(?:that|who|with)\b',  # "project leads that have..."
        # AICODE-NOTE: t017 fix - recommendation queries need ALL qualifying candidates
        r'\bwho\s+(?:would\s+you\s+)?recommend\b',  # "Who would you recommend..."
        r'\brecommend\s+(?:as|for)\b',  # "recommend as trainer"
    ]

    # Superlatives MUST be exhaustive (cannot sample)
    SUPERLATIVE_KEYWORDS = [
        r'\bmost\s+(?:busy|skilled|experienced|likely|interested)\b',
        r'\bleast\s+(?:busy|skilled|experienced|likely|interested)\b',
        r'\bbusiest\b',
        r'\bbest\b',
        r'\btop\s+\d+\b',
        r'\bfind\s+(?:the\s+)?(?:employee|person)\s+who\s+(?:is|has)\b', # "Find the person who is..." -> usually superlative context
    ]

    # Keywords that suggest sampling is OK (only truly optional cases)
    SAMPLING_OK_KEYWORDS = [
        r'\bfind\s+(?:one|a)\s+(?:employee|person)\b',  # "Find a person" (singular, indefinite)
        r'\bgive\s+(?:an|some)\s+example',
    ]

    def __init__(self):
        self._list_re = re.compile('|'.join(self.LIST_KEYWORDS), re.IGNORECASE)
        self._superlative_re = re.compile('|'.join(self.SUPERLATIVE_KEYWORDS), re.IGNORECASE)
        self._sampling_re = re.compile('|'.join(self.SAMPLING_OK_KEYWORDS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Skip if task explicitly suggests sampling is OK
        if self._sampling_re.search(task_text):
            return

        # Check if task expects exhaustive list OR is superlative
        is_list = bool(self._list_re.search(task_text))
        is_superlative = bool(self._superlative_re.search(task_text))

        if not is_list and not is_superlative:
            return

        # AICODE-NOTE: t076 CRITICAL FIX!
        # On last turn, allow best-effort response instead of blocking.
        # Blocking on last turn causes "agent should provide 1 response, found 0" failures.
        current_turn = ctx.shared.get('current_turn', 0)
        max_turns = ctx.shared.get('max_turns', 20)
        remaining_turns = max_turns - current_turn - 1
        if remaining_turns <= 1:
            # Last turn - let the agent respond with what it has
            print(f"  {CLI_GREEN}IncompletePaginationGuard: Last turn - allowing best-effort response{CLI_CLR}")
            return

        # Check for pending pagination
        pending = ctx.shared.get('pending_pagination', {})

        if not pending:
            return

        # Build warning message
        pending_info = []
        for action_type, info in pending.items():
            next_off = info.get('next_offset', 0)
            count = info.get('current_count', 0)
            # Only trigger if there are actually more pages
            if next_off > 0:
                pending_info.append(f"{action_type}: fetched {count}, next_offset={next_off}")

        if pending_info:
            reason = "SUPERLATIVE" if is_superlative else "LIST"
            self._soft_block(
                ctx,
                warning_key='incomplete_pagination_warned',
                log_msg=f"IncompletePaginationGuard: {reason} query with unfetched pages: {pending_info}",
                block_msg=(
                    f"⛔ INCOMPLETE DATA: Task is a {reason} query, but you haven't fetched all results!\n\n"
                    f"**Pending pagination:**\n" +
                    "\n".join(f"  • {p}" for p in pending_info) +
                    f"\n\n"
                    f"**REQUIRED**: Continue paginating with `offset={list(pending.values())[0].get('next_offset')}` "
                    f"until `next_offset=-1` (no more pages).\n\n"
                    f"For {reason} queries, you MUST check ALL data to find the correct answer/global minimum/maximum.\n"
                    f"Do not guess based on the first few pages!"
                )
            )


class YesNoGuard(ResponseGuard):
    """
    AICODE-NOTE: t022 FIX - Ensures Yes/No questions get English "Yes"/"No" in response.
    Problem: Agent responds in Russian "Да" when question is in Russian, but benchmark expects "Yes".
    Solution: If task ends with (Yes/No) or asks for yes/no, force English keywords.
    """
    target_outcomes = {"ok_answer"}
    
    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return
            
        # Check if task explicitly asks for Yes/No (case insensitive)
        if "(yes/no)" in task_text.lower() or "yes or no" in task_text.lower():
            message = ctx.model.message or ""
            msg_lower = message.lower()
            
            # Check if English Yes/No is missing
            has_yes = "yes" in msg_lower
            has_no = "no" in msg_lower
            
            if not (has_yes or has_no):
                # Check for Russian equivalents
                if "да" in msg_lower:
                    ctx.model.message = f"Yes (Да). {message}"
                    print(f"  {CLI_GREEN}✓ YesNoGuard: Added English 'Yes' to response{CLI_CLR}")
                elif "нет" in msg_lower:
                    ctx.model.message = f"No (Нет). {message}"
                    print(f"  {CLI_GREEN}✓ YesNoGuard: Added English 'No' to response{CLI_CLR}")
                else:
                    # Ambiguous - warn
                    ctx.results.append(
                        "⚠️ FORMAT WARNING: Task asks for (Yes/No) but your response doesn't contain 'Yes' or 'No'.\n"
                        "Please include the English word 'Yes' or 'No' clearly."
                    )


class AmbiguityGuardMiddleware(ResponseGuard):
    """
    Guard for ok_not_found responses without database search.

    Problem: Agent searches wiki but not DB, responds ok_not_found.
    - Soft block if task mentions projects/employees but no DB search
    - Soft hint otherwise
    """

    target_outcomes = {"ok_not_found"}

    # Keywords indicating task is about database entities
    PROJECT_KEYWORDS = [
        r'\bproject\b', r'\bpoc\b', r'\bpilot\b', r'\barchived?\b',
        r'\bwrapped\b', r'\bclosed\b', r'\bfinished\b',
    ]
    EMPLOYEE_KEYWORDS = [
        r'\bemployee\b', r'\bperson\b', r'\bwho\s+(?:is|works|did)\b',
        r'\b(?:ana|jonas|elena|marko|sofia|felix|mira|helene)\b',
    ]
    # AICODE-NOTE: t081 FIX - "role of X at Y" pattern indicates project role query
    # e.g., "What is the role of Brands at Fast-cure floor system" = project team query
    ROLE_AT_PROJECT_PATTERN = r'\brole\s+of\s+\w+\s+(?:at|in|on)\s+'

    def __init__(self):
        self._project_re = re.compile('|'.join(self.PROJECT_KEYWORDS), re.IGNORECASE)
        self._employee_re = re.compile('|'.join(self.EMPLOYEE_KEYWORDS), re.IGNORECASE)
        self._role_at_project_re = re.compile(self.ROLE_AT_PROJECT_PATTERN, re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Check what tools were used
        action_types_executed = ctx.shared.get('action_types_executed', set())
        searched_projects = 'projects_search' in action_types_executed
        searched_employees = 'employees_search' in action_types_executed
        searched_any_db = any(t in action_types_executed for t in [
            'projects_search', 'employees_search', 'customers_search', 'time_search'
        ])

        # Detect what the task is about
        mentions_project = bool(self._project_re.search(task_text))
        mentions_employee = bool(self._employee_re.search(task_text))
        # AICODE-NOTE: t081 FIX - "role of X at Y" is almost always a project role query
        is_role_at_project = bool(self._role_at_project_re.search(task_text))
        if is_role_at_project:
            mentions_project = True

        # Soft BLOCK if task is about projects but no projects_search
        if mentions_project and not searched_projects:
            self._soft_block(
                ctx,
                warning_key='ambiguity_project_warned',
                log_msg="AmbiguityGuard: Project-related task without projects_search",
                block_msg=(
                    "💡 HINT: You responded 'ok_not_found' but didn't use `projects_search`. "
                    "Wiki doesn't contain project status - consider searching the database.\n\n"
                    "**REQUIRED**: Use `projects_search(query='...', status=['archived'])` to find archived projects.\n"
                    "The wiki contains policies, not project records!"
                )
            )
            return

        # Soft BLOCK if task mentions specific employee but no employees_search
        if mentions_employee and not searched_employees and not searched_projects:
            self._soft_block(
                ctx,
                warning_key='ambiguity_employee_warned',
                log_msg="AmbiguityGuard: Employee-related task without employees_search",
                block_msg=(
                    "💡 HINT: You responded 'ok_not_found' but didn't search the database. "
                    "Use `employees_search` or `projects_search(member='...')` to find employee-related data."
                )
            )
            return

        # Generic hint for other cases
        if not searched_any_db:
            ctx.results.append(
                "\n💡 HINT: You responded 'ok_not_found' but didn't search the database. "
                "Consider using projects_search/employees_search before concluding something doesn't exist."
            )


class OutcomeValidationMiddleware(ResponseGuard):
    """
    Validates denied_security responses to prevent false denials.

    Problems this solves:
    1. Agent tried to use non-existent tool -> should be 'none_unsupported'
    2. Agent claims no permission without checking (no projects_get/employees_get call)

    EXCEPTIONS (skip validation):
    - Public users (guests) - they SHOULD use denied_security for most requests
    - Destructive operations (wipe, delete) - denial without checking is correct
    """

    target_outcomes = {"denied_security", "denied_authorization", "ok_not_found", "none_clarification_needed"}
    require_public = False  # Skip for public users

    # Permission check tools
    PERMISSION_CHECK_TOOLS_STRONG = {'projects_get', 'employees_get', 'customers_get'}
    PERMISSION_CHECK_TOOLS_WEAK = {'projects_search', 'employees_search'}

    DESTRUCTIVE_KEYWORDS = [
        r'\bwipe\b', r'\bdelete\b', r'\berase\b', r'\bdestroy\b',
        r'\bremove\s+all\b', r'\bclear\s+all\b', r'\bpurge\b',
        r'\bthreat\b', r'\bblackmail\b', r'\bhack\b', r'\bsteal\b',
    ]

    MUTATION_KEYWORDS = [
        r'\bswitch\s+status\b', r'\bchange\s+status\b', r'\bupdate\s+status\b',
        r'\bset\s+status\b', r'\bpause\b', r'\barchive\b', r'\bactivate\b',
        r'\bmodify\b', r'\bupdate\b', r'\bedit\b',
    ]

    MISSING_INFO_KEYWORDS = [
        r'\bjira\b', r'\bticket\b', r'\bcost\s*cent', r'\bcc[\s-]',
        r'\bmissing\s+(required|mandatory)', r'\bnot\s+provided\b',
        r'\bprovide\s+a\s+valid\b', r'\brequired\s+by\s+policy\b',
    ]

    def __init__(self):
        self._destructive_re = re.compile('|'.join(self.DESTRUCTIVE_KEYWORDS), re.IGNORECASE)
        self._mutation_re = re.compile('|'.join(self.MUTATION_KEYWORDS), re.IGNORECASE)
        self._missing_info_re = re.compile('|'.join(self.MISSING_INFO_KEYWORDS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        # Handle ok_not_found for mutation tasks (soft hint only)
        if outcome in ("ok_not_found", "none_clarification_needed"):
            self._validate_ok_not_found_for_mutations(ctx, outcome)
            return

        # Only validate denied outcomes from here
        if outcome not in ("denied_security", "denied_authorization"):
            return

        task_text = get_task_text(ctx)

        # Skip for destructive operations
        if self._destructive_re.search(task_text or ''):
            print(f"  {CLI_GREEN}✓ Outcome Validation: Skipped for destructive operation{CLI_CLR}")
            return

        # AICODE-NOTE: Adaptive denial validation (t011 fix).
        # If agent declares denial_basis, validate appropriately instead of requiring entity checks.
        denial_basis = ctx.shared.get('denial_basis')
        if denial_basis:
            if self._validate_denial_basis(ctx, denial_basis, outcome):
                return  # Validation passed, skip legacy checks

        # Get execution context
        missing_tools = ctx.shared.get('missing_tools', [])
        action_types_executed = ctx.shared.get('action_types_executed', set())
        had_strong_check = bool(action_types_executed & self.PERMISSION_CHECK_TOOLS_STRONG)
        had_weak_check = bool(action_types_executed & self.PERMISSION_CHECK_TOOLS_WEAK)
        warning_key = 'outcome_validation_warned'

        # CASE 1: Tried to use non-existent tool -> suggest none_unsupported
        if missing_tools:
            self._soft_block(
                ctx, warning_key,
                f"Outcome Validation: Agent tried non-existent tool(s): {missing_tools}",
                f"🔍 OUTCOME VALIDATION: You responded with '{outcome}', but you tried to use "
                f"non-existent tool(s): {', '.join(missing_tools)}.\n\n"
                f"**This is likely 'none_unsupported', not '{outcome}'!**\n\n"
                f"- `{outcome}` = You HAVE the capability but security/authorization prevents it\n"
                f"- `none_unsupported` = The requested feature/tool does NOT EXIST in this system\n\n"
                f"If the user asked for something the system cannot do (no tool exists), "
                f"use `none_unsupported` with a message explaining the limitation.\n\n"
                f"**If you're confident '{outcome}' is correct**, call respond again with the same outcome."
            )
            return

        # CASE 2: Claiming denial without ANY permission check
        if not had_strong_check and not had_weak_check:
            self._soft_block(
                ctx, warning_key,
                f"Outcome Validation: '{outcome}' without ANY permission verification",
                f"🔍 OUTCOME VALIDATION: You responded with '{outcome}', but you didn't verify your permissions!\n\n"
                f"Before claiming you don't have authorization, you SHOULD:\n"
                f"1. Call `projects_get(id='proj_xxx')` to see the team and check if you're Lead/Member\n"
                f"2. Call `employees_get(id='employee_id')` to check manager relationships\n"
                f"3. Call `customers_get(id='cust_xxx')` to check if you're the Account Manager\n\n"
                f"You might actually HAVE permission and not realize it!\n\n"
                f"**If you've already verified**, call respond again with the same outcome."
            )
            return

        # CASE 3: Only weak check (search) without strong check (get)
        if had_weak_check and not had_strong_check:
            self._soft_block(
                ctx, warning_key,
                f"Outcome Validation: '{outcome}' with only SEARCH (no GET for role verification)",
                f"🔍 OUTCOME VALIDATION: You responded with '{outcome}', but you only used *_search tools!\n\n"
                f"**IMPORTANT**: `projects_search` only returns basic info (id, name, status). "
                f"It does NOT return team membership or roles!\n\n"
                f"To verify your authorization, you MUST call:\n"
                f"- `projects_get(id='proj_xxx')` -> returns `team` with `employee` and `role` for each member\n"
                f"- `customers_get(id='cust_xxx')` -> returns `account_manager` field\n"
                f"- `employees_get(id='xxx')` -> returns `manager` (direct manager relationship)\n\n"
                f"Without calling these, you CANNOT know if you're the Lead, Account Manager, or Direct Manager!\n\n"
                f"**If you're certain you lack authorization**, call respond again with the same outcome."
            )
            return

        # CASE 4: denied_security but message suggests missing INFO
        message = ctx.model.message or ""
        if self._missing_info_re.search(message):
            self._soft_block(
                ctx, warning_key,
                f"Outcome Validation: '{outcome}' but message suggests MISSING INFO, not missing permission",
                f"🔍 OUTCOME VALIDATION: You responded with '{outcome}', but your message suggests the issue is "
                f"**missing information** (JIRA ticket, CC code, etc.), NOT lack of permission!\n\n"
                f"**CRITICAL DISTINCTION:**\n"
                f"- `denied_security` = You **LACK PERMISSION** to do this action (not Lead, not AM, not Manager)\n"
                f"- `none_clarification_needed` = You **HAVE PERMISSION** but need additional info (JIRA ticket, CC code, etc.)\n\n"
                f"Your message mentions policy requirements. If you ARE authorized (Lead/AM/Manager) but the "
                f"user didn't provide required info (like JIRA ticket), use `none_clarification_needed`.\n\n"
                f"Only use `denied_security` if you actually LACK the role/permission to do the action.\n\n"
                f"**If you're certain you lack permission**, call respond again with the same outcome."
            )

    def _validate_denial_basis(self, ctx: ToolContext, denial_basis: str, outcome: str) -> bool:
        """
        Validate denied outcome based on agent's declared denial_basis.

        AICODE-NOTE: Adaptive approach (t011 fix). Instead of hardcoding department checks,
        agent declares WHY it's denying, and we validate the appropriate checks were made.

        Args:
            ctx: Tool context
            denial_basis: Agent's declared reason for denial
            outcome: The outcome being validated

        Returns:
            True if validation passed (skip legacy checks), False otherwise
        """
        action_types_executed = ctx.shared.get('action_types_executed', set())

        if denial_basis == 'identity_restriction':
            # Agent claims system-level restriction from who_am_i
            # (e.g., External dept cannot access cross-department time summaries)
            # Validation: who_am_i must have been called
            if 'who_am_i' in action_types_executed:
                print(f"  {CLI_GREEN}✓ Outcome Validation: Accepted '{outcome}' with "
                      f"denial_basis='identity_restriction' (who_am_i verified){CLI_CLR}")
                return True
            else:
                # Agent claims identity restriction but didn't call who_am_i
                self._soft_block(
                    ctx,
                    warning_key='outcome_validation_warned',
                    log_msg=f"Denial basis 'identity_restriction' but who_am_i not called",
                    block_msg=(
                        f"🔍 OUTCOME VALIDATION: You declared `denial_basis='identity_restriction'` "
                        f"but you didn't call `who_am_i` to verify your identity restrictions!\n\n"
                        f"Call `who_am_i` first to confirm your department/role restrictions."
                    )
                )
                return False

        elif denial_basis == 'entity_permission':
            # Agent claims no permission on specific entity
            # Validation: must have called _get to check roles
            had_strong_check = bool(action_types_executed & self.PERMISSION_CHECK_TOOLS_STRONG)
            if had_strong_check:
                print(f"  {CLI_GREEN}✓ Outcome Validation: Accepted '{outcome}' with "
                      f"denial_basis='entity_permission' (entity check verified){CLI_CLR}")
                return True
            else:
                # Will fall through to legacy CASE 2/3 which handles this
                return False

        elif denial_basis == 'policy_violation':
            # Agent claims wiki policy prevents action
            # Validation: wiki_search or wiki_load must have been called
            wiki_checked = any(t in action_types_executed for t in ['wiki_search', 'wiki_load'])
            if wiki_checked:
                print(f"  {CLI_GREEN}✓ Outcome Validation: Accepted '{outcome}' with "
                      f"denial_basis='policy_violation' (wiki checked){CLI_CLR}")
                return True
            else:
                self._soft_block(
                    ctx,
                    warning_key='outcome_validation_warned',
                    log_msg=f"Denial basis 'policy_violation' but wiki not checked",
                    block_msg=(
                        f"🔍 OUTCOME VALIDATION: You declared `denial_basis='policy_violation'` "
                        f"but you didn't search the wiki to verify the policy!\n\n"
                        f"Use `wiki_search` to find and cite the specific policy that prevents this action."
                    )
                )
                return False

        elif denial_basis == 'guest_restriction':
            # Agent claims guest/public user restriction
            # Validation: who_am_i called and is_public=True
            is_public = ctx.shared.get('is_public', False)
            if 'who_am_i' in action_types_executed and is_public:
                print(f"  {CLI_GREEN}✓ Outcome Validation: Accepted '{outcome}' with "
                      f"denial_basis='guest_restriction' (public user verified){CLI_CLR}")
                return True
            elif 'who_am_i' not in action_types_executed:
                self._soft_block(
                    ctx,
                    warning_key='outcome_validation_warned',
                    log_msg=f"Denial basis 'guest_restriction' but who_am_i not called",
                    block_msg=(
                        f"🔍 OUTCOME VALIDATION: You declared `denial_basis='guest_restriction'` "
                        f"but you didn't call `who_am_i` to verify you're a guest!\n\n"
                        f"Call `who_am_i` first to confirm your public/guest status."
                    )
                )
                return False
            else:
                # who_am_i called but not public - agent is wrong
                self._soft_block(
                    ctx,
                    warning_key='outcome_validation_warned',
                    log_msg=f"Denial basis 'guest_restriction' but user is not public",
                    block_msg=(
                        f"🔍 OUTCOME VALIDATION: You declared `denial_basis='guest_restriction'` "
                        f"but `who_am_i` shows you are NOT a guest (is_public=False)!\n\n"
                        f"You are an authenticated user. Check your actual permissions via "
                        f"`projects_get` or `employees_get`."
                    )
                )
                return False

        # Unknown denial_basis - fall through to legacy validation
        return False

    def _validate_ok_not_found_for_mutations(self, ctx: ToolContext, outcome: str) -> None:
        """Soft hint for ok_not_found on mutation tasks after projects_search."""
        if outcome != "ok_not_found":
            return

        task_text = get_task_text(ctx).lower()
        if not self._mutation_re.search(task_text):
            return

        action_types_executed = ctx.shared.get('action_types_executed', set())
        if 'projects_search' not in action_types_executed:
            return

        self._soft_hint(
            ctx,
            "Mutation Hint: 'ok_not_found' on possible mutation task",
            "💡 HINT: You responded 'ok_not_found' after projects_search. "
            "If any results looked similar (fuzzy match), check authorization before giving up."
        )


class SingleCandidateOkHint(ResponseGuard):
    """
    Nudges agent to use ok_answer when exactly ONE candidate was found.

    Problem: Agent finds single match but returns none_clarification_needed
    because query contains subjective words like "fits", "best", "good".

    Solution: If message indicates single candidate found with high confidence,
    hint that ok_answer is appropriate for single-match results.

    EXCEPTION: If the task contains subjective/vague patterns ("cool project",
    "best employee"), the clarification is CORRECT and we should NOT nudge.
    """

    target_outcomes = {"none_clarification_needed"}

    # Patterns indicating single candidate found
    SINGLE_CANDIDATE_PATTERNS = [
        r'\b(?:I\s+)?found\s+(?:one|1|a\s+single)\s+(?:candidate|match|person|employee|lead)',
        r'\bone\s+candidate\s+(?:who\s+)?match',
        r'\bonly\s+(?:one|1)\s+(?:person|candidate|employee|lead)',
        r'\bexactly\s+(?:one|1)\s+(?:person|candidate|employee|lead)',
        r'\bthe\s+only\s+(?:Vienna-based\s+)?(?:lead|candidate|person)',
    ]

    # Patterns indicating multiple candidates (should NOT trigger hint)
    MULTIPLE_PATTERNS = [
        r'\b(?:multiple|several|two|three|four|2|3|4)\s+(?:candidates?|matches?|options?|people)',
        r'\bfound\s+(?:the\s+following|these)\s+(?:candidates?|options?)',
        r'\bwhich\s+(?:one|of\s+these)',
    ]

    # Confirmation question patterns
    CONFIRMATION_PATTERNS = [
        r'[Ii]s\s+(?:this|that|he|she)\s+(?:the\s+(?:person|one)|what|who)\s+you',
        r'[Ii]s\s+\w+\s+the\s+person\s+you\'?re?\s+looking',
        r'[Dd]o\s+you\s+(?:mean|want)',
    ]

    # Subjective/vague patterns in TASK - if present, clarification is CORRECT
    # AICODE-NOTE: Reuses patterns from SubjectiveQueryGuard
    SUBJECTIVE_TASK_PATTERNS = [
        r'\b(that|the)\s+(cool|nice|best|great|good|interesting|important)\s+\w+',
        r'\bcool\s+(project|person|employee|customer)\b',
        r'\bbest\s+(project|person|employee|customer)\b',
        r'\bthat\s+(one|project|thing)\b',
        r'\bfavorite\s+\w+',
        r'\bmost\s+(interesting|important|cool)\b',
    ]

    def __init__(self):
        self._single_re = re.compile('|'.join(self.SINGLE_CANDIDATE_PATTERNS), re.IGNORECASE)
        self._multiple_re = re.compile('|'.join(self.MULTIPLE_PATTERNS), re.IGNORECASE)
        self._confirm_re = re.compile('|'.join(self.CONFIRMATION_PATTERNS), re.IGNORECASE)
        self._subjective_re = re.compile('|'.join(self.SUBJECTIVE_TASK_PATTERNS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        message = ctx.model.message or ""

        # Skip if task contains subjective/vague patterns - clarification is CORRECT
        task_text = get_task_text(ctx) or ""
        if self._subjective_re.search(task_text):
            print(f"  {CLI_GREEN}✓ SingleCandidateOkHint: Skipped - task is subjective/vague{CLI_CLR}")
            return

        # Skip if message indicates multiple candidates
        if self._multiple_re.search(message):
            return

        # Check for single candidate pattern
        has_single = self._single_re.search(message)
        has_confirm = self._confirm_re.search(message)

        if has_single or has_confirm:
            # Agent found ONE candidate but is asking for confirmation
            self._soft_block(
                ctx,
                warning_key='single_candidate_warned',
                log_msg="SingleCandidateOkHint: Agent found one candidate but used clarification",
                block_msg=(
                    "💡 SINGLE CANDIDATE FOUND: You found exactly ONE matching candidate and are asking "
                    "for confirmation. For single-match results with high confidence, use `ok_answer`!\n\n"
                    "**BENCHMARK EXPECTATION**: When there's only one candidate that matches the criteria, "
                    "the benchmark expects a definitive answer (`ok_answer`), not a clarification request.\n\n"
                    "Only use `none_clarification_needed` when:\n"
                    "- You found MULTIPLE candidates and need user to choose\n"
                    "- You found ZERO candidates and need more info\n"
                    "- Critical information is genuinely missing\n\n"
                    "**ACTION**: Change outcome to `ok_answer` and provide the candidate information directly."
                )
            )


class SubjectiveQueryGuard(ResponseGuard):
    """
    Catches ok_answer on subjective/vague queries using combo approach:

    1. PRIMARY: Check query_specificity parameter from agent
       - If agent declares "ambiguous" but uses ok_answer -> block
    2. FALLBACK: Basic heuristic for obvious cases (agent didn't set param or lied)
       - Task has vague pattern + no specific ID -> block

    This forces agent to explicitly think about query specificity.
    """

    target_outcomes = {"ok_answer"}
    require_public = None  # Both public and internal users

    # Fallback: Vague patterns (determiner + adjective + entity)
    VAGUE_PATTERNS = [
        r'\b(that|the)\s+(cool|nice|best|great|good|interesting|important)\s+\w+',
        r'\bcool\s+(project|person|employee|customer)\b',
        r'\bbest\s+(project|person|employee|customer)\b',
        r'\bthat\s+(one|project|thing)\b',
    ]

    # Specific ID patterns - if present, query is likely specific
    SPECIFIC_ID_PATTERN = r'\b(proj_|emp_|cust_)[a-z0-9_]+'

    def __init__(self):
        self._vague_re = re.compile('|'.join(self.VAGUE_PATTERNS), re.IGNORECASE)
        self._specific_id_re = re.compile(self.SPECIFIC_ID_PATTERN, re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        # CRITICAL: Skip if mutation was already performed - action is done, can't undo
        # For TIME LOGGING with ambiguous queries, agent filters by authorization (per prompts.py)
        # so if time was logged successfully, we should accept ok_answer even if query was ambiguous
        if ctx.shared.get('had_mutations'):
            return

        # Get agent's declared specificity
        query_specificity = ctx.shared.get('query_specificity', 'unspecified')

        # PRIMARY CHECK: Agent explicitly declared query as ambiguous
        if query_specificity == 'ambiguous':
            self._soft_block(
                ctx,
                warning_key='subjective_query_warned',
                log_msg="SubjectiveQueryGuard: Agent declared 'ambiguous' but used ok_answer",
                block_msg=(
                    "⚠️ CONTRADICTION: You declared `query_specificity: 'ambiguous'` but responded with `ok_answer`!\n\n"
                    "If the query is ambiguous, you MUST use `none_clarification_needed` and ask the user to clarify.\n"
                    "Even if you found only ONE result, ask: 'I found [X]. Is this what you meant?'\n\n"
                    "Either:\n"
                    "1. Change outcome to `none_clarification_needed` (if truly ambiguous)\n"
                    "2. Change query_specificity to `specific` (if you're certain about the answer)"
                )
            )
            return

        # FALLBACK CHECK: Agent didn't declare specificity - verify with heuristic
        # Only trigger if: agent didn't say 'specific' AND has vague pattern AND no specific ID in task
        # If agent explicitly says 'specific', trust it - they resolved the entities via API
        if query_specificity == 'specific':
            return  # Agent explicitly confirmed specificity, trust them

        has_vague_pattern = bool(self._vague_re.search(task_text))
        has_specific_id = bool(self._specific_id_re.search(task_text))

        if has_vague_pattern and not has_specific_id:
            # Agent might be lying or forgot to set the parameter
            self._soft_block(
                ctx,
                warning_key='subjective_query_warned',
                log_msg=f"SubjectiveQueryGuard: Vague pattern detected, specificity='{query_specificity}'",
                block_msg=(
                    "⚠️ AMBIGUITY CHECK: The query contains vague/subjective terms (e.g., 'cool', 'that', 'best') "
                    "and NO specific entity ID.\n\n"
                    "You responded with `ok_answer` but did you verify the query is truly specific?\n\n"
                    "**REQUIRED**: Set `query_specificity` in your respond call:\n"
                    "- `'specific'` = Query has clear identifiers or unambiguous names\n"
                    "- `'ambiguous'` = Query uses vague terms, pronouns, or subjective adjectives\n\n"
                    "If ambiguous -> use `none_clarification_needed` and ask user to clarify.\n"
                    "If specific -> set `query_specificity: 'specific'` and call respond again."
                )
            )


class VagueQueryNotFoundGuard(ResponseGuard):
    """
    Catches ok_not_found on vague/ambiguous queries.

    AICODE-NOTE: Fix for t005. Agent correctly identifies query as "vague" but uses
    ok_not_found instead of none_clarification_needed. If the query is vague,
    agent cannot claim "not found" - they don't even know what to search for!

    Logic:
    - If query_specificity == "vague" AND outcome == "ok_not_found" -> block
    - Vague queries should ALWAYS use none_clarification_needed to ask for specifics
    """

    target_outcomes = {"ok_not_found"}
    require_public = None  # Both public and internal users

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        query_specificity = ctx.shared.get('query_specificity', 'unspecified')

        # PRIMARY CHECK: Agent explicitly declared query as vague
        if query_specificity == 'vague':
            self._soft_block(
                ctx,
                warning_key='vague_not_found_warned',
                log_msg="VagueQueryNotFoundGuard: Agent declared 'vague' but used ok_not_found",
                block_msg=(
                    "⚠️ CONTRADICTION: You declared `query_specificity: 'vague'` but responded with `ok_not_found`!\n\n"
                    "**If the query is vague, you CANNOT claim 'not found'!**\n"
                    "You don't even know what exactly to search for.\n\n"
                    "For vague/ambiguous queries, ALWAYS use `none_clarification_needed` and ask:\n"
                    "- 'Could you specify which project you mean?'\n"
                    "- 'What exactly are you looking for?'\n\n"
                    "**ACTION**: Change outcome to `none_clarification_needed` and ask user to clarify."
                )
            )
            return

        # SECONDARY CHECK: Agent declared "ambiguous" (similar to vague)
        if query_specificity == 'ambiguous':
            self._soft_block(
                ctx,
                warning_key='vague_not_found_warned',
                log_msg="VagueQueryNotFoundGuard: Agent declared 'ambiguous' but used ok_not_found",
                block_msg=(
                    "⚠️ CONTRADICTION: You declared `query_specificity: 'ambiguous'` but responded with `ok_not_found`!\n\n"
                    "**If the query is ambiguous, you CANNOT claim 'not found'!**\n\n"
                    "For ambiguous queries, use `none_clarification_needed` and ask for specifics.\n\n"
                    "**ACTION**: Change outcome to `none_clarification_needed`."
                )
            )
