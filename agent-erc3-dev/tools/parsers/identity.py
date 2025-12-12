"""
Identity tool parsers.
"""
from typing import Any
from erc3.erc3 import client
from ..registry import ToolParser, ParseContext


@ToolParser.register("whoami", "who_am_i", "me", "identity")
def _parse_who_am_i(ctx: ParseContext) -> Any:
    """Get current user identity and context."""
    return client.Req_WhoAmI()
