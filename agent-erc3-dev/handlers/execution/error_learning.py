"""
Error learning extraction for dynamic API adaptation.
Helps agent learn from API errors without hardcoded rules.
"""
from typing import Any, Optional
from erc3.erc3 import client


def extract_learning_from_error(error: Exception, request: Any) -> Optional[str]:
    """
    Extract actionable learning hints from API errors.
    This helps the agent adapt to API behavior dynamically without hardcoded rules.

    Args:
        error: The exception that occurred
        request: The request model that caused the error

    Returns:
        Learning hint string or None
    """
    error_str = str(error).lower()

    # Pattern 1: "Not found" errors for entities
    if "not found" in error_str:
        # Time logging with invalid customer
        if isinstance(request, client.Req_LogTimeEntry) and hasattr(request, 'customer') and request.customer:
            if not request.customer.startswith('cust_'):
                return (
                    f"LEARNING: Customer '{request.customer}' not found and doesn't match format 'cust_*'. "
                    f"Possible interpretations:\n"
                    f"  1. This might be a 'work_category' code -> try: work_category='{request.customer}', customer=None\n"
                    f"  2. This might be an invalid/unknown code -> return `none_clarification_needed` asking user what this code means\n"
                    f"Valid customer IDs follow format: cust_acme_systems, cust_baltic_ports, etc.\n"
                    f"Work categories are typically: dev, design, qa, ops, or custom project codes."
                )

        # Generic not found - suggest checking ID format
        return (
            f"LEARNING: Entity not found. Double-check ID format:\n"
            f"  - Customers: 'cust_*' (e.g., cust_acme_systems)\n"
            f"  - Projects: 'proj_*' (e.g., proj_cv_poc)\n"
            f"  - Employees: username (e.g., felix_baum) or 'emp_*'"
        )

    # Pattern 2: Validation errors reveal expected formats
    if "validation error" in error_str or "should be a valid list" in error_str:
        return (
            f"LEARNING: Validation error - API expects specific format. "
            f"Common fixes:\n"
            f"  - If 'should be a valid list': wrap single values in brackets ['value']\n"
            f"  - If 'required field missing': check tool documentation for required parameters\n"
            f"  - If 'invalid type': verify parameter types (strings, numbers, booleans)"
        )

    return None
