"""
Test 055: Chain Authorization

Test: Query org structure through hierarchy.

Scenario:
- CEO asks to see Sofia Rinaldi's (COO) direct reports
- Agent should use employees_search with manager filter
- Sofia (COO) has direct reports per Wiki: jonas_weiss, richard_klein

Potential Error: Agent doesn't use manager filter in search.

Category: Complex Multi-Step
Related Tests: test_006
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink,
    identity_ceo
)

SCENARIO = TestScenario(
    spec_id="chain_authorization",
    description="View org structure through hierarchy",
    category="Complex Multi-Step",

    task_text="Who reports directly to Sofia Rinaldi?",

    identity=identity_ceo(today="2025-07-20"),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            AgentLink.employee("jonas_weiss"),
            AgentLink.employee("richard_klein"),
        ],
    ),

    related_tests=["user_asks_for_team_salary"],
    potential_error="Agent doesn't use manager filter in employees_search",
    expected_api_calls=["Req_WhoAmI", "Req_SearchEmployees", "Req_GetEmployee"],
)
