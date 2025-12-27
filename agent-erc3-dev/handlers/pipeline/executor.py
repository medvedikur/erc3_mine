"""
Pipeline executor.

Handles API dispatch with retry logic and error handling.
"""

from typing import Any, List, TYPE_CHECKING

from erc3 import ApiException

from .base import ExecutionResult
from ..execution import (
    EmployeeUpdateStrategy, TimeEntryUpdateStrategy, ProjectTeamUpdateStrategy,
    handle_pagination_error, extract_learning_from_error, patch_null_list_response,
)

if TYPE_CHECKING:
    from ..base import ToolContext


class PipelineExecutor:
    """
    Executes API actions with strategy selection and error handling.

    Responsibilities:
    - Check for pre-processed results from specialized handlers
    - Select appropriate update strategy (fetch-merge-dispatch)
    - Execute with pagination retry
    - Extract learning from errors
    """

    def __init__(self):
        self._update_strategies = [
            EmployeeUpdateStrategy(),
            TimeEntryUpdateStrategy(),
            ProjectTeamUpdateStrategy(),
        ]

    def execute(self, ctx: 'ToolContext') -> ExecutionResult:
        """
        Execute action and return result.

        Args:
            ctx: Tool context with model, api, shared state

        Returns:
            ExecutionResult with success/failure and result/error
        """
        try:
            # Check for pre-processed results from specialized handlers
            if pre_result := self._check_preprocessed_results(ctx):
                return pre_result

            # Try update strategies
            for strategy in self._update_strategies:
                if strategy.can_handle(ctx.model):
                    result = strategy.execute(ctx.model, ctx.api, ctx.shared)
                    return ExecutionResult.ok(result)

            # Standard dispatch with retry
            result = self._dispatch_with_retry(ctx)
            return ExecutionResult.ok(result)

        except ApiException as e:
            return self._handle_api_exception(ctx, e)
        except Exception as e:
            return ExecutionResult.fail(str(e), error_type="system")

    def _check_preprocessed_results(self, ctx: 'ToolContext') -> ExecutionResult:
        """Check for results from specialized handlers (wiki, project search, etc.)."""
        # Error from specialized handler
        if '_search_error' in ctx.shared:
            error = ctx.shared.pop('_search_error')
            raise error

        # Project search result
        if '_project_search_result' in ctx.shared:
            result = ctx.shared.pop('_project_search_result')
            return ExecutionResult.ok(result)

        # Employee search result
        if '_employee_search_result' in ctx.shared:
            result = ctx.shared.pop('_employee_search_result')
            return ExecutionResult.ok(result)

        # Customer search result
        if '_customer_search_result' in ctx.shared:
            result = ctx.shared.pop('_customer_search_result')
            return ExecutionResult.ok(result)

        return None

    def _dispatch_with_retry(self, ctx: 'ToolContext') -> Any:
        """Dispatch request with pagination error retry."""
        has_limit = hasattr(ctx.model, 'limit')

        try:
            return ctx.api.dispatch(ctx.model)
        except ApiException as e:
            # Try pagination error handling
            if has_limit:
                handled, result = handle_pagination_error(e, ctx.model, ctx.api)
                if handled:
                    if result is not None:
                        return result
                    else:
                        # Unrecoverable pagination error
                        raise e

            # Try null list patching
            handled, patched = patch_null_list_response(e)
            if handled and patched is not None:
                return patched

            # Re-raise
            raise e
        except Exception as e:
            # Try null list patching for non-API errors
            handled, patched = patch_null_list_response(e)
            if handled and patched is not None:
                return patched
            raise e

    def _handle_api_exception(self, ctx: 'ToolContext', error: ApiException) -> ExecutionResult:
        """Handle API exception with learning extraction."""
        from erc3.erc3 import client

        error_msg = error.api_error.error if error.api_error else str(error)

        # Extract learning hint
        learning_hint = extract_learning_from_error(error, ctx.model)
        hints = [learning_hint] if learning_hint else []

        # AICODE-NOTE: t026 FIX - Set flag when internal customer returns "not found".
        # This flag is used by InternalProjectContactGuard to block ok_answer.
        if 'not found' in str(error).lower():
            if isinstance(ctx.model, client.Req_GetCustomer):
                customer_id = getattr(ctx.model, 'id', '') or ''
                if 'internal' in customer_id.lower() or 'bellini_internal' in customer_id.lower():
                    ctx.shared['_internal_customer_contact_blocked'] = True

        result = ExecutionResult.fail(error_msg, error_type="api")
        result.hints = hints
        return result
