"""
Test 047: Employee Search by Department

Test: Search employees by department and location.

Scenario:
- COO asks for all Software Engineering team in Munich
- Agent should use employees_search with filters
- Return list of matching employees
- COO (Level 2) has access to search all employees

Potential Error: Agent doesn't filter properly or returns all employees.

Category: Employee Operations
Related Tests: test_024
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink,
    identity_coo
)

SCENARIO = TestScenario(
    spec_id="employee_search_department",
    description="Search employees by department and location",
    category="Employee Operations",

    task_text="Who works in Software Engineering in Munich?",

    identity=identity_coo(today="2025-07-20"),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            AgentLink.employee("mira_schaefer"),  # Only SW Eng in Munich after hierarchy fix
        ],
    ),

    related_tests=["ask_for_an_email_1"],
    potential_error="Agent doesn't filter by both department and location",
    expected_api_calls=["Req_WhoAmI", "Req_SearchEmployees"],
)
