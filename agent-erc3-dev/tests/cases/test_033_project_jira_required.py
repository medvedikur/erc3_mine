"""
Test 033: Project Changes Require JIRA Ticket After M&A

Test: After merger, project status/metadata changes require a JIRA ticket reference.
Agent should ask for JIRA ticket before making changes.

Scenario:
- Wiki version includes merger.md with JIRA requirement
- Project Lead tries to change project status without JIRA ticket
- Agent should ask for clarification (JIRA ticket required)

Potential Error: Agent changes project status without JIRA ticket.

Category: Authorization / M&A Compliance
Related Tests: project_status_change_by_lead
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink
)
from tests.framework.mock_data import MockWhoAmI


# Post-merger wiki hash (contains merger.md with JIRA requirement)
POST_MERGER_WIKI_HASH = "a744c2c01ee8c5a2311f95b6dc496accd3c0ca74"


# Jonas Weiss is Lead of proj_acme_line3_cv_poc
SCENARIO = TestScenario(
    spec_id="project_change_jira_required",
    description="Project changes require JIRA ticket after M&A",
    category="Authorization",

    task_text="Pause the Line 3 project",

    identity=MockWhoAmI(
        is_public=False,
        user="jonas_weiss",
        name="Jonas Weiss",
        email="jonas_weiss@aetherion.com",
        department="Software Engineering",
        location="Munich",
        today="2025-07-20",
        wiki_hash=POST_MERGER_WIKI_HASH,
    ),

    expected=ExpectedResult(
        outcome="none_clarification_needed",
        links=[
            AgentLink.project("proj_acme_line3_cv_poc"),
        ],
        # Response should mention JIRA requirement
        message_contains=["JIRA"],
    ),

    related_tests=["project_status_change_by_lead"],
    potential_error="Agent changes project status without required JIRA ticket",
    expected_api_calls=["Req_WhoAmI", "Req_SearchProjects", "Req_GetProject"],
)
