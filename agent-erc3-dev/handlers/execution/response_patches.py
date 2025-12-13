"""
Response patching for malformed API responses.
Handles cases where API returns null for list fields.
"""
from typing import Any, Optional, Tuple

from utils import CLI_YELLOW, CLI_CLR


def patch_null_list_response(error: Exception) -> Tuple[bool, Optional[Any]]:
    """
    Patch responses where API returns null for list fields.

    Args:
        error: The exception that occurred during parsing

    Returns:
        Tuple of (handled: bool, patched_result: Any or None)
    """
    error_str = str(error)

    # Check for "Input should be a valid list" error (Server returning null)
    if "valid list" not in error_str or "NoneType" not in error_str:
        return False, None

    print(f"  {CLI_YELLOW}API returned invalid list (null). Patching response.{CLI_CLR}")

    from erc3.erc3.dtos import (
        Resp_SearchWiki, Resp_ProjectSearchResults, Resp_SearchEmployees,
        Resp_SearchTimeEntries, Resp_CustomerSearchResults
    )

    if "Resp_SearchWiki" in error_str:
        return True, Resp_SearchWiki(results=[])
    elif "Resp_ProjectSearchResults" in error_str:
        return True, Resp_ProjectSearchResults(projects=[])
    elif "Resp_SearchEmployees" in error_str:
        return True, Resp_SearchEmployees(employees=[])
    elif "Resp_SearchTimeEntries" in error_str:
        # Patch with required aggregate fields
        return True, Resp_SearchTimeEntries(
            time_entries=[],
            entries=[],  # Alias check
            total_hours=0.0,
            total_billable=0.0,
            total_non_billable=0.0
        )
    elif "Resp_CustomerSearchResults" in error_str:
        return True, Resp_CustomerSearchResults(customers=[])

    return False, None
