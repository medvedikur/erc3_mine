"""
Base classes and utilities for middleware.
"""
from typing import Optional, Set
from abc import ABC, abstractmethod
import re
from erc3.erc3 import client
from ..base import ToolContext, Middleware
from utils import CLI_YELLOW, CLI_GREEN, CLI_CLR


# =============================================================================
# Utility Functions
# =============================================================================

def get_task_text(ctx: ToolContext) -> str:
    """Extract task text from context."""
    task = ctx.shared.get('task')
    return getattr(task, 'task_text', '') if task else ''


def is_public_user(ctx: ToolContext) -> bool:
    """Check if current user is public/guest."""
    sm = ctx.shared.get('security_manager')
    return sm and getattr(sm, 'is_public', False)


def has_project_reference(message: str, links: list) -> bool:
    """Check if response contains project reference in message or links."""
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


# =============================================================================
# Base Classes
# =============================================================================

class ResponseGuard(Middleware, ABC):
    """
    Base class for middleware that intercepts Req_ProvideAgentResponse.

    Subclasses define:
    - target_outcomes: Set of outcomes to intercept (empty = all)
    - require_public: True = only for public users, False = only for non-public, None = both
    - _check(): Custom validation logic
    """

    # Override in subclasses
    target_outcomes: Set[str] = set()  # Empty = all outcomes
    require_public: Optional[bool] = None  # None = both, True = public only, False = non-public only

    def process(self, ctx: ToolContext) -> None:
        # Only intercept respond calls
        if not isinstance(ctx.model, client.Req_ProvideAgentResponse):
            return

        outcome = ctx.model.outcome or ""

        # Filter by target outcomes
        if self.target_outcomes and outcome not in self.target_outcomes:
            return

        # Filter by public/non-public user
        user_is_public = is_public_user(ctx)
        if self.require_public is True and not user_is_public:
            return
        if self.require_public is False and user_is_public:
            return

        # Delegate to subclass
        self._check(ctx, outcome)

    @abstractmethod
    def _check(self, ctx: ToolContext, outcome: str) -> None:
        """Override with specific validation logic."""
        pass

    # === Helper methods for subclasses ===

    def _soft_hint(self, ctx: ToolContext, log_msg: str, hint_msg: str) -> None:
        """Add a non-blocking hint to results."""
        print(f"  {CLI_YELLOW}💡 {log_msg}{CLI_CLR}")
        ctx.results.append(hint_msg)

    def _soft_block(self, ctx: ToolContext, warning_key: str, log_msg: str, block_msg: str) -> bool:
        """
        Block first time, allow on repeat.
        Returns True if blocked, False if allowed through.
        """
        if ctx.shared.get(warning_key):
            print(f"  {CLI_GREEN}✓ {self.__class__.__name__}: Confirmed after warning{CLI_CLR}")
            return False

        print(f"  {CLI_YELLOW}🛑 {log_msg}{CLI_CLR}")
        ctx.shared[warning_key] = True
        ctx.stop_execution = True
        ctx.results.append(block_msg)
        return True
