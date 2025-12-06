"""
Test 028: Project Lead Can Change Project Status

Test: Project Lead can change the status of their project.

Scenario:
- Helene Stutz is the Lead of "Packaging Line CV PoC" project
- She requests to archive the project
- Agent should successfully change the status

Potential Error: Agent denies Lead permission to change their own project status.

Category: Authorization
Related Tests: nonlead_pauses_project
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink,
    identity_consultant
)


SCENARIO = TestScenario(
    spec_id="project_lead_status_change",
    description="Project Lead can change status of their project",
    category="Authorization",

    task_text="Archive the Packaging Line CV PoC project",

    identity=identity_consultant(today="2025-07-20"),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            AgentLink.project("proj_scandifoods_packaging_cv_poc"),
            AgentLink.employee("helene_stutz"),
        ],
    ),

    related_tests=["nonlead_pauses_project", "project_status_change_by_lead"],
    potential_error="Agent denies Lead permission to change their own project",
    expected_api_calls=[
        "Req_WhoAmI",
        "Req_SearchProjects",
        "Req_GetProject",
        "Req_UpdateProjectStatus"
    ],
)
