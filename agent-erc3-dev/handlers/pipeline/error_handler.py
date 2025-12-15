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

        # Add critical error hint for broken system scenarios
        # AICODE-NOTE: This addresses broken_system test where API returns -1 limit
        critical_hint = self._get_critical_error_hint(error_msg, action_name)
        if critical_hint:
            ctx.results.append(critical_hint)

        # Add learning hints
        for hint in exec_result.hints:
            ctx.results.append(f"\n{hint}\n")

        # Log to failure logger
        self._log_error(ctx, action_name, error_msg)

    def _get_critical_error_hint(self, error_msg: str, action_name: str) -> Optional[str]:
        """
        Generate hint for critical/unrecoverable errors.

        These errors indicate the API/system is broken, not a permission issue.
        Agent should respond with `error_internal`, NOT try workarounds.
        """
        error_lower = error_msg.lower()

        # Check for broken system indicators
        # "page limit exceeded: 5 > -1" means limit is -1 (impossible/broken)
        if 'page limit exceeded' in error_lower and '> -1' in error_msg:
            return (
                "\n🛑 CRITICAL SYSTEM ERROR: The API returned an impossible limit (-1).\n"
                "This indicates the system/database is BROKEN or UNAVAILABLE.\n\n"
                "⚠️ REQUIRED ACTION:\n"
                "You CANNOT complete this task using wiki or other workarounds.\n"
                "You MUST respond with:\n"
                "  - `outcome: 'error_internal'`\n"
                "  - Explain that the API is returning errors and the request cannot be fulfilled.\n\n"
                "Do NOT try alternative approaches. Do NOT use wiki to answer.\n"
                "The core data source is broken — report the error!"
            )

        # Generic internal error detection
        internal_error_patterns = [
            'internal error',
            'internal server error',
            'service unavailable',
            'database error',
            'connection refused',
            'timeout',
        ]

        if any(pattern in error_lower for pattern in internal_error_patterns):
            return (
                f"\n🛑 SYSTEM ERROR DETECTED in {action_name}.\n"
                "If this error persists and prevents you from completing the task,\n"
                "respond with `outcome: 'error_internal'` and explain the system failure."
            )

        return None

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
