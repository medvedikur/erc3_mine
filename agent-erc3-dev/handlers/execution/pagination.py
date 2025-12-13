"""
Pagination error handling for API requests.
"""
import re
from typing import Any, Tuple, Optional
from erc3 import ApiException

from utils import CLI_YELLOW, CLI_CLR


def handle_pagination_error(
    error: ApiException,
    model: Any,
    api: Any
) -> Tuple[bool, Optional[Any]]:
    """
    Handle "page limit exceeded" errors by retrying with allowed limit.

    Args:
        error: The ApiException that occurred
        model: The request model with limit field
        api: The API client to retry with

    Returns:
        Tuple of (handled: bool, result: Any or None)
        If handled=True and result is not None, the retry succeeded
        If handled=True and result is None, the error was unrecoverable
        If handled=False, this error type is not handled
    """
    error_str = str(error).lower()

    if "page limit exceeded" not in error_str:
        return False, None

    if not hasattr(model, 'limit'):
        return False, None

    # Parse max limit from error like "page limit exceeded: 5 > 3" or "1 > -1"
    match = re.search(r'(\d+)\s*>\s*(-?\d+)', str(error))
    if match:
        max_limit = int(match.group(2))
        if max_limit <= 0:
            # API says no pagination allowed at all - this is a system restriction
            print(f"  {CLI_YELLOW}API forbids pagination (max_limit={max_limit}). Cannot retrieve data.{CLI_CLR}")
            return True, None  # Handled but unrecoverable
        else:
            # Retry with allowed limit
            print(f"  {CLI_YELLOW}Page limit exceeded. Retrying with limit={max_limit}.{CLI_CLR}")
            model.limit = max_limit
            result = api.dispatch(model)
            return True, result
    else:
        # Can't parse, try with limit=1
        print(f"  {CLI_YELLOW}Page limit exceeded. Retrying with limit=1.{CLI_CLR}")
        model.limit = 1
        result = api.dispatch(model)
        return True, result
