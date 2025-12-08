"""
Test 054: Cross Entity Query

Test: Query requiring multiple entity types.

Scenario:
- COO asks "Who leads the Munich Edge AI Platform project?"
- Agent needs to search projects, get project details, find lead
- proj_munich_edge_ai is led by lukas_brenner
- COO (Level 2) has access to all project team data

Potential Error: Agent doesn't call projects_get to see team/lead.

Category: Complex Multi-Step
Related Tests: test_011
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink,
    identity_coo
)

SCENARIO = TestScenario(
    spec_id="cross_entity_query",
    description="Query across projects and employees",
    category="Complex Multi-Step",

    task_text="Who leads the Munich Edge AI Platform project?",

    identity=identity_coo(today="2025-07-20"),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            AgentLink.employee("lukas_brenner"),
            AgentLink.project("proj_munich_edge_ai"),
        ],
    ),

    related_tests=["find_cv_lead_in_vienna"],
    potential_error="Agent doesn't call projects_get to see team/lead",
    expected_api_calls=["Req_WhoAmI", "Req_SearchProjects", "Req_GetProject"],
)
