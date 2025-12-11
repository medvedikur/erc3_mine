"""
Tools module.

Provides tool parsing, link extraction, and SDK model patching.
"""
from .registry import ToolParser, ParseContext, ParseError
from .links import LinkExtractor
from .patches import SafeReq_UpdateEmployeeInfo
from .parser import parse_action

__all__ = [
    'ToolParser',
    'ParseContext',
    'ParseError',
    'LinkExtractor',
    'SafeReq_UpdateEmployeeInfo',
    'parse_action',
]
