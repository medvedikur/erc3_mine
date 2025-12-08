"""
Test 048: Project Add Team Member

Test: Project Lead adds a new member to the team.

Scenario:
- Jonas Weiss is Lead of proj_acme_line3_cv_poc
- He wants to add Ana Kovac to the team as Engineer
- Agent should use projects_team_update

Potential Error: Agent replaces entire team instead of adding.

Category: Project Team Operations
Related Tests: test_049, test_050, test_051
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink,
    identity_engineer
)

SCENARIO = TestScenario(
    spec_id="project_add_team_member",
    description="Lead adds team member",
    category="Project Team Operations",

    task_text="Add Ana Kovac as Engineer to the Line 3 project",

    identity=identity_engineer("jonas_weiss", today="2025-07-20"),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            AgentLink.project("proj_acme_line3_cv_poc"),
            AgentLink.employee("ana_kovac"),
        ],
    ),

    related_tests=["project_remove_team_member", "project_change_role"],
    potential_error="Agent replaces entire team instead of adding member",
    expected_api_calls=["Req_WhoAmI", "Req_SearchProjects", "Req_GetProject", "Req_SearchEmployees", "Req_UpdateProjectTeam"],
)
