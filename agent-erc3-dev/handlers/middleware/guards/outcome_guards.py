"""
Outcome Guards - validate outcome type selection.

Guards:
- AmbiguityGuardMiddleware: Catches ok_not_found without DB search
- OutcomeValidationMiddleware: Validates denied outcomes
- SingleCandidateOkHint: Nudges ok_answer for single candidate
- SubjectiveQueryGuard: Blocks ok_answer on ambiguous queries
"""
import re
from ..base import ResponseGuard, get_task_text
from ...base import ToolContext
from utils import CLI_GREEN, CLI_CLR


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

    def __init__(self):
        self._project_re = re.compile('|'.join(self.PROJECT_KEYWORDS), re.IGNORECASE)
        self._employee_re = re.compile('|'.join(self.EMPLOYEE_KEYWORDS), re.IGNORECASE)

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

    def __init__(self):
        self._single_re = re.compile('|'.join(self.SINGLE_CANDIDATE_PATTERNS), re.IGNORECASE)
        self._multiple_re = re.compile('|'.join(self.MULTIPLE_PATTERNS), re.IGNORECASE)
        self._confirm_re = re.compile('|'.join(self.CONFIRMATION_PATTERNS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        message = ctx.model.message or ""

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
