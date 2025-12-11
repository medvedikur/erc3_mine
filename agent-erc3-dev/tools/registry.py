"""
Tool parser registry.

Provides the ToolParser registry class with automatic dispatch.
"""
from typing import Any, Callable, Dict, List, Optional


class ParseContext:
    """Context passed to tool parsers with pre-processed data."""
    __slots__ = ('args', 'raw_args', 'context', 'current_user')

    def __init__(self, args: dict, raw_args: dict, context: Any, current_user: Optional[str]):
        self.args = args
        self.raw_args = raw_args
        self.context = context
        self.current_user = current_user


class ToolParser:
    """
    Registry of tool parsers with automatic dispatch.

    Usage:
        @ToolParser.register("whoami", "me", "identity")
        def _parse_who_am_i(ctx: ParseContext) -> Any:
            return client.Req_WhoAmI()
    """
    _parsers: Dict[str, Callable[[ParseContext], Any]] = {}

    @classmethod
    def register(cls, *names: str):
        """Decorator to register a parser function for one or more tool names."""
        def decorator(func: Callable[[ParseContext], Any]) -> Callable[[ParseContext], Any]:
            for name in names:
                # Normalize name same way as in parse()
                normalized = name.lower().replace("_", "").replace("-", "").replace("/", "")
                cls._parsers[normalized] = func
            return func
        return decorator

    @classmethod
    def get_parser(cls, tool_name: str) -> Optional[Callable[[ParseContext], Any]]:
        """Get parser for a tool name (normalized)."""
        normalized = tool_name.lower().replace("_", "").replace("-", "").replace("/", "")
        return cls._parsers.get(normalized)

    @classmethod
    def list_tools(cls) -> List[str]:
        """List all registered tool names."""
        return sorted(cls._parsers.keys())


class ParseError:
    """
    Represents a parsing error with a message to return to the LLM.
    Used instead of None to provide meaningful feedback.
    """
    def __init__(self, message: str, tool: str = None):
        self.message = message
        self.tool = tool

    def __str__(self):
        if self.tool:
            return f"Tool '{self.tool}': {self.message}"
        return self.message
