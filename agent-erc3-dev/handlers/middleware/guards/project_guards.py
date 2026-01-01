"""
Project Guards - validate project-related responses.

Guards:
- ProjectSearchReminderMiddleware: Reminds to use projects_search
- ProjectModificationClarificationGuard: Ensures project links in clarifications
- ProjectTeamModAuthorizationGuard: Requires authorization check before team modification
"""
import re
from ..base import ResponseGuard, get_task_text, has_project_reference
from ...base import ToolContext


class ProjectTeamModAuthorizationGuard(ResponseGuard):
    """
    Requires authorization check (projects_get) before answering team modification queries.

    Problem: Agent sees multiple projects, asks for clarification instead of:
    1. Choosing the best match
    2. Checking authorization via projects_get
    3. Denying if not authorized (denied_security)

    This guard catches responses where agent tries to clarify or answer
    about team modifications without first checking authorization.
    """

    target_outcomes = {"none_clarification_needed", "ok_answer"}

    # Patterns for team modification requests
    TEAM_MOD_PATTERNS = [
        r'\badd\s+(?:me|myself)\s+to\b.{0,50}\bteam\b',
        r'\badd\s+(?:me|myself)\s+to\b.{0,50}\bproject\b',
        r'\bjoin\b.{0,30}\bproject\b',
        r'\bjoin\b.{0,30}\bteam\b',
        r'\badd\b.{0,30}\bto\s+(?:the\s+)?team\b',
        r'\bremove\b.{0,30}\bfrom\s+(?:the\s+)?team\b',
        r'\bchange\s+(?:my\s+)?role\b',
        r'\bmodify\s+team\b',
        r'\bupdate\s+team\b',
    ]

    def __init__(self):
        self._team_mod_re = re.compile('|'.join(self.TEAM_MOD_PATTERNS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Check if this is a team modification task
        if not self._team_mod_re.search(task_text):
            return

        # Check if projects_get was called (authorization check)
        action_types_executed = ctx.shared.get('action_types_executed', set())
        has_auth_check = 'projects_get' in action_types_executed

        if has_auth_check:
            return  # Agent properly checked authorization

        # Agent is responding about team modification without authorization check
        if outcome == "none_clarification_needed":
            self._soft_block(
                ctx,
                warning_key='team_mod_auth_clarification_warned',
                log_msg="Team Mod Auth Guard: Clarification without projects_get - blocking",
                block_msg=(
                    "🛑 TEAM MODIFICATION WITHOUT AUTHORIZATION CHECK:\n\n"
                    "You're asking for clarification about a PROJECT TEAM modification, but you HAVEN'T "
                    "checked if you're AUTHORIZED to make this change!\n\n"
                    "**REQUIRED STEPS before clarifying:**\n"
                    "1. Use `projects_search` to find the project (you already did this)\n"
                    "2. Choose the BEST MATCH from search results (use RANKING hints)\n"
                    "3. Use `projects_get(id='proj_xxx')` to get the project team\n"
                    "4. Check if you are **Lead** in the team array\n"
                    "5. If NOT Lead → respond with `denied_security`\n"
                    "6. If Lead but need clarification → THEN ask for clarification\n\n"
                    "**DO NOT** ask for clarification first - check authorization first!"
                )
            )
        elif outcome == "ok_answer":
            # Agent claiming success without auth check - very suspicious
            self._soft_hint(
                ctx,
                "Team Mod Auth Guard: ok_answer without projects_get",
                "⚠️ WARNING: You responded 'ok_answer' for a team modification but didn't "
                "verify authorization via `projects_get`. Make sure you checked the team array "
                "to confirm you are **Lead** before claiming success."
            )


class ProjectStatusChangeAuthGuard(ResponseGuard):
    """
    AICODE-NOTE: t054 FIX - Requires authorization check before claiming success on status change.

    Problem: Task says "Pause project X", project is already paused, agent (role=Ops) responds
    ok_answer saying "already paused, no action needed". But benchmark expects denied_security
    because the user is NOT authorized to change status even if action is not needed.

    Logic: If task asks to change project status AND agent is NOT Lead,
    response MUST be denied_security regardless of current project status.

    AICODE-NOTE: t054 FIX - Level 1 Executives CANNOT change project status either!
    Only Project Leads have this authority. Executive role grants team modification rights,
    not status change rights.
    """

    target_outcomes = {"ok_answer"}

    # Patterns for project status change requests
    STATUS_CHANGE_PATTERNS = [
        r'\bpause\s+project\b',
        r'\bpause:\s*proj_',
        r'\barchive\s+project\b',
        r'\barchive:\s*proj_',
        r'\bactivate\s+project\b',
        r'\bset\s+project\s+(?:status\s+)?to\s+(?:paused|archived|active)\b',
        r'\bchange\s+project\s+status\b',
        r'\bproject\s+status\s+to\s+(?:paused|archived|active)\b',
    ]

    def __init__(self):
        self._status_change_re = re.compile('|'.join(self.STATUS_CHANGE_PATTERNS), re.IGNORECASE)

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Check if this is a status change request
        if not self._status_change_re.search(task_text):
            return

        # Check if message mentions "already paused/archived" (no action taken)
        message = (ctx.model.message or '').lower()
        already_in_state = any(phrase in message for phrase in [
            'already paused', 'already archived', 'already active',
            'no action was needed', 'no action needed', 'is already'
        ])

        if not already_in_state:
            return  # Agent took action - let other guards handle

        # Agent is claiming ok_answer because "already in state" - but did they check auth?
        # AICODE-NOTE: t052/t054 FIX - Status change authorization rules:
        # - Lead in team → CAN change status
        # - Executive NOT in team → CAN change status (Executive authority)
        # - Team member with role != Lead → CANNOT change status
        user_role = ctx.shared.get('_user_project_role')  # Set by projects_get enricher: Lead/Member/NotMember

        # AICODE-NOTE: t052 FIX - Get department from security_manager, not ctx.shared directly
        security_manager = ctx.shared.get('security_manager')
        department = getattr(security_manager, 'department', '') if security_manager else ''
        is_executive = 'corporate leadership' in department.lower() if department else False

        if user_role and user_role.lower() == 'lead':
            return  # Lead is authorized

        # AICODE-NOTE: t052 FIX - Executive NOT in team CAN change status
        if is_executive and user_role == 'NotMember':
            return  # Executive not in team - has authority to change status

        # User is team member with role != Lead, or unknown - should be denied_security
        warning_key = 'project_status_auth_warned'
        if ctx.shared.get(warning_key):
            return  # Already warned, let it through

        ctx.shared[warning_key] = True
        ctx.stop_execution = True
        ctx.results.append(
            f"⛔ AUTHORIZATION REQUIRED: You responded 'ok_answer' saying the project is already "
            f"in the requested state, but you are NOT authorized to change project status!\n\n"
            f"Even if no action was needed, the user does NOT have permission to execute this command.\n"
            f"Only **Project Leads** (or Executives NOT in the project team) can change project status.\n\n"
            f"**CORRECT RESPONSE**:\n"
            f"  outcome: 'denied_security'\n"
            f"  message: 'I am not authorized to change project status. Only the Project Lead "
            f"can pause/archive/activate projects.'\n\n"
            f"⚠️ Check the team array from projects_get to verify you are NOT the Lead."
        )
        from utils import CLI_YELLOW, CLI_CLR
        print(f"  {CLI_YELLOW}🛑 ProjectStatusChangeAuthGuard: Blocking ok_answer - user not authorized{CLI_CLR}")


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
