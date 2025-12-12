"""
Project Guards - validate project-related responses.

Guards:
- ProjectSearchReminderMiddleware: Reminds to use projects_search
- ProjectModificationClarificationGuard: Ensures project links in clarifications
"""
import re
from ..base import ResponseGuard, get_task_text, has_project_reference
from ...base import ToolContext


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
