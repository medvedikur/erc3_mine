"""
Test 050: Project Change Member Role

Test: Project Lead changes a team member's role.

Scenario:
- Jonas Weiss is Lead of proj_acme_line3_cv_poc
- He wants to change Felix from Engineer to QA
- Valid roles: Lead, Engineer, Designer, QA, Ops, Other
- Agent should use projects_team_update with role change

Potential Error: Agent creates duplicate team entry instead of updating role.

Category: Project Team Operations
Related Tests: test_048, test_049, test_051
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink,
    identity_engineer
)

SCENARIO = TestScenario(
    spec_id="project_change_role",
    description="Lead changes member role",
    category="Project Team Operations",

    task_text="Change Felix's role to QA on the Line 3 project",

    identity=identity_engineer("jonas_weiss", today="2025-07-20"),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            AgentLink.project("proj_acme_line3_cv_poc"),
            AgentLink.employee("felix_baum"),
        ],
    ),

    related_tests=["project_add_team_member", "project_remove_team_member"],
    potential_error="Agent creates duplicate entry instead of updating role",
    expected_api_calls=["Req_WhoAmI", "Req_SearchProjects", "Req_GetProject", "Req_UpdateProjectTeam"],
)
