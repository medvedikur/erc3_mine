"""
Base types for action handling.

Provides ToolContext and protocol definitions.
"""

from typing import Any, Dict, Protocol, List

from .context import SharedState, SharedStateProxy


class ToolContext:
    """
    Context object passed to handlers and middleware.

    Provides:
    - api: ERC3 client for API calls
    - raw_action: Original action dict from LLM
    - model: Parsed Pydantic request model
    - results: List of result strings to return to agent
    - stop_execution: Flag to halt further processing
    - shared: Typed shared state (Dict-compatible proxy over SharedState)
    - state: Direct access to typed SharedState

    AICODE-NOTE: shared is now a SharedStateProxy that provides dict-like access
    to the underlying typed SharedState. This maintains backward compatibility
    while adding type safety. Use ctx.state for direct typed access.
    """

    def __init__(self, api, action_dict: Dict[str, Any], action_model: Any):
        self.api = api
        self.raw_action = action_dict
        self.model = action_model
        self.results: List[str] = []
        self.stop_execution: bool = False

        # Typed state with dict-compatible proxy
        self._state = SharedState(api=api)
        self.shared = SharedStateProxy(self._state)

    @property
    def state(self) -> SharedState:
        """
        Direct access to typed shared state.

        Use this for type-safe access:
            ctx.state.security_manager  # typed as Optional[SecurityManager]
            ctx.state.task_id           # typed as Optional[str]

        Use ctx.shared for dict-like access (backward compatible):
            ctx.shared['security_manager']
            ctx.shared.get('task_id')
        """
        return self._state


class ActionHandlerProtocol(Protocol):
    """
    Protocol for action handlers.

    Legacy protocol - prefer using action_handlers.base.ActionHandler ABC.
    """

    def handle(self, ctx: ToolContext) -> None:
        ...


class Middleware(Protocol):
    """
    Protocol for middleware.

    Middleware processes context before handlers run.
    Can modify ctx.results, ctx.shared, or set ctx.stop_execution.
    """

    def process(self, ctx: ToolContext) -> None:
        ...
