"""
Main tool parsing logic.

Provides the parse_action() function.
Individual tool parsers are in the parsers/ submodule.
"""
from typing import Any, Optional

from .registry import ToolParser, ParseContext, ParseError
from .normalizers import normalize_args, inject_context, detect_placeholders

# Import all parsers to register them (side effect)
from . import parsers  # noqa: F401


# =============================================================================
# Main Parse Function
# =============================================================================

def parse_action(action_dict: dict, context: Any = None) -> Optional[Any]:
    """
    Parse action dict into Pydantic model for Erc3Client.

    Uses ToolParser registry for dispatch.

    Args:
        action_dict: Dict with 'tool' and 'args' keys
        context: Optional context with security_manager

    Returns:
        Parsed request model or ParseError
    """
    tool = action_dict.get("tool", "").lower().replace("_", "").replace("-", "").replace("/", "")

    # Flatten args
    raw_args = action_dict.get("args", {})
    if raw_args:
        combined_args = {**action_dict, **raw_args}
    else:
        combined_args = action_dict

    args = combined_args.copy()

    # Detect placeholders
    placeholder_error = detect_placeholders(args)
    if placeholder_error:
        return ParseError(placeholder_error, tool=tool)

    # Normalize args
    args = normalize_args(args)

    # Inject context
    if context:
        args = inject_context(args, context)

    # Get current user
    current_user = None
    if context and hasattr(context, 'shared'):
        sm = context.shared.get('security_manager')
        if sm:
            current_user = sm.current_user

    # Create parse context
    ctx = ParseContext(
        args=args,
        raw_args=raw_args,
        context=context,
        current_user=current_user
    )

    # Dispatch to parser
    parser = ToolParser.get_parser(tool)
    if parser:
        return parser(ctx)

    # Unknown tool
    registered_tools = ", ".join(sorted(set(
        name for name in ToolParser._parsers.keys()
        if "_" not in name
    )))
    return ParseError(
        f"Unknown tool '{tool}'. Available: {registered_tools}. Check spelling.",
        tool=tool
    )
