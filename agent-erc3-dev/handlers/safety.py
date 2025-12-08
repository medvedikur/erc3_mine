from typing import Any, List, Set, Tuple
import re
from erc3.erc3 import client
from .base import ToolContext, Middleware
from utils import CLI

CLI_YELLOW = CLI.YELLOW
CLI_RED = CLI.RED
CLI_GREEN = CLI.GREEN
CLI_BLUE = CLI.BLUE
CLI_CLR = CLI.RESET


class BasicLookupDenialGuard(Middleware):
    """
    Middleware that catches when authenticated users deny basic org-chart lookups.

    Problem: Agent finds customer data (account manager), but decides to deny based on
    overly strict interpretation of rulebook ("I'm not involved with this customer").

    Reality: Basic organizational lookups like "who is the account manager" are NOT sensitive.
    They're org-chart info available to ALL employees. Only DETAILED customer data
    (contracts, financials, internal notes) requires project involvement.

    This catches: authenticated user + denied_security + basic lookup query
    """

    # Patterns for basic lookup queries (NOT modification, NOT sensitive details)
    BASIC_LOOKUP_PATTERNS = [
        r'\bwho\s+is\s+(the\s+)?account\s+manager\b',
        r'\baccount\s+manager\s+for\b',
        r'\bAM\s+for\b',
        r'\bwho\s+(is\s+)?lead(s|ing)?\b',
        r'\bproject\s+lead\s+for\b',
        r'\bwho\s+manages?\b',
    ]

    # Patterns that indicate it's NOT a basic lookup (modification, detailed access)
    NON_LOOKUP_PATTERNS = [
        r'\bchange\b', r'\bupdate\b', r'\bmodify\b', r'\bedit\b',
        r'\bdelete\b', r'\bremove\b', r'\bpause\b', r'\barchive\b',
        r'\bcontract\b', r'\bfinancial\b', r'\bbudget\b', r'\brevenue\b',
        r'\binternal\s+notes?\b', r'\bconfidential\b',
    ]

    def __init__(self):
        self._lookup_re = re.compile('|'.join(self.BASIC_LOOKUP_PATTERNS), re.IGNORECASE)
        self._non_lookup_re = re.compile('|'.join(self.NON_LOOKUP_PATTERNS), re.IGNORECASE)

    def process(self, ctx: ToolContext) -> None:
        if not isinstance(ctx.model, client.Req_ProvideAgentResponse):
            return

        outcome = ctx.model.outcome or ""
        if outcome != "denied_security":
            return

        # Skip for public users - they SHOULD deny
        security_manager = ctx.shared.get('security_manager')
        if security_manager and getattr(security_manager, 'is_public', False):
            return

        task = ctx.shared.get('task')
        task_text = getattr(task, 'task_text', '') if task else ''
        if not task_text:
            return

        # Check if it's a basic lookup query
        if not self._lookup_re.search(task_text):
            return

        # Check if it's actually a modification/sensitive request
        if self._non_lookup_re.search(task_text):
            return  # Not a simple lookup, denial might be valid

        # Soft hint only - don't block, just add reminder to results
        print(f"  {CLI_YELLOW}💡 Basic Lookup Hint: denied_security for possible org-chart lookup{CLI_CLR}")
        ctx.results.append(
            f"💡 HINT: You responded 'denied_security' but this looks like a basic org-chart lookup.\n"
            f"Basic info like 'who is the Account Manager' is typically available to all employees.\n"
            f"If you found the data, consider responding with `ok_answer` instead."
        )


class PublicUserSemanticGuard(Middleware):
    """
    Middleware that ensures public/guest users use 'denied_security' for internal data queries.

    Problem: Agent sees it's a guest, doesn't try customers_search (because it knows guests can't),
    but responds with 'ok_not_found' instead of 'denied_security'.

    The distinction is critical:
    - ok_not_found = "The data doesn't exist"
    - denied_security = "The data exists but you can't access it"

    For guests asking about internal entities (customers, employees, projects, time entries),
    the correct response is ALWAYS denied_security, not ok_not_found.
    """

    # Patterns that indicate queries about internal/sensitive entities
    SENSITIVE_ENTITY_PATTERNS = [
        # Customer-related
        r'\bcustomer\b', r'\baccount\s+manager\b', r'\bAM\s+for\b', r'\bclient\b',
        r'\bACME\b', r'\bScandi\b', r'\bNordic\b',  # Common customer names
        # Employee-related (excluding generic greetings)
        r'\bsalary\b', r'\bemployee\s+id\b', r'\bwho\s+is\b.*\b(lead|manager|engineer)\b',
        r'\breports?\s+to\b', r'\bteam\s+member\b',
        # Project-related
        r'\bproject\s+(id|status|team)\b', r'\bwho\s+leads?\b',
        # Time-related
        r'\btime\s+entries?\b', r'\bhours?\s+logged\b', r'\btime\s+summary\b',
    ]

    def __init__(self):
        self._sensitive_re = re.compile(
            '|'.join(self.SENSITIVE_ENTITY_PATTERNS),
            re.IGNORECASE
        )

    def process(self, ctx: ToolContext) -> None:
        # Only intercept respond calls
        if not isinstance(ctx.model, client.Req_ProvideAgentResponse):
            return

        # Only for public users
        security_manager = ctx.shared.get('security_manager')
        if not security_manager or not getattr(security_manager, 'is_public', False):
            return

        outcome = ctx.model.outcome or ""

        # Only check ok_not_found - other outcomes are fine
        if outcome != "ok_not_found":
            return

        # Get task text
        task = ctx.shared.get('task')
        task_text = getattr(task, 'task_text', '') if task else ''

        if not task_text:
            return

        # Check if task is about sensitive/internal entities
        if not self._sensitive_re.search(task_text):
            return  # Not a sensitive query, ok_not_found might be valid

        # Check if already warned
        warning_key = 'public_user_semantic_warned'
        if ctx.shared.get(warning_key):
            print(f"  {CLI_GREEN}✓ Public User Guard: Agent confirmed ok_not_found after warning{CLI_CLR}")
            return

        # Block and require denied_security
        print(f"  {CLI_YELLOW}🛑 Public User Guard: 'ok_not_found' for internal data query by guest{CLI_CLR}")
        ctx.shared[warning_key] = True
        ctx.stop_execution = True
        ctx.results.append(
            f"🛑 PUBLIC USER SEMANTIC ERROR: You are a GUEST and responded 'ok_not_found' "
            f"for a query about internal company data (customers, employees, projects, etc.).\n\n"
            f"**THIS IS INCORRECT!** The correct outcome is `denied_security`.\n\n"
            f"The distinction:\n"
            f"- `ok_not_found` = \"The data doesn't exist\" (WRONG for internal data)\n"
            f"- `denied_security` = \"The data exists but you cannot access it\" (CORRECT for guests)\n\n"
            f"As a guest, you have NO ACCESS to internal company data. "
            f"The data exists - you just can't see it. That's a security denial, not 'not found'.\n\n"
            f"**Please respond with `denied_security` and explain that guests cannot access internal data.**"
        )


class AmbiguityGuardMiddleware(Middleware):
    """
    Lightweight middleware for ok_not_found responses only.

    Problem: Agent searches wiki, doesn't find something, responds ok_not_found.
    But maybe they should have searched the database (projects_search, employees_search).

    This middleware just adds a gentle reminder for ok_not_found - no blocking.
    For subjective queries ("cool", "best"), we rely on the prompt rules.
    """

    def process(self, ctx: ToolContext) -> None:
        # Only intercept ok_not_found responses
        if not isinstance(ctx.model, client.Req_ProvideAgentResponse):
            return

        outcome = ctx.model.outcome or ""
        if outcome != "ok_not_found":
            return

        # Get task text
        task = ctx.shared.get('task')
        task_text = getattr(task, 'task_text', '') if task else ''

        if not task_text:
            return

        # Check what tools were used
        action_types_executed = ctx.shared.get('action_types_executed', set())

        # If agent didn't search database at all, add a hint (but don't block)
        searched_db = any(t in action_types_executed for t in [
            'projects_search', 'employees_search', 'customers_search', 'time_search'
        ])

        if not searched_db:
            # Just add a hint to the response, don't block
            ctx.results.append(
                f"\n💡 HINT: You responded 'ok_not_found' but didn't search the database. "
                f"Consider using projects_search/employees_search before concluding something doesn't exist."
            )


class ProjectModificationClarificationGuard(Middleware):
    """
    Middleware that ensures project modification clarifications include project links.

    Problem: When asking for JIRA tickets or other clarifications for project changes,
    the agent might respond before searching for the project. But the benchmark
    expects the project link even in clarification responses.

    Solution: If task mentions modifying a specific project and agent responds with
    clarification without a project link, block and ask agent to search for project first.
    """

    # Patterns that indicate project modification intent
    PROJECT_MOD_PATTERNS = [
        r'\bpause\b.{0,50}\bproject\b',      # "pause the Line 3 project"
        r'\barchive\b.{0,50}\bproject\b',    # "archive X project"
        r'\bchange\s+project\s+status\b',
        r'\bupdate\s+project\s+status\b',
        r'\bset\s+project\s+to\b',
        r'\bswitch\s+project\b',
        r'\bproject\b.{0,30}\bto\s+(paused|archived|active)\b',  # "project X to paused"
    ]

    # Patterns that extract project name candidates from task
    PROJECT_NAME_PATTERNS = [
        r'pause\s+(?:the\s+)?["\']?([A-Za-z0-9\s]+?)["\']?\s+project',
        r'project\s+["\']?([A-Za-z0-9\s]+?)["\']?\s+to\s+',
    ]

    def __init__(self):
        self._mod_re = re.compile('|'.join(self.PROJECT_MOD_PATTERNS), re.IGNORECASE)

    def _has_project_reference(self, message: str, links: list) -> bool:
        """Check if response contains project reference."""
        if links:
            for link in links:
                if isinstance(link, dict):
                    link_id = link.get('id', '')
                    link_kind = link.get('kind', '')
                    if link_id.startswith('proj_') or link_kind == 'project':
                        return True
                elif isinstance(link, str) and link.startswith('proj_'):
                    return True

        if message and re.search(r'proj_[a-z0-9_]+', message, re.IGNORECASE):
            return True

        return False

    def process(self, ctx: ToolContext) -> None:
        if not isinstance(ctx.model, client.Req_ProvideAgentResponse):
            return

        outcome = ctx.model.outcome or ""
        if outcome != "none_clarification_needed":
            return

        task = ctx.shared.get('task')
        task_text = getattr(task, 'task_text', '') if task else ''
        if not task_text:
            return

        # Check if this is a project modification task
        if not self._mod_re.search(task_text):
            return

        message = ctx.model.message or ""
        links = ctx.model.links or []

        # Check if project is referenced
        if self._has_project_reference(message, links):
            return  # All good

        # Check if projects_search was called
        action_types_executed = ctx.shared.get('action_types_executed', set())
        has_searched = 'projects_search' in action_types_executed

        # Soft hint only - don't block, just add reminder to results
        print(f"  {CLI_YELLOW}💡 Project Mod Hint: Clarification without project link{CLI_CLR}")

        if not has_searched:
            ctx.results.append(
                f"💡 HINT: You're asking for clarification about a project modification, "
                f"but haven't searched for the project yet. Consider using `projects_search` "
                f"to identify and include the project link in your response."
            )
        else:
            ctx.results.append(
                f"💡 HINT: You searched for projects but didn't include the project link "
                f"in your clarification. Consider adding the project to your `links` array."
            )


class TimeLoggingClarificationGuard(Middleware):
    """
    Middleware that ensures time logging clarification requests include project links.

    Problem: When asking for CC codes or other clarifications for time logging,
    the agent might respond before identifying the project. But the benchmark
    expects the project link even in clarification responses.

    Solution: If task mentions time logging and agent responds with clarification
    without a project link, block and ask agent to identify the project first.
    """

    # Patterns that indicate time logging intent
    TIME_LOG_PATTERNS = [
        r'\blog\s+\d+\s*hours?\b',       # "log 3 hours"
        r'\b\d+\s*hours?\s+of\b',         # "3 hours of"
        r'\bbillable\s+work\b',           # "billable work"
        r'\blog\s+time\b',                # "log time"
        r'\btime\s+entry\b',              # "time entry"
        r'\btrack\s+time\b',              # "track time"
    ]

    def __init__(self):
        self._time_log_re = re.compile(
            '|'.join(self.TIME_LOG_PATTERNS),
            re.IGNORECASE
        )

    def _has_project_reference(self, message: str, links: list) -> bool:
        """Check if response contains project reference."""
        # Check links
        if links:
            for link in links:
                if isinstance(link, dict):
                    link_id = link.get('id', '')
                    link_kind = link.get('kind', '')
                    if link_id.startswith('proj_') or link_kind == 'project':
                        return True
                elif isinstance(link, str) and link.startswith('proj_'):
                    return True

        # Check message text for project ID pattern
        if message and re.search(r'proj_[a-z0-9_]+', message, re.IGNORECASE):
            return True

        return False

    def process(self, ctx: ToolContext) -> None:
        # Only intercept respond calls with none_clarification_needed
        if not isinstance(ctx.model, client.Req_ProvideAgentResponse):
            return

        outcome = ctx.model.outcome or ""
        if outcome != "none_clarification_needed":
            return

        # Get task text
        task = ctx.shared.get('task')
        task_text = getattr(task, 'task_text', '') if task else ''

        if not task_text:
            return

        # Check if this is a time logging task
        if not self._time_log_re.search(task_text):
            return

        message = ctx.model.message or ""
        links = ctx.model.links or []

        # Check if project is referenced
        if self._has_project_reference(message, links):
            return  # All good

        # SOFT BLOCK: Block first time, allow on repeat
        warning_key = 'time_log_clarification_warned'
        if ctx.shared.get(warning_key):
            print(f"  {CLI_GREEN}✓ TimeLog Guard: Agent confirmed clarification after warning{CLI_CLR}")
            return

        print(f"  {CLI_YELLOW}🛑 TimeLog Guard: Clarification without project link - blocking{CLI_CLR}")
        ctx.shared[warning_key] = True
        ctx.stop_execution = True
        ctx.results.append(
            f"🛑 TIME LOGGING CLARIFICATION: You're asking for clarification about a time logging task, "
            f"but you MUST include the project link in your response!\n\n"
            f"**REQUIRED STEPS:**\n"
            f"1. Use `projects_search(member=target_employee_id)` to find the project\n"
            f"2. Include the project ID in your message (e.g., 'proj_acme_line3_cv_poc')\n"
            f"3. Add the project to your `links` array\n\n"
            f"The benchmark expects project links even in clarification responses.\n"
            f"Search for the project first, then ask for clarification WITH the project link."
        )


class ResponseValidationMiddleware(Middleware):
    """
    Middleware that validates respond calls have proper message and links.
    Prevents agents from submitting empty responses after mutations.
    """
    def process(self, ctx: ToolContext) -> None:
        if not isinstance(ctx.model, client.Req_ProvideAgentResponse):
            return

        message = ctx.model.message or ""
        links = ctx.model.links or []
        outcome = ctx.model.outcome or ""

        # Check if mutations were performed this session
        had_mutations = ctx.shared.get('had_mutations', False)
        mutation_entities = ctx.shared.get('mutation_entities', [])

        # Validate: If mutations happened and outcome is ok_answer, message should describe what was done
        if had_mutations and outcome == "ok_answer":
            if message in ["", "No message provided.", "No message provided"]:
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


class OutcomeValidationMiddleware(Middleware):
    """
    Middleware that validates denied_security responses to prevent false denials.

    Problems this solves:
    1. Agent tried to use non-existent tool → should be 'none_unsupported', not 'denied_security'
    2. Agent claims no permission without actually checking (no projects_get/employees_get call)

    This is a SOFT block - it warns the agent and asks to reconsider.
    If agent confirms, the response goes through.

    EXCEPTIONS (skip validation):
    - Public users (guests) - they SHOULD use denied_security for most requests
    - Destructive operations (wipe, delete) - denial without checking is correct
    """

    # Tools that indicate THOROUGH permission verification was done
    # projects_get and employees_get return detailed info (team, roles, manager)
    # projects_search and employees_search only return basic info (id, name, status)
    PERMISSION_CHECK_TOOLS_STRONG = {
        'projects_get',   # Returns team with roles - can verify Lead/Member
        'employees_get',  # Returns manager info - can verify Direct Manager
        'customers_get',  # Returns account_manager - can verify AM relationship
    }
    PERMISSION_CHECK_TOOLS_WEAK = {
        'projects_search',   # Only returns id, name, status - NO team info
        'employees_search',  # Only returns basic info - NO manager relationship
    }

    # Keywords that indicate destructive/dangerous operations where denial is expected
    DESTRUCTIVE_KEYWORDS = [
        r'\bwipe\b', r'\bdelete\b', r'\berase\b', r'\bdestroy\b',
        r'\bremove\s+all\b', r'\bclear\s+all\b', r'\bpurge\b',
        r'\bthreat\b', r'\bblackmail\b', r'\bhack\b', r'\bsteal\b',
    ]

    def __init__(self):
        self._destructive_re = re.compile(
            '|'.join(self.DESTRUCTIVE_KEYWORDS),
            re.IGNORECASE
        )

    def _is_destructive_request(self, task_text: str) -> bool:
        """Check if task appears to be a destructive/dangerous operation."""
        return bool(self._destructive_re.search(task_text or ''))

    def process(self, ctx: ToolContext) -> None:
        # Only intercept respond calls
        if not isinstance(ctx.model, client.Req_ProvideAgentResponse):
            return

        outcome = ctx.model.outcome or ""

        # Handle ok_not_found and none_clarification_needed for mutation tasks
        if outcome in ("ok_not_found", "none_clarification_needed"):
            self._validate_ok_not_found_for_mutations(ctx)
            return

        # Only validate denied outcomes from here
        if outcome not in ("denied_security", "denied_authorization"):
            return

        # EXCEPTION 1: Public users (guests) should deny most operations without needing to check
        security_manager = ctx.shared.get('security_manager')
        if security_manager and getattr(security_manager, 'is_public', False):
            print(f"  {CLI_GREEN}✓ Outcome Validation: Skipped for public user (guest){CLI_CLR}")
            return

        # EXCEPTION 2: Destructive operations - denial is expected without checking
        task = ctx.shared.get('task')
        task_text = getattr(task, 'task_text', '') if task else ''
        if self._is_destructive_request(task_text):
            print(f"  {CLI_GREEN}✓ Outcome Validation: Skipped for destructive operation{CLI_CLR}")
            return

        # Check for tool existence errors (tracked in agent.py)
        missing_tools = ctx.shared.get('missing_tools', [])
        had_unsupported_tool = len(missing_tools) > 0

        # Check for permission verification - distinguish between strong and weak checks
        action_types_executed = ctx.shared.get('action_types_executed', set())
        had_strong_check = bool(action_types_executed & self.PERMISSION_CHECK_TOOLS_STRONG)
        had_weak_check = bool(action_types_executed & self.PERMISSION_CHECK_TOOLS_WEAK)

        # Check if already warned (persists across turns via agent.py)
        warning_key = 'outcome_validation_warned'
        already_warned = ctx.shared.get(warning_key, False)

        # CASE 1: Tried to use non-existent tool → suggest none_unsupported
        if had_unsupported_tool:
            if already_warned:
                print(f"  {CLI_GREEN}✓ Outcome Validation: Agent confirmed '{outcome}' after warning{CLI_CLR}")
                return

            print(f"  {CLI_YELLOW}🔍 Outcome Validation: Agent tried non-existent tool(s): {missing_tools}{CLI_CLR}")
            ctx.shared[warning_key] = True
            ctx.stop_execution = True
            ctx.results.append(
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
            if already_warned:
                print(f"  {CLI_GREEN}✓ Outcome Validation: Agent confirmed '{outcome}' after warning{CLI_CLR}")
                return

            print(f"  {CLI_YELLOW}🔍 Outcome Validation: '{outcome}' without ANY permission verification{CLI_CLR}")
            ctx.shared[warning_key] = True
            ctx.stop_execution = True
            ctx.results.append(
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
        # Search only returns basic info, not team roles!
        if had_weak_check and not had_strong_check:
            if already_warned:
                print(f"  {CLI_GREEN}✓ Outcome Validation: Agent confirmed '{outcome}' after warning{CLI_CLR}")
                return

            print(f"  {CLI_YELLOW}🔍 Outcome Validation: '{outcome}' with only SEARCH (no GET for role verification){CLI_CLR}")
            ctx.shared[warning_key] = True
            ctx.stop_execution = True
            ctx.results.append(
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

        # CASE 4: denied_security but message suggests missing INFO (not missing PERMISSION)
        # If message mentions JIRA, ticket, CC code, cost centre - it's likely none_clarification_needed
        message = ctx.model.message or ""
        missing_info_keywords = [
            r'\bjira\b', r'\bticket\b', r'\bcost\s*cent', r'\bcc[\s-]',
            r'\bmissing\s+(required|mandatory)', r'\bnot\s+provided\b',
            r'\bprovide\s+a\s+valid\b', r'\brequired\s+by\s+policy\b',
        ]
        missing_info_re = re.compile('|'.join(missing_info_keywords), re.IGNORECASE)

        if missing_info_re.search(message):
            if already_warned:
                print(f"  {CLI_GREEN}✓ Outcome Validation: Agent confirmed '{outcome}' after warning{CLI_CLR}")
                return

            print(f"  {CLI_YELLOW}🔍 Outcome Validation: '{outcome}' but message suggests MISSING INFO, not missing permission{CLI_CLR}")
            ctx.shared[warning_key] = True
            ctx.stop_execution = True
            ctx.results.append(
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
            return

    def _validate_ok_not_found_for_mutations(self, ctx: ToolContext) -> None:
        """
        Validates ok_not_found responses for mutation tasks.

        Problem: Agent finds a fuzzy match (same words, different order) but responds
        ok_not_found instead of:
        1. Recognizing the fuzzy match as the intended entity
        2. Checking authorization for that entity
        3. Responding denied_security if not authorized
        """
        if not isinstance(ctx.model, client.Req_ProvideAgentResponse):
            return

        outcome = ctx.model.outcome or ""
        if outcome != "ok_not_found":
            return

        # Check if this is a mutation task (status change, update, modify)
        task = ctx.shared.get('task')
        task_text = (getattr(task, 'task_text', '') if task else '').lower()

        mutation_keywords = [
            r'\bswitch\s+status\b', r'\bchange\s+status\b', r'\bupdate\s+status\b',
            r'\bset\s+status\b', r'\bpause\b', r'\barchive\b', r'\bactivate\b',
            r'\bmodify\b', r'\bupdate\b', r'\bedit\b',
        ]
        mutation_re = re.compile('|'.join(mutation_keywords), re.IGNORECASE)
        is_mutation_task = bool(mutation_re.search(task_text))

        if not is_mutation_task:
            return

        # Check if projects_search was called and returned results
        action_types_executed = ctx.shared.get('action_types_executed', set())
        had_project_search = 'projects_search' in action_types_executed

        if not had_project_search:
            return

        # Soft hint only - don't block based on regex task text matching
        print(f"  {CLI_YELLOW}💡 Mutation Hint: 'ok_not_found' on possible mutation task{CLI_CLR}")
        ctx.results.append(
            f"💡 HINT: You responded 'ok_not_found' after projects_search. "
            f"If any results looked similar (fuzzy match), check authorization before giving up."
        )


class ProjectSearchReminderMiddleware(Middleware):
    """
    Middleware that reminds the agent to use projects_search when looking for projects.

    Problem: Agent searches wiki for project info, doesn't find it, responds ok_not_found.
    But wiki doesn't contain project status/existence - only projects_search does!

    Solution: When agent responds ok_not_found for a project-related query without
    having called projects_search, remind them to check the database.
    """

    PROJECT_KEYWORDS = [
        r'\bproject\b', r'\bPoC\b', r'\bpoc\b', r'\barchived?\b',
        r'\bwrapped\s+up\b', r'\bcompleted?\s+project\b',
    ]

    def __init__(self):
        self._project_re = re.compile('|'.join(self.PROJECT_KEYWORDS), re.IGNORECASE)

    def process(self, ctx: ToolContext) -> None:
        # Only intercept ok_not_found responses
        if not isinstance(ctx.model, client.Req_ProvideAgentResponse):
            return

        outcome = ctx.model.outcome or ""
        if outcome != "ok_not_found":
            return

        # Check if this is a project-related query
        task = ctx.shared.get('task')
        task_text = getattr(task, 'task_text', '') if task else ''
        if not self._project_re.search(task_text):
            return

        # Check if projects_search was called
        action_types_executed = ctx.shared.get('action_types_executed', set())
        if 'projects_search' in action_types_executed:
            return  # Agent did search projects, ok_not_found is valid

        # Soft hint only - don't block, just add reminder
        print(f"  {CLI_YELLOW}💡 Project Search Hint: ok_not_found without projects_search{CLI_CLR}")
        ctx.results.append(
            f"💡 HINT: You responded 'ok_not_found' but didn't use `projects_search`. "
            f"Wiki doesn't contain project status - consider searching the database."
        )


class ProjectMembershipMiddleware(Middleware):
    """
    Middleware that verifies if an employee is a member of the project
    before allowing a time log entry.
    This prevents the agent from logging time to the wrong project just because
    the name matched partially.

    Also checks for M&A policy compliance (CC codes) if merger.md exists.
    """
    def process(self, ctx: ToolContext) -> None:
        # Intercept Time Logging
        if isinstance(ctx.model, client.Req_LogTimeEntry):
            employee_id = ctx.model.employee
            project_id = ctx.model.project

            # Skip check if arguments are missing (validation will catch it later)
            if not employee_id or not project_id:
                return

            print(f"  {CLI_YELLOW}🛡️ Safety Check: Verifying project membership...{CLI_CLR}")

            # CHECK M&A POLICY: If merger.md exists and requires CC codes, inject a warning
            wiki_manager = ctx.shared.get('wiki_manager')
            if wiki_manager and wiki_manager.has_page("merger.md"):
                merger_content = wiki_manager.get_page("merger.md")
                if merger_content and "cost centre" in merger_content.lower():
                    # M&A policy requires CC codes for time entries
                    # Check if task text mentions CC code or the notes contain one
                    task = ctx.shared.get('task')
                    task_text = (getattr(task, 'task_text', '') or '').lower() if task else ''
                    notes = (ctx.model.notes or '').upper()

                    # CC code format: CC-<Region>-<Unit>-<ProjectCode> e.g. CC-EU-AI-042
                    # Be flexible with format - accept any CC-XXX-XX-XXX pattern (letters/numbers)
                    # User might provide CC-NORD-AI-12O (with letter O instead of 0)
                    cc_pattern = r'CC-[A-Z0-9]{2,4}-[A-Z0-9]{2,4}-[A-Z0-9]{2,4}'
                    has_cc_in_task = bool(re.search(cc_pattern, task_text.upper()))
                    has_cc_in_notes = bool(re.search(cc_pattern, notes))

                    if not has_cc_in_task and not has_cc_in_notes:
                        print(f"  {CLI_RED}⚠️ M&A Policy: CC code required but not provided!{CLI_CLR}")
                        ctx.stop_execution = True
                        ctx.results.append(
                            f"⚠️ M&A POLICY VIOLATION: Per merger.md, all time entries now require a Cost Centre (CC) code. "
                            f"Format: CC-<Region>-<Unit>-<ProjectCode> (e.g., CC-EU-AI-042). "
                            f"You MUST ask the user for the CC code before logging time. "
                            f"Use `none_clarification_needed` to request the CC code from the user."
                        )
                        return
            
            try:
                # Fetch Project Details
                # We use the API available in context
                # Try positional arg first, then project_id/id kwarg to handle API variations
                try:
                    resp_project = ctx.api.get_project(project_id)
                except TypeError:
                    try:
                        resp_project = ctx.api.get_project(project_id=project_id)
                    except TypeError:
                        resp_project = ctx.api.get_project(id=project_id)
                
                # Check Membership
                # Resp_GetProject contains 'project' field which has 'team'
                project = getattr(resp_project, 'project', None)
                if not project:
                     # Project not found?
                     print(f"  {CLI_YELLOW}⚠️ Safety Check: Project '{project_id}' not found via API.{CLI_CLR}")
                     # Let the action proceed to fail naturally or handle as error?
                     # If we can't find it, we can't check membership. 
                     # But time_log will likely fail if project invalid.
                     return

                # Assuming project.team is a list of employee IDs or objects with 'id'
                is_member = False
                if project.team:
                    # Handle both list of strings and list of objects
                    for member in project.team:
                        if isinstance(member, str):
                            if member == employee_id:
                                is_member = True
                                break
                        elif hasattr(member, 'id') and member.id == employee_id:
                            is_member = True
                            break
                        elif hasattr(member, 'employee_id') and member.employee_id == employee_id:
                            is_member = True
                            break
                        elif hasattr(member, 'employee') and member.employee == employee_id:
                            is_member = True
                            break
                
                if not is_member:
                    print(f"  {CLI_RED}⛔ Safety Violation: Employee not in project.{CLI_CLR}")
                    ctx.stop_execution = True
                    ctx.results.append(
                        f"SAFETY ERROR: Employee '{employee_id}' is NOT a member of project '{project.name}' ({project_id}). "
                        f"You cannot log time for an employee on a project they are not assigned to. "
                        f"Please verify the project ID. You may need to search for other projects or check project details. "
                        f"Tip: You can use 'time_search' for this employee to see which projects they have worked on recently."
                    )
            
            except Exception as e:
                # If we can't verify (e.g. project not found or API error), 
                # we should probably fail safe or warn. 
                # Let's fail safe and block, as logging to non-existent project is also bad.
                print(f"  {CLI_RED}⚠️ Safety Check Failed: {e}{CLI_CLR}")
                # We don't block execution on API error, just warn? 
                # Or block? If get_project fails, time_log will likely fail too.
                # Let's let the actual handler try and fail naturally if it's a network error.
                pass

