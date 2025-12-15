"""
Unified error handler.

Provides consistent error formatting and logging.
"""

from typing import Any, Optional, TYPE_CHECKING

from .base import ExecutionResult
from utils import CLI_RED, CLI_CLR

if TYPE_CHECKING:
    from ..base import ToolContext


class ErrorHandler:
    """
    Handles errors from pipeline execution.

    Responsibilities:
    - Format error messages consistently
    - Log errors to failure logger
    - Add learning hints to context
    """

    def handle(
        self,
        ctx: 'ToolContext',
        action_name: str,
        exec_result: ExecutionResult
    ) -> None:
        """
        Handle execution error.

        Args:
            ctx: Tool context
            action_name: Name of the failed action
            exec_result: ExecutionResult with error details
        """
        error_msg = exec_result.error or "Unknown error"
        error_type = exec_result.error_type

        # Format based on error type
        if error_type == "api":
            print(f"  {CLI_RED}FAILED:{CLI_CLR} {error_msg}")
            ctx.results.append(f"Action ({action_name}): FAILED\nError: {error_msg}")
        elif error_type == "system":
            print(f"  {CLI_RED}SYSTEM ERROR:{CLI_CLR} {error_msg}")
            ctx.results.append(f"Action ({action_name}): SYSTEM ERROR\nError: {error_msg}")
        else:
            print(f"  {CLI_RED}ERROR:{CLI_CLR} {error_msg}")
            ctx.results.append(f"Action ({action_name}): ERROR\nError: {error_msg}")

        # Add learning hints
        for hint in exec_result.hints:
            ctx.results.append(f"\n{hint}\n")

        # Log to failure logger
        self._log_error(ctx, action_name, error_msg)

    def _log_error(
        self,
        ctx: 'ToolContext',
        action_name: str,
        error_msg: str
    ) -> None:
        """Log error to failure logger if available."""
        failure_logger = ctx.shared.get('failure_logger')
        task_id = ctx.shared.get('task_id')

        if not failure_logger or not task_id:
            return

        try:
            req_dict = (
                ctx.model.model_dump()
                if hasattr(ctx.model, 'model_dump')
                else str(ctx.model)
            )
            failure_logger.log_api_call(task_id, action_name, req_dict, None, error_msg)
        except Exception:
            pass  # Don't break execution on logging errors


class SuccessLogger:
    """
    Logs successful API calls.

    Separated from error handling for cleaner code.
    """

    def log(
        self,
        ctx: 'ToolContext',
        action_name: str,
        result: Any
    ) -> None:
        """Log successful API call."""
        failure_logger = ctx.shared.get('failure_logger')
        task_id = ctx.shared.get('task_id')

        if not failure_logger or not task_id:
            return

        try:
            req_dict = (
                ctx.model.model_dump()
                if hasattr(ctx.model, 'model_dump')
                else str(ctx.model)
            )
            resp_dict = (
                result.model_dump()
                if hasattr(result, 'model_dump')
                else str(result)
            )
            failure_logger.log_api_call(task_id, action_name, req_dict, resp_dict, None)
        except Exception:
            pass  # Don't break execution on logging errors
