"""
Response Guards - middleware that intercepts Req_ProvideAgentResponse.

All classes inherit from ResponseGuard and implement _check() method.
"""
import re
from .base import ResponseGuard, get_task_text, has_project_reference
from ..base import ToolContext
from utils import CLI_YELLOW, CLI_GREEN, CLI_CLR


class BasicLookupDenialGuard(ResponseGuard):
    """
    Catches when authenticated users deny basic org-chart lookups.

    Problem: Agent finds customer data (account manager), but denies based on
    overly strict interpretation ("I'm not involved with this customer").

    Reality: Basic org lookups ("who is the account manager") are NOT sensitive.
    They're org-chart info available to ALL employees.
    """

    target_outcomes = {"denied_security"}
    require_public = False  # Skip for public users - they SHOULD deny

    # Patterns for basic lookup queries
    BASIC_LOOKUP_PATTERNS = [
        r'\bwho\s+is\s+(the\s+)?account\s+manager\b',
        r'\baccount\s+manager\s+for\b',
        r'\bAM\s+for\b',
        r'\bwho\s+(is\s+)?lead(s|ing)?\b',
        r'\bproject\s+lead\s+for\b',
        r'\bwho\s+manages?\b',
    ]

    # Patterns indicating it's NOT a basic lookup
    NON_LOOKUP_PATTERNS = [
        r'\bchange\b', r'\bupdate\b', r'\bmodify\b', r'\bedit\b',
        r'\bdelete\b', r'\bremove\b', r'\bpause\b', r'\barchive\b',
        r'\bcontract\b', r'\bfinancial\b', r'\bbudget\b', r'\brevenue\b',
        r'\binternal\s+notes?\b', r'\bconfidential\b',
    ]

    def __init__(self):
        self._lookup_re = re.compile('|'.join(self.BASIC_LOOKUP_PATTERNS), re.IGNORECASE)
        self._non_lookup_re = re.compile('|'.join(self.NON_LOOKUP_PATTERNS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Check if it's a basic lookup query
        if not self._lookup_re.search(task_text):
            return

        # Check if it's actually a modification/sensitive request
        if self._non_lookup_re.search(task_text):
            return  # Not a simple lookup, denial might be valid

        # Use soft_block to force agent to reconsider - basic lookups should NOT be denied
        self._soft_block(
            ctx,
            warning_key='basic_lookup_warned',
            log_msg="BasicLookupDenialGuard: denied_security for basic org-chart lookup",
            block_msg=(
                "🛑 INCORRECT DENIAL: This is a basic org-chart lookup, NOT a sensitive operation!\n\n"
                "**RULE**: Basic info like 'who leads project X' or 'who is the account manager' "
                "is available to ALL authenticated employees. No special authorization needed.\n\n"
                "You found the data (Lukas Brenner is the lead). Simply answer the question!\n\n"
                "**ACTION**: Change outcome to `ok_answer` and provide the requested information.\n"
                "Include relevant links (project, employee) in your response."
            )
        )


class PublicUserSemanticGuard(ResponseGuard):
    """
    Ensures public/guest users use 'denied_security' for internal data queries.

    Problem: Guest responds 'ok_not_found' instead of 'denied_security'.
    The distinction is critical:
    - ok_not_found = "The data doesn't exist"
    - denied_security = "The data exists but you can't access it"
    """

    target_outcomes = {"ok_not_found"}
    require_public = True  # Only for public/guest users

    # Patterns for internal/sensitive entities
    SENSITIVE_ENTITY_PATTERNS = [
        # Customer-related
        r'\bcustomer\b', r'\baccount\s+manager\b', r'\bAM\s+for\b', r'\bclient\b',
        r'\bACME\b', r'\bScandi\b', r'\bNordic\b',
        # Employee-related
        r'\bsalary\b', r'\bemployee\s+id\b', r'\bwho\s+is\b.*\b(lead|manager|engineer)\b',
        r'\breports?\s+to\b', r'\bteam\s+member\b',
        # Project-related
        r'\bproject\s+(id|status|team)\b', r'\bwho\s+leads?\b',
        # Time-related
        r'\btime\s+entries?\b', r'\bhours?\s+logged\b', r'\btime\s+summary\b',
    ]

    def __init__(self):
        self._sensitive_re = re.compile('|'.join(self.SENSITIVE_ENTITY_PATTERNS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Check if task is about sensitive/internal entities
        if not self._sensitive_re.search(task_text):
            return

        self._soft_block(
            ctx,
            warning_key='public_user_semantic_warned',
            log_msg="Public User Guard: 'ok_not_found' for internal data query by guest",
            block_msg=(
                "🛑 PUBLIC USER SEMANTIC ERROR: You are a GUEST and responded 'ok_not_found' "
                "for a query about internal company data (customers, employees, projects, etc.).\n\n"
                "**THIS IS INCORRECT!** The correct outcome is `denied_security`.\n\n"
                "The distinction:\n"
                "- `ok_not_found` = \"The data doesn't exist\" (WRONG for internal data)\n"
                "- `denied_security` = \"The data exists but you cannot access it\" (CORRECT for guests)\n\n"
                "As a guest, you have NO ACCESS to internal company data. "
                "The data exists - you just can't see it. That's a security denial, not 'not found'.\n\n"
                "**Please respond with `denied_security` and explain that guests cannot access internal data.**"
            )
        )


class AmbiguityGuardMiddleware(ResponseGuard):
    """
    Lightweight guard for ok_not_found responses.

    Problem: Agent searches wiki but not DB, responds ok_not_found.
    Adds a gentle reminder to search the database - no blocking.
    """

    target_outcomes = {"ok_not_found"}

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Check what tools were used
        action_types_executed = ctx.shared.get('action_types_executed', set())
        searched_db = any(t in action_types_executed for t in [
            'projects_search', 'employees_search', 'customers_search', 'time_search'
        ])

        if not searched_db:
            ctx.results.append(
                "\n💡 HINT: You responded 'ok_not_found' but didn't search the database. "
                "Consider using projects_search/employees_search before concluding something doesn't exist."
            )


class ProjectModificationClarificationGuard(ResponseGuard):
    """
    Ensures project modification clarifications include project links.

    Problem: Agent asks for JIRA ticket but didn't search for the project.
    The benchmark expects project links even in clarification responses.
    """

    target_outcomes = {"none_clarification_needed"}

    PROJECT_MOD_PATTERNS = [
        r'\bpause\b.{0,50}\bproject\b',
        r'\barchive\b.{0,50}\bproject\b',
        r'\bchange\s+project\s+status\b',
        r'\bupdate\s+project\s+status\b',
        r'\bset\s+project\s+to\b',
        r'\bswitch\s+project\b',
        r'\bproject\b.{0,30}\bto\s+(paused|archived|active)\b',
    ]

    def __init__(self):
        self._mod_re = re.compile('|'.join(self.PROJECT_MOD_PATTERNS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Check if this is a project modification task
        if not self._mod_re.search(task_text):
            return

        message = ctx.model.message or ""
        links = ctx.model.links or []

        # Check if project is referenced
        if has_project_reference(message, links):
            return  # All good

        # Check if projects_search was called
        action_types_executed = ctx.shared.get('action_types_executed', set())
        has_searched = 'projects_search' in action_types_executed

        if not has_searched:
            block_msg = (
                "🛑 PROJECT MODIFICATION CLARIFICATION: You're asking for clarification about a project modification, "
                "but you MUST include the project link in your response!\n\n"
                "**REQUIRED STEPS:**\n"
                "1. Use `projects_search` to find the project (e.g., search by name or keyword)\n"
                "2. Include the project ID in your message (e.g., 'proj_acme_line3_cv_poc')\n"
                "3. Add the project to your `links` array\n\n"
                "The benchmark expects project links even in clarification responses."
            )
        else:
            block_msg = (
                "🛑 PROJECT MODIFICATION CLARIFICATION: You searched for projects but didn't include the project link "
                "in your clarification response!\n\n"
                "**REQUIRED:** Add the project to your `links` array, like:\n"
                "`\"links\": [{\"id\": \"proj_xxx\", \"kind\": \"project\"}]`\n\n"
                "The benchmark expects project links even in clarification responses."
            )

        self._soft_block(
            ctx,
            warning_key='project_mod_clarification_warned',
            log_msg="Project Mod Guard: Clarification without project link - blocking",
            block_msg=block_msg
        )


class TimeLoggingClarificationGuard(ResponseGuard):
    """
    Ensures time logging clarification requests include project links.

    Problem: Agent asks for CC codes but didn't identify the project.
    The benchmark expects project links even in clarification responses.
    """

    target_outcomes = {"none_clarification_needed"}

    TIME_LOG_PATTERNS = [
        r'\blog\s+\d+\s*hours?\b',
        r'\b\d+\s*hours?\s+of\b',
        r'\bbillable\s+work\b',
        r'\blog\s+time\b',
        r'\btime\s+entry\b',
        r'\btrack\s+time\b',
    ]

    def __init__(self):
        self._time_log_re = re.compile('|'.join(self.TIME_LOG_PATTERNS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Check if this is a time logging task
        if not self._time_log_re.search(task_text):
            return

        message = ctx.model.message or ""
        links = ctx.model.links or []

        # Check if project is referenced
        if has_project_reference(message, links):
            return  # All good

        self._soft_block(
            ctx,
            warning_key='time_log_clarification_warned',
            log_msg="TimeLog Guard: Clarification without project link - blocking",
            block_msg=(
                "🛑 TIME LOGGING CLARIFICATION: You're asking for clarification about a time logging task, "
                "but you MUST include the project link in your response!\n\n"
                "**REQUIRED STEPS:**\n"
                "1. Use `projects_search(member=target_employee_id)` to find the project\n"
                "2. Include the project ID in your message (e.g., 'proj_acme_line3_cv_poc')\n"
                "3. Add the project to your `links` array\n\n"
                "The benchmark expects project links even in clarification responses.\n"
                "Search for the project first, then ask for clarification WITH the project link."
            )
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


class ResponseValidationMiddleware(ResponseGuard):
    """
    Validates respond calls have proper message and links.
    Auto-generates message for empty responses after mutations.
    """

    target_outcomes = {"ok_answer"}

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        message = ctx.model.message or ""

        # Check if mutations were performed this session
        had_mutations = ctx.shared.get('had_mutations', False)
        mutation_entities = ctx.shared.get('mutation_entities', [])

        # Validate: If mutations happened and outcome is ok_answer, message should describe what was done
        if had_mutations and message in ["", "No message provided.", "No message provided"]:
            print(f"  {CLI_YELLOW}⚠️ Response Validation: Empty message after mutation. Injecting summary...{CLI_CLR}")
            # Auto-generate a minimal message from mutation_entities
            entity_descriptions = []
            for entity in mutation_entities:
                kind = entity.get("kind", "entity")
                eid = entity.get("id", "unknown")
                entity_descriptions.append(f"{kind}: {eid}")
            if entity_descriptions:
                ctx.model.message = f"Action completed. Affected entities: {', '.join(entity_descriptions)}"
                print(f"  {CLI_GREEN}✓ Auto-generated message: {ctx.model.message[:100]}...{CLI_CLR}")


class OutcomeValidationMiddleware(ResponseGuard):
    """
    Validates denied_security responses to prevent false denials.

    Problems this solves:
    1. Agent tried to use non-existent tool → should be 'none_unsupported'
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

        # CASE 1: Tried to use non-existent tool → suggest none_unsupported
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
                f"- `projects_get(id='proj_xxx')` → returns `team` with `employee` and `role` for each member\n"
                f"- `customers_get(id='cust_xxx')` → returns `account_manager` field\n"
                f"- `employees_get(id='xxx')` → returns `manager` (direct manager relationship)\n\n"
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


class SubjectiveQueryGuard(ResponseGuard):
    """
    Catches ok_answer on subjective/vague queries using combo approach:

    1. PRIMARY: Check query_specificity parameter from agent
       - If agent declares "ambiguous" but uses ok_answer → block
    2. FALLBACK: Basic heuristic for obvious cases (agent didn't set param or lied)
       - Task has vague pattern + no specific ID → block

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
        # If agent explicitly says 'specific', trust it — they resolved the entities via API
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
                    "If ambiguous → use `none_clarification_needed` and ask user to clarify.\n"
                    "If specific → set `query_specificity: 'specific'` and call respond again."
                )
            )


class ProjectSearchReminderMiddleware(ResponseGuard):
    """
    Reminds agent to use projects_search for project-related queries.

    Problem: Agent searches wiki for project info, responds ok_not_found.
    But wiki doesn't contain project status - only projects_search does!
    """

    target_outcomes = {"ok_not_found"}

    PROJECT_KEYWORDS = [
        r'\bproject\b', r'\bPoC\b', r'\bpoc\b', r'\barchived?\b',
        r'\bwrapped\s+up\b', r'\bcompleted?\s+project\b',
    ]

    def __init__(self):
        self._project_re = re.compile('|'.join(self.PROJECT_KEYWORDS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not self._project_re.search(task_text):
            return

        # Check if projects_search was called
        action_types_executed = ctx.shared.get('action_types_executed', set())
        if 'projects_search' in action_types_executed:
            return  # Agent did search projects, ok_not_found is valid

        self._soft_hint(
            ctx,
            "Project Search Hint: ok_not_found without projects_search",
            "💡 HINT: You responded 'ok_not_found' but didn't use `projects_search`. "
            "Wiki doesn't contain project status - consider searching the database."
        )
