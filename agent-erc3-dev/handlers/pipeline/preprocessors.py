"""
Request preprocessors.

Prepare and normalize requests before API execution.
"""
import re
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

        # AICODE-NOTE: t037 FIX - Track notes for salary injection guard
        # Store employee ID and notes text so guard can detect salary-related notes
        # AICODE-NOTE: Req_UpdateEmployeeInfo uses 'employee' field, not 'id'
        notes = getattr(model, 'notes', None)
        if notes:
            employee_id = getattr(model, 'employee', None)
            if employee_id:
                employee_notes_updated = ctx.shared.get('employee_notes_updated', {})
                employee_notes_updated[employee_id] = notes
                ctx.shared['employee_notes_updated'] = employee_notes_updated

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


class SkillNameCorrectionPreprocessor(Preprocessor):
    """
    AICODE-NOTE: t056 FIX - Automatically correct skill names before API call.

    Problem: When task asks for "CRM system usage skills", agent uses "skill_crm"
    but the correct skill is "skill_crm_systems". The hint comes AFTER API returns
    results, so agent ignores it.

    Solution: Before API call, check if task contains specificity hints (like "system")
    and auto-correct the skill name.

    Correction rules:
    - skill_crm + task contains "system" -> skill_crm_systems
    """

    # Mapping of base skill -> (specificity word, correct skill)
    SKILL_CORRECTIONS = {
        'skill_crm': [
            (['system', 'systems', 'system usage'], 'skill_crm_systems'),
        ],
    }

    def can_process(self, ctx: 'ToolContext') -> bool:
        """Process only employee search requests with skills filter."""
        return isinstance(ctx.model, client.Req_SearchEmployees) and ctx.model.skills

    def process(self, ctx: 'ToolContext') -> None:
        """Correct skill names based on task context."""
        model = ctx.model
        task_text = self._get_task_text(ctx).lower()

        if not model.skills:
            return

        corrected = False
        new_skills = []

        for skill in model.skills:
            skill_name = skill.name
            corrected_name = self._get_corrected_skill(skill_name, task_text)

            if corrected_name != skill_name:
                print(f"  🔧 [t056 fix] Auto-correcting skill: {skill_name} -> {corrected_name}")
                skill.name = corrected_name
                corrected = True

            new_skills.append(skill)

        if corrected:
            model.skills = new_skills

    def _get_task_text(self, ctx: 'ToolContext') -> str:
        """Extract task text from context."""
        task = ctx.shared.get("task")
        return getattr(task, "task_text", "") or ""

    def _get_corrected_skill(self, skill_name: str, task_text: str) -> str:
        """Get corrected skill name if task contains specificity hints."""
        if skill_name not in self.SKILL_CORRECTIONS:
            return skill_name

        for specificity_words, correct_skill in self.SKILL_CORRECTIONS[skill_name]:
            for word in specificity_words:
                if word in task_text:
                    return correct_skill

        return skill_name


class SendToLocationPreprocessor(Preprocessor):
    """
    AICODE-NOTE: t013 FIX - Warn when agent uses location filter with "send to" pattern.

    Problem: Task says "send employee to Milano" and agent filters by location='HQ – Italy'
    thinking employees must BE in Milano. But "send TO" means DESTINATION, not current location.

    Solution: When task contains "send to [location]"/"assign to [location]" patterns AND
    agent uses location filter in employees_search → add warning hint.
    """

    # Patterns indicating destination, not current location
    SEND_TO_PATTERNS = [
        re.compile(r'\bsend\s+(?:\w+\s+)?to\s+(\w+)', re.IGNORECASE),
        re.compile(r'\bassign\s+(?:\w+\s+)?to\s+(\w+)', re.IGNORECASE),
        re.compile(r'\bdispatch\s+(?:\w+\s+)?to\s+(\w+)', re.IGNORECASE),
        re.compile(r'\btravel\s+to\s+(\w+)', re.IGNORECASE),
    ]

    def can_process(self, ctx: 'ToolContext') -> bool:
        """Process only employee search requests with location filter."""
        if not isinstance(ctx.model, client.Req_SearchEmployees):
            return False
        # Check if location filter is being used
        return getattr(ctx.model, 'location', None) is not None

    def process(self, ctx: 'ToolContext') -> None:
        """Check for send-to pattern and warn about location filter misuse."""
        task_text = self._get_task_text(ctx)
        if not task_text:
            return

        # Check if task has "send to" pattern
        for pattern in self.SEND_TO_PATTERNS:
            match = pattern.search(task_text)
            if match:
                destination = match.group(1)
                location_filter = ctx.model.location

                # Add warning to results
                warning = (
                    f"\n⚠️ **LOCATION FILTER WARNING** (t013 fix):\n"
                    f"Task says 'send/assign to {destination}' - this is a DESTINATION, not current location!\n"
                    f"You are filtering by location='{location_filter}', which may EXCLUDE valid candidates.\n\n"
                    f"🔴 **REMOVE location filter!** Search ALL employees, not just those at {location_filter}.\n"
                    f"   Correct: employees_search(skills=[...]) WITHOUT location filter\n"
                    f"   Wrong: employees_search(skills=[...], location='{location_filter}')\n\n"
                    f"The employee will be SENT to {destination}, they don't need to be there NOW."
                )
                ctx.results.append(warning)
                print(f"  ⚠️ [t013 fix] Warning: location filter with 'send to' pattern detected")
                return

    def _get_task_text(self, ctx: 'ToolContext') -> str:
        """Extract task text from context."""
        task = ctx.shared.get("task")
        return getattr(task, "task_text", "") or ""
