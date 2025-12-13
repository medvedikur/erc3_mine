"""
M&A Compliance Guards.

Middleware guards for merger & acquisition policy enforcement.
These guards check compliance with policies defined in merger.md:
- CC code format validation for time entries
- JIRA ticket requirement for project changes
"""
import re
from typing import Optional, Set, TYPE_CHECKING

from erc3.erc3 import client

from ..base import ResponseGuard
from utils import CLI_YELLOW, CLI_CLR

if TYPE_CHECKING:
    from ...base import ToolContext


# Valid CC code format: CC-<Region>-<Unit>-<3digits>
# Examples: CC-EU-AI-042, CC-AMS-CS-017
CC_CODE_PATTERN = re.compile(r'^CC-[A-Z]{2,4}-[A-Z]{2}-\d{3}$')


class CCCodeValidationGuard(ResponseGuard):
    """
    Validates Cost Centre (CC) code format for time logging after M&A.

    Post-merger policy requires CC codes in format: CC-<Region>-<Unit>-<ProjectCode>
    where ProjectCode is exactly 3 digits.

    Triggers when:
    - Agent responds with ok_answer for time logging
    - Wiki contains merger.md (post-M&A state)
    - Task mentions CC code but in wrong format

    Action:
    - Soft block: Ask agent to request correct CC code format from user
    """

    target_outcomes = {"ok_answer"}

    def _check(self, ctx: 'ToolContext', outcome: str) -> None:
        """Check if CC code format is valid for time logging."""
        # Check if merger.md exists (post-M&A state)
        wiki_manager = ctx.shared.get('wiki_manager')
        if not wiki_manager or not wiki_manager.has_page('merger.md'):
            return

        # Get the task text to look for CC code attempts
        task = ctx.shared.get('task')
        task_text = (getattr(task, 'task_text', '') or '').lower()

        # Check if this is a time logging task
        time_keywords = ['log', 'hour', 'time', 'entry', 'work']
        if not any(kw in task_text for kw in time_keywords):
            return

        # Look for CC code patterns in task text
        potential_cc = self._extract_potential_cc(task_text)

        if potential_cc:
            # Validate the format
            if not CC_CODE_PATTERN.match(potential_cc.upper()):
                block_msg = (
                    f"⚠️ M&A COMPLIANCE: Invalid Cost Centre (CC) code format detected.\n\n"
                    f"The provided code '{potential_cc}' does not match the required format.\n"
                    f"Required format: CC-<Region>-<Unit>-<3digits>\n"
                    f"Examples: CC-EU-AI-042, CC-AMS-CS-017\n\n"
                    f"You should return `none_clarification_needed` asking the user to provide "
                    f"the CC code in the correct format. Include the project link in your response."
                )
                self._soft_block(
                    ctx,
                    warning_key='cc_code_format_warning',
                    log_msg=f"M&A Guard: Invalid CC code format: {potential_cc}",
                    block_msg=block_msg
                )

    def _extract_potential_cc(self, task_text: str) -> Optional[str]:
        """Extract potential CC code from task text."""
        # Look for explicit CC code patterns
        # Pattern: CC-XXX-XX-NNN or variations
        cc_pattern = re.compile(r'(CC-?[A-Z0-9-]+)', re.IGNORECASE)
        match = cc_pattern.search(task_text)
        if match:
            return match.group(1)

        # Look for "cost centre ABC123" style patterns
        cc_keyword_pattern = re.compile(r'(?:cost\s*cent(?:re|er)|cc)\s*[:\s]*([A-Z0-9-]+)', re.IGNORECASE)
        match = cc_keyword_pattern.search(task_text)
        if match:
            return match.group(1)

        return None


class JiraTicketRequirementGuard(ResponseGuard):
    """
    Ensures JIRA ticket is referenced for project changes after M&A.

    Post-merger policy requires JIRA ticket reference for:
    - Project status changes
    - Project structure modifications
    - Key project metadata changes

    Triggers when:
    - Agent responds with ok_answer after project modification
    - Wiki contains merger.md (post-M&A state)
    - No JIRA reference found in task

    Action:
    - Soft block: Ask agent to request JIRA ticket from user
    """

    target_outcomes = {"ok_answer"}

    # JIRA ticket pattern: PROJECT-123 or JIRA-123
    JIRA_PATTERN = re.compile(r'\b([A-Z]{2,10}-\d+)\b')

    # Keywords indicating project modification
    PROJECT_MOD_KEYWORDS = ['pause', 'archive', 'status', 'close', 'reopen', 'activate']

    def _check(self, ctx: 'ToolContext', outcome: str) -> None:
        """Check if JIRA ticket is required for project change."""
        # Check if merger.md exists (post-M&A state)
        wiki_manager = ctx.shared.get('wiki_manager')
        if not wiki_manager or not wiki_manager.has_page('merger.md'):
            return

        # Get the task text
        task = ctx.shared.get('task')
        task_text = (getattr(task, 'task_text', '') or '').lower()

        # Check if this is a project modification task
        if not any(kw in task_text for kw in self.PROJECT_MOD_KEYWORDS):
            return

        # Also check if "project" is mentioned
        if 'project' not in task_text:
            return

        # Check if JIRA ticket is mentioned
        if self.JIRA_PATTERN.search(task_text.upper()):
            return  # JIRA ticket found, proceed

        # Try to find project from links in the response
        project_id = None
        links = getattr(ctx.model, 'links', []) or []
        for link in links:
            link_id = getattr(link, 'id', '') or ''
            if link_id.startswith('proj_'):
                project_id = link_id
                break

        project_ref = f" ({project_id})" if project_id else ""

        block_msg = (
            f"⚠️ M&A COMPLIANCE: Project changes require JIRA ticket reference.\n\n"
            f"Post-merger policy (see merger.md section 'JIRA Ticket Linking for Changes') requires:\n"
            f"- All changes to project structures or key metadata must reference a JIRA ticket\n"
            f"- If change cannot be linked to JIRA ticket, default is NOT to proceed\n\n"
            f"You should return `none_clarification_needed` asking the user to provide "
            f"the JIRA ticket number for this change{project_ref}."
        )

        self._soft_block(
            ctx,
            warning_key='jira_requirement_warning',
            log_msg="M&A Guard: Project change without JIRA ticket",
            block_msg=block_msg
        )
