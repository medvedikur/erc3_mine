"""
Update strategies implementing fetch-merge-dispatch pattern.

The ERC3 API does NOT support partial updates - missing fields are cleared.
These strategies fetch current data, merge with requested changes, and send complete payload.
"""
from typing import Any, Dict, List, Optional
from abc import ABC, abstractmethod

from erc3.erc3 import client

from utils import CLI_YELLOW, CLI_CLR


def merge_non_none(payload: dict, model: Any, fields: List[str]) -> None:
    """
    Copy non-None fields from model to payload.

    Used in fetch-merge-dispatch pattern for partial updates where API
    requires all fields but we only want to update some.

    Args:
        payload: Target dict to update
        model: Source model with fields to copy
        fields: List of field names to check and copy
    """
    for field in fields:
        value = getattr(model, field, None)
        if value is not None:
            payload[field] = value


class UpdateStrategy(ABC):
    """Base class for update strategies."""

    @abstractmethod
    def can_handle(self, model: Any) -> bool:
        """Check if this strategy can handle the given model."""
        pass

    @abstractmethod
    def execute(self, model: Any, api: Any, shared: Dict[str, Any]) -> Any:
        """Execute the update with fetch-merge-dispatch pattern."""
        pass


class EmployeeUpdateStrategy(UpdateStrategy):
    """
    Handles Req_UpdateEmployeeInfo with fetch-merge-dispatch.
    API requires ALL fields to be sent, otherwise missing fields are cleared!
    """

    def can_handle(self, model: Any) -> bool:
        return isinstance(model, client.Req_UpdateEmployeeInfo)

    def execute(self, model: Any, api: Any, shared: Dict[str, Any]) -> Any:
        """
        Execute employee update:
        1. Fetch current employee data
        2. Merge with requested changes
        3. Send complete payload
        """
        employee_id = model.employee

        # Step 1: Fetch current employee data to preserve existing values
        try:
            current_data = api.get_employee(employee_id)
            emp = current_data.employee

            # Step 2: Build complete payload - start with current data
            payload = {
                'employee': employee_id,
                'notes': emp.notes if emp.notes else "",
                'location': emp.location if emp.location else "",
                'department': emp.department if emp.department else "",
                'skills': emp.skills if emp.skills else [],
                'wills': emp.wills if emp.wills else [],
            }

            # Step 3: Override with new values from the request
            if model.salary is not None:
                payload['salary'] = int(model.salary)
            if model.changed_by:
                payload['changed_by'] = model.changed_by
            merge_non_none(payload, model, ['notes', 'location', 'department', 'skills', 'wills'])

        except Exception as e:
            print(f"  {CLI_YELLOW}Could not fetch current employee data: {e}. Using request data only.{CLI_CLR}")
            # Fallback: use only what we have
            payload = {'employee': employee_id}
            if model.salary is not None:
                payload['salary'] = int(model.salary)
            if model.changed_by:
                payload['changed_by'] = model.changed_by

        # Create model with complete payload and dispatch
        update_model = client.Req_UpdateEmployeeInfo(**payload)
        return api.dispatch(update_model)


class TimeEntryUpdateStrategy(UpdateStrategy):
    """
    Handles Req_UpdateTimeEntry with fetch-merge-dispatch.
    API may clear unset fields, so we fetch current entry and merge.
    """

    def can_handle(self, model: Any) -> bool:
        return isinstance(model, client.Req_UpdateTimeEntry)

    def execute(self, model: Any, api: Any, shared: Dict[str, Any]) -> Any:
        """
        Execute time entry update:
        1. Search for the specific entry
        2. Merge with requested changes
        3. Send complete payload

        Also extracts project/employee for auto-linking.
        """
        entry_id = model.id

        # Try to fetch current time entry to preserve existing values
        try:
            # Search for this specific entry
            search_result = api.dispatch(client.Req_SearchTimeEntries(
                employee=None,
                limit=100  # Should find our entry
            ))

            current_entry = None
            if hasattr(search_result, 'entries') and search_result.entries:
                for entry in search_result.entries:
                    if entry.id == entry_id:
                        current_entry = entry
                        break

            if current_entry:
                # Save project/employee for auto-linking (time_entry is not a valid link kind!)
                time_update_entities = []
                if hasattr(current_entry, 'project') and current_entry.project:
                    time_update_entities.append({"id": current_entry.project, "kind": "project"})
                if hasattr(current_entry, 'employee') and current_entry.employee:
                    time_update_entities.append({"id": current_entry.employee, "kind": "employee"})
                shared['time_update_entities'] = time_update_entities

                # Build payload starting with current data
                payload = {
                    'id': entry_id,
                    'date': current_entry.date,
                    'hours': current_entry.hours,
                    'work_category': current_entry.work_category or "",
                    'notes': current_entry.notes or "",
                    'billable': current_entry.billable,
                    'status': current_entry.status or "",
                }

                # Override with new values
                merge_non_none(payload, model, [
                    'date', 'hours', 'work_category', 'notes', 'billable', 'status', 'changed_by'
                ])

                update_model = client.Req_UpdateTimeEntry(**payload)
                return api.dispatch(update_model)
            else:
                # Entry not found, proceed with original request
                return api.dispatch(model)

        except Exception as e:
            print(f"  {CLI_YELLOW}Could not fetch current time entry: {e}. Using request data only.{CLI_CLR}")
            return api.dispatch(model)


class ProjectTeamUpdateStrategy(UpdateStrategy):
    """
    Handles Req_UpdateProjectTeam.
    Note: For project team updates, we typically want to REPLACE the team,
    not merge. The agent should provide the complete new team.
    """

    def can_handle(self, model: Any) -> bool:
        return isinstance(model, client.Req_UpdateProjectTeam)

    def execute(self, model: Any, api: Any, shared: Dict[str, Any]) -> Any:
        """Execute project team update (replaces entire team)."""
        new_team = model.team or []

        # Warn if team is empty (might be accidental)
        if not new_team:
            print(f"  {CLI_YELLOW}Warning: Updating project team with empty team list!{CLI_CLR}")

        return api.dispatch(model)
