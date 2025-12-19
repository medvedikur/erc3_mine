"""
Time Guards - validate time logging responses.

Guards:
- TimeLoggingClarificationGuard: Ensures time log clarifications include project
- TimeLoggingAuthorizationGuard: Ensures projects_get called before denying time log
"""
import re
from ..base import ResponseGuard, get_task_text, has_project_reference
from ...base import ToolContext
from utils import CLI_GREEN


def _is_time_logging_action(task_text: str) -> bool:
    """
    Detect if task is a TIME LOGGING ACTION (not just an inquiry about time tracking).

    AICODE-NOTE: t061 fix - distinguish between:
    - ACTION: "log 3 hours on project X" → requires authorization check
    - INQUIRY: "where can I read about time tracking" → just an info request

    Returns True only for actual time logging/entry operations.
    """
    if not task_text:
        return False

    task_lower = task_text.lower()

    # EXCLUDE: Informational queries about time tracking documentation
    # These are NOT time logging actions!
    info_patterns = [
        r'\bread\s+about\b.*time',      # "read about time tracking"
        r'\bwhere\b.*time\s+track',      # "where can I... time tracking"
        r'\bhow\b.*time\s+track',        # "how does time tracking work"
        r'\bwhat\b.*time\s+track',       # "what is time tracking"
        r'\bdocument',                   # documentation queries
        r'\bpolicy|policies\b',          # policy queries
    ]
    for pattern in info_patterns:
        if re.search(pattern, task_lower):
            return False

    # INCLUDE: Actual time logging actions
    action_patterns = [
        r'\blog\s+\d+\s*hours?\b',       # "log 3 hours"
        r'\b\d+\s*hours?\s+of\b',        # "3 hours of work"
        r'\bbillable\s+work\b',          # "billable work"
        r'\blog\s+time\b',               # "log time" (imperative)
        r'\btime\s+entry\b',             # "time entry"
        r'\brecord\s+\d+\s*hours?\b',    # "record 3 hours"
    ]
    for pattern in action_patterns:
        if re.search(pattern, task_lower):
            return True

    return False


class TimeLoggingClarificationGuard(ResponseGuard):
    """
    Ensures time logging clarification requests include project links.

    Problem: Agent asks for CC codes but didn't identify the project.
    The benchmark expects project links even in clarification responses.
    """

    target_outcomes = {"none_clarification_needed"}

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Check if this is a time logging ACTION (not just inquiry)
        if not _is_time_logging_action(task_text):
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


class TimeLoggingAuthorizationGuard(ResponseGuard):
    """
    Ensures agent calls projects_get before denying time logging requests.

    Problem: Agent may deny time logging without verifying team membership
    via projects_get. The projects_search response doesn't include team info,
    so agent must call projects_get to check if they're a member.

    This guard blocks denied_security for time logging tasks if projects_get
    wasn't called, prompting the agent to verify authorization properly.
    """

    target_outcomes = {"denied_security"}

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        task_text = get_task_text(ctx)
        if not task_text:
            return

        # Only apply to time logging ACTIONS (not inquiries about time tracking)
        if not _is_time_logging_action(task_text):
            return

        # Check if projects_get was called
        action_types_executed = ctx.shared.get('action_types_executed', set())
        if 'projects_get' in action_types_executed:
            print(f"  {CLI_GREEN}✓ TimeLog Auth: projects_get called, authorization verified{CLI_GREEN}")
            return  # Agent properly verified

        self._soft_block(
            ctx,
            warning_key='time_log_auth_warned',
            log_msg="TimeLog Auth Guard: denied_security without projects_get",
            block_msg=(
                "🛑 TIME LOGGING AUTHORIZATION: You're denying a time logging request, "
                "but you HAVEN'T called `projects_get` to verify team membership!\n\n"
                "**AUTHORIZATION CHECK REQUIRED:**\n"
                "1. Call `projects_get(id='proj_xxx')` to see the **team** array\n"
                "2. Check if YOU or the TARGET EMPLOYEE is in the team\n"
                "3. If logging for YOURSELF and you're a team member → AUTHORIZED\n"
                "4. If logging for OTHERS, check if you're Lead/Account Manager/Direct Manager\n\n"
                "`projects_search` does NOT return team info - only `projects_get` does!\n"
                "Verify your authorization before denying."
            )
        )
