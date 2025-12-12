"""
Security Guards - validate security-related responses.

Guards:
- BasicLookupDenialGuard: Catches denied_security for basic org-chart lookups
- PublicUserSemanticGuard: Ensures guests use denied_security for internal data
"""
import re
from ..base import ResponseGuard, get_task_text
from ...base import ToolContext


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
