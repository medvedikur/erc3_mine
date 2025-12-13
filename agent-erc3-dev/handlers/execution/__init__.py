"""
Execution strategies for different action types.

Provides specialized execution logic for actions that need
special handling (fetch-merge-dispatch, enrichments, etc.)
"""
from .update_strategies import (
    EmployeeUpdateStrategy,
    TimeEntryUpdateStrategy,
    ProjectTeamUpdateStrategy,
)
from .pagination import handle_pagination_error
from .error_learning import extract_learning_from_error
from .response_patches import patch_null_list_response

__all__ = [
    'EmployeeUpdateStrategy',
    'TimeEntryUpdateStrategy',
    'ProjectTeamUpdateStrategy',
    'handle_pagination_error',
    'extract_learning_from_error',
    'patch_null_list_response',
]
