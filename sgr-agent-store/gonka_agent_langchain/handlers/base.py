from typing import Any, Dict, Protocol, List
from erc3 import store

class ToolContext:
    """Context object passed to handlers and middleware"""
    def __init__(self, api, action_dict: Dict[str, Any], action_model: Any):
        self.api = api
        self.raw_action = action_dict
        self.model = action_model
        self.results: List[str] = []
        self.stop_execution: bool = False

class ActionHandler(Protocol):
    """Protocol for action handlers"""
    def handle(self, ctx: ToolContext) -> None:
        ...

class Middleware(Protocol):
    """Protocol for middleware"""
    def process(self, ctx: ToolContext) -> None:
        ...

