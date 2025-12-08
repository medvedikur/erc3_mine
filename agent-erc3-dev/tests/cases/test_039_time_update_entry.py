"""
Test 039: Update Time Entry

Test: Employee updates their own time entry.

Scenario:
- Jonas Weiss wants to update his time entry from July 15th
- He wants to change hours from 6 to 7
- Agent should search for entry, then update it

Potential Error: Agent tries to log new entry instead of updating existing.

Category: TimeTracking / Mutation
Related Tests: test_038 (search), add_time_entry_me
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink
)
from tests.framework.mock_data import MockWhoAmI


SCENARIO = TestScenario(
    spec_id="time_update_entry",
    description="Employee updates their own time entry",
    category="TimeTracking",

    task_text="Update my time entry from July 15th on Line 3 project - change hours to 7",

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
            AgentLink.employee("jonas_weiss"),
            AgentLink.project("proj_acme_line3_cv_poc"),
        ],
        # Response should confirm update
        message_contains=["update", "7"],
    ),

    related_tests=["time_search_own_entries", "add_time_entry_me"],
    potential_error="Agent logs new entry instead of updating existing one",
    expected_api_calls=["Req_WhoAmI", "Req_SearchTimeEntries", "Req_UpdateTimeEntry"],
)
