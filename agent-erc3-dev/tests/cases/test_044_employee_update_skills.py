"""
Test 044: Employee Update Skills

Test: Employee updates their own skills list.

Scenario:
- Helene Stutz wants to add "Python" to her skills
- She has permission to update her own profile
- Agent should use employees_update with skills field

Potential Error: Agent tries to replace all skills instead of adding.

Category: Employee Operations
Related Tests: test_045, test_046
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink,
    identity_consultant
)

SCENARIO = TestScenario(
    spec_id="employee_update_skills",
    description="Employee updates own skills",
    category="Employee Operations",

    task_text="Add Python to my skills list",

    identity=identity_consultant(today="2025-07-20"),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            AgentLink.employee("helene_stutz"),
        ],
    ),

    related_tests=["employee_update_location", "employee_update_unauthorized"],
    potential_error="Agent replaces all skills instead of adding",
    expected_api_calls=["Req_WhoAmI", "Req_GetEmployee", "Req_UpdateEmployeeInfo"],
)
