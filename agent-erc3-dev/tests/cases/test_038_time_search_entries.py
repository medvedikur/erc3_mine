"""
Test 038: Search Own Time Entries

Test: Employee searches for their own time entries.

Scenario:
- Jonas Weiss asks to see his time entries from last week
- System has multiple time entries for Jonas
- Agent should use time_search tool to find entries

Potential Error: Agent uses time_summary instead of time_search.

Category: TimeTracking / Search
Related Tests: test_035 (summary), test_039 (update entry)
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink
)
from tests.framework.mock_data import MockWhoAmI


SCENARIO = TestScenario(
    spec_id="time_search_own_entries",
    description="Employee searches for their own time entries",
    category="TimeTracking",

    task_text="Show me my time entries from last week",

    identity=MockWhoAmI(
        is_public=False,
        user="jonas_weiss",
        name="Jonas Weiss",
        email="jonas_weiss@aetherion.com",
        department="Software Engineering",
        location="Munich",
        today="2025-07-20",  # So "last week" is around 2025-07-13 to 2025-07-19
    ),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            AgentLink.employee("jonas_weiss"),
        ],
        # Response should mention entries or hours
        message_contains=["time", "entries"],
    ),

    related_tests=["time_summary_by_employee", "time_update_entry"],
    potential_error="Agent uses summary instead of search for listing entries",
    expected_api_calls=["Req_WhoAmI", "Req_SearchTimeEntries"],
)
