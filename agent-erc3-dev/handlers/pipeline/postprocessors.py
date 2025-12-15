"""
Result postprocessors.

Handle side effects after successful API execution:
- Identity state updates
- Wiki synchronization
- Security redaction
- Policy hints injection
"""

from typing import Any, TYPE_CHECKING

from erc3.erc3 import client

from .base import PostProcessor
from ..enrichers import WikiHintEnricher
from utils import CLI_YELLOW, CLI_CLR

if TYPE_CHECKING:
    from ..base import ToolContext


class IdentityPostProcessor(PostProcessor):
    """
    Updates identity state after who_am_i response.

    Extracts user identity from Resp_WhoAmI and updates SecurityManager.
    """

    def can_process(self, ctx: 'ToolContext', result: Any) -> bool:
        """Process only WhoAmI responses."""
        return isinstance(result, client.Resp_WhoAmI)

    def process(self, ctx: 'ToolContext', result: Any) -> Any:
        """Update security manager with identity."""
        security_manager = ctx.shared.get('security_manager')
        if security_manager:
            identity_msg = security_manager.update_identity(result)
            if identity_msg:
                ctx.results.append(f"\n{identity_msg}\n")
        return result


class WikiSyncPostProcessor(PostProcessor):
    """
    Synchronizes wiki when SHA1 changes.

    Detects wiki version changes and injects critical documents.
    """

    def __init__(self):
        self._wiki_hints = WikiHintEnricher()

    def can_process(self, ctx: 'ToolContext', result: Any) -> bool:
        """Process responses that may contain wiki SHA1."""
        return isinstance(result, (client.Resp_WhoAmI, client.Resp_ListWiki))

    def process(self, ctx: 'ToolContext', result: Any) -> Any:
        """Sync wiki if SHA1 changed."""
        wiki_manager = ctx.shared.get('wiki_manager')
        if not wiki_manager:
            return result

        # Extract SHA1 based on response type
        sha1 = None
        if isinstance(result, client.Resp_WhoAmI):
            sha1 = result.wiki_sha1
        elif isinstance(result, client.Resp_ListWiki):
            sha1 = result.sha1

        if not sha1:
            return result

        # Sync wiki
        wiki_changed = wiki_manager.sync(sha1)

        if wiki_changed:
            self._inject_wiki_updates(ctx, wiki_manager)

        return result

    def _inject_wiki_updates(self, ctx: 'ToolContext', wiki_manager) -> None:
        """Inject critical docs and task-relevant hints."""
        critical_docs = wiki_manager.get_critical_docs()
        if critical_docs:
            print(f"  {CLI_YELLOW}Wiki changed! Injecting critical docs...{CLI_CLR}")
            ctx.results.append(
                f"\nWIKI UPDATED! You MUST read these policy documents before proceeding:\n\n"
                f"{critical_docs}\n\n"
                f"Action based on outdated rules will be REJECTED."
            )

        # Task-relevant file hint
        task = ctx.shared.get('task')
        task_text = getattr(task, 'task_text', '') if task else ''
        security_manager = ctx.shared.get('security_manager')
        is_public = getattr(security_manager, 'is_public', False) if security_manager else False

        hint = self._wiki_hints.get_task_file_hints(
            wiki_manager, task_text, is_public,
            skip_critical=True, context="wiki_change"
        )
        if hint:
            ctx.results.append(hint)


class MergerPolicyPostProcessor(PostProcessor):
    """
    Injects merger policy for public users.

    When a public user calls who_am_i and merger.md exists,
    the policy is injected to ensure compliance.
    """

    def can_process(self, ctx: 'ToolContext', result: Any) -> bool:
        """Process WhoAmI for public users when merger.md exists."""
        if not isinstance(result, client.Resp_WhoAmI):
            return False

        security_manager = ctx.shared.get('security_manager')
        if not security_manager or not security_manager.is_public:
            return False

        wiki_manager = ctx.shared.get('wiki_manager')
        return wiki_manager and wiki_manager.has_page("merger.md")

    def process(self, ctx: 'ToolContext', result: Any) -> Any:
        """Inject merger policy."""
        wiki_manager = ctx.shared.get('wiki_manager')
        merger_content = wiki_manager.get_page("merger.md")

        if merger_content:
            print(f"  {CLI_YELLOW}Public user - Injecting merger policy...{CLI_CLR}")
            ctx.results.append(
                f"\nCRITICAL POLICY - You are a PUBLIC chatbot and merger.md exists:\n\n"
                f"=== merger.md ===\n{merger_content}\n\n"
                f"YOU MUST include the acquiring company name (exactly as written in merger.md) "
                f"in EVERY response you give, regardless of the question topic."
            )

        return result


class BonusHintPostProcessor(PostProcessor):
    """
    Injects bonus policy hint for salary-related tasks.

    When task mentions bonus/salary keywords and culture.md has bonus info,
    injects a hint to check the policy.
    """

    BONUS_KEYWORDS = ['bonus', 'ny bonus', 'new year', 'eoy', 'raise salary', 'salary by']

    def can_process(self, ctx: 'ToolContext', result: Any) -> bool:
        """Process WhoAmI when task mentions bonus."""
        if not isinstance(result, client.Resp_WhoAmI):
            return False

        task_text = self._get_task_text(ctx)
        if not task_text:
            return False

        # Check for bonus keywords
        task_lower = task_text.lower()
        return any(kw in task_lower for kw in self.BONUS_KEYWORDS)

    def process(self, ctx: 'ToolContext', result: Any) -> Any:
        """Inject bonus policy hint if culture.md has bonus info."""
        wiki_manager = ctx.shared.get('wiki_manager')
        if not wiki_manager:
            return result

        # Check if culture.md exists and has bonus info
        if not wiki_manager.has_page('culture.md'):
            return result

        culture_content = wiki_manager.get_page('culture.md') or ''
        if 'bonus' not in culture_content.lower():
            return result

        print(f"  {CLI_YELLOW}📝 Bonus task detected - injecting culture.md hint{CLI_CLR}")
        ctx.results.append(
            f"\n💡 BONUS POLICY HINT: This task mentions bonus/salary. "
            f"In company culture, 'NY bonus' or 'EoY bonus' is a small token amount (5-15 EUR). "
            f"So '+10' means +10 EUR (not +10k or +10%). "
            f"As Level 1 Executive, you have authority to grant bonuses to any employee - "
            f"the 'employee of the year' tradition is just the typical use case, not a strict requirement."
        )

        return result

    def _get_task_text(self, ctx: 'ToolContext') -> str:
        """Extract task text from context."""
        task = ctx.shared.get('task')
        return getattr(task, 'task_text', '') if task else ''


class SecurityRedactionPostProcessor(PostProcessor):
    """
    Applies security redaction to results.

    Removes sensitive fields based on user permissions.
    Should run LAST in the postprocessor chain.
    """

    def can_process(self, ctx: 'ToolContext', result: Any) -> bool:
        """Always process if security manager exists."""
        return ctx.shared.get('security_manager') is not None

    def process(self, ctx: 'ToolContext', result: Any) -> Any:
        """Apply redaction."""
        security_manager = ctx.shared.get('security_manager')
        return security_manager.redact_result(result)
