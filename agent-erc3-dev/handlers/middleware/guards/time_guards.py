"""
Time Guards - validate time logging responses.

Guards:
- TimeLoggingClarificationGuard: Ensures time log clarifications include project
"""
import re
from ..base import ResponseGuard, get_task_text, has_project_reference
from ...base import ToolContext


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
