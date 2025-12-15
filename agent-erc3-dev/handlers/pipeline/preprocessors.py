"""
Request preprocessors.

Prepare and normalize requests before API execution.
"""

from typing import TYPE_CHECKING

from erc3.erc3 import client

from .base import Preprocessor
from ..intent import detect_intent

if TYPE_CHECKING:
    from ..base import ToolContext


class EmployeeUpdatePreprocessor(Preprocessor):
    """
    Preprocesses employee update requests.

    Responsibilities:
    - Ensure salary is integer (API requirement)
    - Set changed_by from current user
    - Clear non-essential fields based on intent (salary-only vs full update)
    """

    # Fields that should be cleared for salary-only updates
    CLEARABLE_FIELDS = ['skills', 'wills', 'notes', 'location', 'department']

    def can_process(self, ctx: 'ToolContext') -> bool:
        """Process only employee update requests."""
        return isinstance(ctx.model, client.Req_UpdateEmployeeInfo)

    def process(self, ctx: 'ToolContext') -> None:
        """Normalize employee update request."""
        model = ctx.model
        task_text = self._get_task_text(ctx)

        # Detect intent to determine field handling
        intent = detect_intent(task_text)
        salary_only = intent.is_salary_only

        # Ensure salary is integer (API requirement)
        if model.salary is not None:
            model.salary = int(round(model.salary))

        # Set changed_by from current user if not set
        if not getattr(model, "changed_by", None):
            current_user = self._get_current_user(ctx)
            if current_user:
                model.changed_by = current_user

        # Clear fields based on intent
        self._clear_fields(model, salary_only)

    def _get_task_text(self, ctx: 'ToolContext') -> str:
        """Extract task text from context."""
        task = ctx.shared.get("task")
        return getattr(task, "task_text", "") or ""

    def _get_current_user(self, ctx: 'ToolContext') -> str:
        """Get current user from security manager."""
        security_manager = ctx.shared.get('security_manager')
        if security_manager:
            return getattr(security_manager, 'current_user', None)
        return None

    def _clear_fields(self, model, salary_only: bool) -> None:
        """Clear non-essential fields based on update intent."""
        if salary_only:
            # For salary-only updates, explicitly clear all other fields
            for field_name in self.CLEARABLE_FIELDS:
                setattr(model, field_name, None)
        else:
            # For other updates, only clear empty fields
            for field_name in self.CLEARABLE_FIELDS:
                val = getattr(model, field_name, None)
                if val in ([], "", None):
                    setattr(model, field_name, None)
