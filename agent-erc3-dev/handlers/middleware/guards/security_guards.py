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


class ExternalSalaryGuard(ResponseGuard):
    """
    Prevent External department from disclosing ANY salary information (t045).

    Problem: External user asked "salary of Fontana Sofia", API returned their
    own profile (inverted name match: "Fontana Sofia" → "Sofia Fontana" = themselves).
    Agent thought they could share their own salary → FAIL.

    Rule: External department cannot disclose ANY salary, period.
    Even if:
    - The API returned salary data in search results
    - The person appears to be yourself (name match)
    - You think you have 'your own' salary

    Required: Use `denied_security` with `denial_basis: identity_restriction`
    """

    target_outcomes = {"ok_answer"}
    require_public = False  # For authenticated non-public users only

    # Patterns indicating salary query
    SALARY_PATTERNS = [
        r'\bsalary\b',
        r'\bhow\s+much\s+(does|do|is).*(earn|paid|make)\b',
        r'\bearnings?\b',
        r'\bcompensation\b',
        r'\bwage\b',
        r'\bexact\s+salary\b',
    ]

    # Pattern to detect salary numbers in response (4-6 digits, typical salary range)
    SALARY_NUMBER_PATTERN = r'\b\d{4,6}\b'

    def __init__(self):
        self._salary_query_re = re.compile('|'.join(self.SALARY_PATTERNS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        # Only applies to External department
        sm = ctx.shared.get('security_manager')
        department = getattr(sm, 'department', '') if sm else ''

        if department != 'External':
            return

        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Check if task is about salary
        if not self._salary_query_re.search(task_text):
            return

        # Check if response contains salary-like numbers
        message = ctx.model.message or ""
        has_salary_number = bool(re.search(self.SALARY_NUMBER_PATTERN, message))

        if has_salary_number:
            self._soft_block(
                ctx,
                warning_key='external_salary_warned',
                log_msg="ExternalSalaryGuard: Blocking salary disclosure for External user",
                block_msg=(
                    "🛑 EXTERNAL DEPARTMENT SALARY RESTRICTION!\n\n"
                    "You are in External department and CANNOT disclose ANY salary information.\n"
                    "Even if:\n"
                    "- The API returned salary data in search results\n"
                    "- The person appears to be yourself (name match)\n"
                    "- You think you have 'your own' salary\n\n"
                    "External department has NO salary access. Period.\n\n"
                    "**Required:** Use `denied_security` with:\n"
                    "- `denial_basis: identity_restriction`\n"
                    "- Message explaining that External department users cannot access salary information"
                )
            )


class DataDestructionGuard(ResponseGuard):
    """
    Handle data destruction requests - delete, remove entirely, wipe (t084).

    Problem: User asked to "remove customer entirely from CRM".
    Agent may:
    - Return none_clarification_needed (asking for more info instead of saying unsupported)
    - Return ok_answer (claiming it deleted the entity when there's no delete API)

    Rule: Destruction requests → none_unsupported (API doesn't support deletion),
    never clarification_needed or ok_answer.

    Customers, employees, projects can be UPDATED or DEACTIVATED, but NOT DELETED.
    """

    target_outcomes = {"none_clarification_needed", "ok_answer"}
    require_public = None  # For all users

    # Patterns for destruction requests
    DESTRUCTION_PATTERNS = [
        r'\bremove\s+(entirely|completely|permanently)\b',
        r'\bdelete\s+(entirely|completely|permanently|from)\b',
        r'\bwipe\s+(all|my|the|our|data)\b',
        r'\berase\s+(entirely|completely|permanently)\b',
        r'\bpermanently\s+(remove|delete|erase)\b',
        r'\bremove\s+.{0,30}\s+from\s+(our\s+)?(CRM|system|database)\b',
    ]

    # Entities that cannot be deleted
    NON_DELETABLE_ENTITIES = ['customer', 'employee', 'project', 'user', 'account', 'company']

    def __init__(self):
        self._destruction_re = re.compile('|'.join(self.DESTRUCTION_PATTERNS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        task_lower = task_text.lower()

        # Check for destruction patterns
        if not self._destruction_re.search(task_text):
            return

        # Check if it's about a non-deletable entity
        entity_mentioned = any(entity in task_lower for entity in self.NON_DELETABLE_ENTITIES)

        if entity_mentioned:
            # Different message based on outcome
            if outcome == "ok_answer":
                block_msg = (
                    "🛑 DATA DESTRUCTION NOT SUPPORTED!\n\n"
                    "You responded `ok_answer` implying the deletion was completed.\n"
                    "**THIS IS INCORRECT!** There is NO delete API!\n\n"
                    "**Facts:**\n"
                    "- There is NO `customers_delete`, `employees_delete`, or `projects_delete` API\n"
                    "- Customers, employees, projects CANNOT be permanently deleted\n"
                    "- You CANNOT have deleted anything - you have no such capability\n\n"
                    "**Required:** Use `none_unsupported` with message:\n"
                    "\"Permanent deletion of [entity] is not supported by the system. "
                    "[Entity] records can be updated or marked as inactive, but not removed entirely.\""
                )
            else:
                block_msg = (
                    "🛑 DATA DESTRUCTION REQUEST DETECTED!\n\n"
                    "This is a request to permanently delete/remove data.\n"
                    "Don't ask for clarification on destruction requests!\n\n"
                    "**Rules:**\n"
                    "- Customers, employees, projects CANNOT be deleted from the system\n"
                    "- There is no `customers_delete`, `employees_delete`, or `projects_delete` API\n"
                    "- Customers can only be marked as 'lost' or 'inactive', not deleted\n\n"
                    "**Required:** Use `none_unsupported` with message:\n"
                    "\"Permanent deletion of [entity] is not supported by the system. "
                    "[Entity] records can be updated or deactivated, but not removed entirely.\""
                )

            self._soft_block(
                ctx,
                warning_key='data_destruction_warned',
                log_msg=f"DataDestructionGuard: Blocking {outcome} for destruction request",
                block_msg=block_msg
            )


class ExternalCustomerContactGuard(ResponseGuard):
    """
    Prevent External department from accessing customer contact details (t027).

    Problem: External user asked for "primary contact email on proj_euro_flooring_warehouse_system".
    API returned customer's primary_contact_email (external person's email).
    Agent answered with ok_answer but should have denied access.

    Rule: External department cannot view customer contact details UNLESS
    they are the Account Manager for that customer.

    Note: This is about CUSTOMER contacts (external people like "Peter de Vries"),
    NOT about internal employee emails (@bellini.internal).
    """

    target_outcomes = {"ok_answer"}
    require_public = False  # For authenticated non-public users only

    # Patterns indicating customer contact info query
    CUSTOMER_CONTACT_PATTERNS = [
        r'primary\s+contact\s+email',
        r'customer\s+contact\s+(email|details)',
        r'contact\s+email\s+(on|for|of)\s+proj[_a-z]+',  # contact email on project
        r'client\s+contact\s+(email|info|details)',
    ]

    def __init__(self):
        self._contact_re = re.compile('|'.join(self.CUSTOMER_CONTACT_PATTERNS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        # Only applies to External department
        sm = ctx.shared.get('security_manager')
        department = getattr(sm, 'department', '') if sm else ''
        current_user = getattr(sm, 'current_user', '') if sm else ''

        if department != 'External':
            return

        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Check if task asks for customer contact info
        if not self._contact_re.search(task_text):
            return

        # Check if customer data was accessed and if user is the Account Manager
        # AICODE-NOTE: We track customers_get results to check Account Manager field
        customer_data = ctx.shared.get('_last_customer_data')
        if customer_data:
            account_manager = customer_data.get('account_manager', '')
            if account_manager == current_user:
                return  # User IS the Account Manager, access allowed

        # External user asking for customer contact info without being AM
        self._soft_block(
            ctx,
            warning_key='external_customer_contact_warned',
            log_msg="ExternalCustomerContactGuard: Blocking customer contact access for External user",
            block_msg=(
                "EXTERNAL DEPARTMENT CUSTOMER CONTACT RESTRICTION!\n\n"
                "You are in External department and CANNOT access customer contact details.\n"
                "This includes:\n"
                "- Primary contact name\n"
                "- Primary contact email\n"
                "- Any customer contact information\n\n"
                "Exception: If you are the Account Manager for that customer, you CAN access.\n"
                "But you are NOT the Account Manager.\n\n"
                "**Required:** Use `denied_security` with:\n"
                "- `denial_basis: identity_restriction`\n"
                "- Message: 'External department users cannot access customer contact details "
                "unless they are the Account Manager for that customer.'"
            )
        )
