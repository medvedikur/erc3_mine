"""
Test 036: Time Summary by Employee

Test: Manager asks for time summary for a specific employee.

Scenario:
- Richard Klein (COO, manager of Jonas) asks for Jonas's billable hours
- System has time entries for Jonas
- Agent should use time_summary_employee tool and return aggregated data

Potential Error: Agent searches time entries manually instead of using summary.

Category: TimeTracking / Analytics
Related Tests: test_035 (by project), test_038 (search entries)
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink
)
from tests.framework.mock_data import MockWhoAmI


SCENARIO = TestScenario(
    spec_id="time_summary_by_employee",
    description="Get time summary for specific employee",
    category="TimeTracking",

    task_text="Show me Jonas Weiss's billable hours for this month",

    identity=MockWhoAmI(
        is_public=False,
        user="richard_klein",
        name="Richard Klein",
        email="richard_klein@aetherion.com",
        department="Operations",
        location="Munich",
        today="2025-07-20",
    ),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            AgentLink.employee("jonas_weiss"),
        ],
        # Response should contain hours info
        message_contains=["hours"],
    ),

    related_tests=["time_summary_by_project", "time_search_own_entries"],
    potential_error="Agent doesn't use time_summary_employee tool",
    expected_api_calls=["Req_WhoAmI", "Req_SearchEmployees", "Req_TimeSummaryByEmployee"],
)
