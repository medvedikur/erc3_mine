"""
Test 053: Find and Archive Project

Test: Find project by description and archive it.

Scenario:
- Jonas asks to archive the "computer vision defect detection" project
- Agent should search by description, find Line 3 PoC
- Then archive it (Jonas is Lead)

Potential Error: Agent doesn't find project by description.

Category: Complex Multi-Step
Related Tests: test_007, test_014
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink,
    identity_engineer
)

SCENARIO = TestScenario(
    spec_id="find_and_archive_project",
    description="Find by description and archive",
    category="Complex Multi-Step",

    task_text="Archive the computer vision defect detection project",

    identity=identity_engineer("jonas_weiss", today="2025-07-20"),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            AgentLink.project("proj_acme_line3_cv_poc"),
        ],
    ),

    related_tests=["project_status_change_by_lead", "name_a_project"],
    potential_error="Agent doesn't match description to project",
    expected_api_calls=["Req_WhoAmI", "Req_SearchProjects", "Req_GetProject", "Req_UpdateProjectStatus"],
)
