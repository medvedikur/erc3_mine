"""
Test 045: Employee Update Location (CEO)

Test: CEO (Level 1) updates employee's location.

Scenario:
- Elena Vogel (CEO, Level 1) updates Jonas Weiss's location
- Only Level 1 can modify employee personal data per rulebook
- Agent should verify Level 1 authorization before updating

Potential Error: Agent doesn't check authorization level.

Category: Employee Operations
Related Tests: test_044, test_046
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink,
    identity_ceo
)

SCENARIO = TestScenario(
    spec_id="employee_update_location",
    description="CEO updates employee location",
    category="Employee Operations",

    task_text="Change Jonas Weiss's location to Berlin",

    identity=identity_ceo(today="2025-07-20"),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            AgentLink.employee("jonas_weiss"),
        ],
    ),

    related_tests=["employee_update_skills", "employee_update_unauthorized"],
    potential_error="Agent doesn't verify Level 1 authorization",
    expected_api_calls=["Req_WhoAmI", "Req_SearchEmployees", "Req_GetEmployee", "Req_UpdateEmployeeInfo"],
)
