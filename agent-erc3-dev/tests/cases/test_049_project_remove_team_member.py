"""
Test 049: Project Remove Team Member

Test: Project Lead removes a member from the team.

Scenario:
- Jonas Weiss is Lead of proj_acme_line3_cv_poc
- He wants to remove a team member
- Agent should use projects_team_update with reduced team

Potential Error: Agent doesn't preserve other team members.

Category: Project Team Operations
Related Tests: test_048, test_050, test_051
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink,
    identity_engineer
)

SCENARIO = TestScenario(
    spec_id="project_remove_team_member",
    description="Lead removes team member",
    category="Project Team Operations",

    task_text="Remove Felix Baum from the Line 3 project team",

    identity=identity_engineer("jonas_weiss", today="2025-07-20"),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            AgentLink.project("proj_acme_line3_cv_poc"),
            AgentLink.employee("felix_baum"),
        ],
    ),

    related_tests=["project_add_team_member", "project_change_role"],
    potential_error="Agent doesn't preserve other team members when removing",
    expected_api_calls=["Req_WhoAmI", "Req_SearchProjects", "Req_GetProject", "Req_UpdateProjectTeam"],
)
