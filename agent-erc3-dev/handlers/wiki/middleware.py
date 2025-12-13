"""
Wiki middleware for automatic wiki sync and context injection.
"""
from ..base import ToolContext, Middleware
from .manager import WikiManager


class WikiMiddleware(Middleware):
    """
    Middleware that syncs wiki when hash changes
    and injects wiki manager into context.
    """

    def __init__(self, manager: WikiManager):
        self.manager = manager

    def process(self, ctx: ToolContext) -> None:
        """Inject wiki manager into shared context."""
        ctx.shared['wiki_manager'] = self.manager
