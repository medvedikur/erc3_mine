"""
Base classes for action handlers.

Action handlers implement the Strategy pattern - each handler knows how to
process a specific type of action (wiki search, project search, etc.)
"""
from abc import ABC, abstractmethod
from typing import Any, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..base import ToolContext


class ActionHandler(ABC):
    """
    Base class for action-specific handlers.

    Each handler processes one or more action types. The CompositeHandler
    delegates to the first handler that can handle the action.
    """

    @abstractmethod
    def can_handle(self, ctx: 'ToolContext') -> bool:
        """
        Check if this handler can process the given action.

        Args:
            ctx: Tool context with action model

        Returns:
            True if this handler should process the action
        """
        pass

    @abstractmethod
    def handle(self, ctx: 'ToolContext') -> bool:
        """
        Handle the action.

        Args:
            ctx: Tool context with action model, API, and shared state

        Returns:
            True if handled successfully, False to pass to next handler
        """
        pass


class CompositeActionHandler:
    """
    Chains multiple handlers using Chain of Responsibility pattern.

    Delegates to the first handler that can handle the action.
    If no handler matches, delegates to the default handler.
    """

    def __init__(self, handlers: List[ActionHandler], default_handler: Any):
        """
        Initialize composite handler.

        Args:
            handlers: List of specialized handlers to try in order
            default_handler: Fallback handler for unmatched actions (duck typed, needs handle() method)
        """
        self.handlers = handlers
        self.default_handler = default_handler

    def handle(self, ctx: 'ToolContext') -> None:
        """
        Route action to appropriate handler.

        Tries each specialized handler in order. If a handler's can_handle()
        returns True and handle() returns True, processing stops.
        Otherwise, falls back to default_handler.

        Args:
            ctx: Tool context with action model
        """
        for handler in self.handlers:
            if handler.can_handle(ctx):
                if handler.handle(ctx):
                    return

        # No specialized handler matched or handled, use default
        self.default_handler.handle(ctx)
