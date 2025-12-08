"""
Test 035: Time Summary by Project

Test: Employee asks for time summary on a specific project.

Scenario:
- Jonas Weiss (Lead of Line 3 project) asks for hours logged on his project
- System has time entries for this project
- Agent should use time_summary_project tool and return aggregated data

Potential Error: Agent doesn't know about time_summary_project tool.

Category: TimeTracking / Analytics
Related Tests: test_036 (by employee), test_038 (search entries)
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink
)
from tests.framework.mock_data import MockWhoAmI


SCENARIO = TestScenario(
    spec_id="time_summary_by_project",
    description="Get time summary aggregated by project",
    category="TimeTracking",

    task_text="How many hours have been logged on the Line 3 project?",

    identity=MockWhoAmI(
        is_public=False,
        user="jonas_weiss",
        name="Jonas Weiss",
        email="jonas_weiss@aetherion.com",
        department="Software Engineering",
        location="Munich",
        today="2025-07-20",
    ),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            AgentLink.project("proj_acme_line3_cv_poc"),
        ],
        # Response should contain hours info
        message_contains=["hours", "Line 3"],
    ),

    related_tests=["time_summary_by_employee", "time_search_own_entries"],
    potential_error="Agent doesn't use time_summary_project tool",
    expected_api_calls=["Req_WhoAmI", "Req_SearchProjects", "Req_TimeSummaryByProject"],
)
