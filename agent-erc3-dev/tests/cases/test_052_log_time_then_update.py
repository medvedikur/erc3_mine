"""
Test 052: Log Time Then Update

Test: Log time entry, then correct the hours.

Scenario:
- Jonas logs 3 hours on Line 3 project
- Then realizes it should be 4 hours
- Agent should log first, then use time_update

Potential Error: Agent creates two separate entries instead of updating.

Category: Complex Multi-Step
Related Tests: test_039
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink,
    identity_engineer
)

SCENARIO = TestScenario(
    spec_id="log_time_then_update",
    description="Log time then correct hours",
    category="Complex Multi-Step",

    task_text="Log 3 hours on Line 3 project for today. Actually, make that 4 hours.",

    identity=identity_engineer("jonas_weiss", today="2025-07-20"),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            AgentLink.project("proj_acme_line3_cv_poc"),
        ],
    ),

    related_tests=["time_update_entry", "add_time_entry_me"],
    potential_error="Agent creates two entries instead of updating the first",
    expected_api_calls=["Req_WhoAmI", "Req_SearchProjects", "Req_LogTimeEntry"],
)
