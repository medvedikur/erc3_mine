"""
Test 025: Ambiguous Project Search

Test: Agent should ask for clarification when project name is ambiguous.

Scenario:
- User asks "Show me the status of the CV project"
- System has 3 projects containing "CV" in the name
- Agent should return none_clarification_needed

Potential Error: Agent picks first found project instead of asking for clarification.

Category: Search / Disambiguation
Related Tests: 026 (exact match)
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink,
    identity_consultant, MockDataBuilder
)
from tests.framework.mock_data import MockProject, MockTeamMember


# Create scenario with multiple CV projects
data_builder = MockDataBuilder()

# Add extra projects to make search ambiguous
data_builder.add_project(MockProject(
    id="proj_cv_detection_poc",
    name="CV Detection PoC",
    customer="cust_acme_industrial_systems",
    status="active",
    team=[MockTeamMember(employee="felix_baum", role="Lead", time_slice=0.3)],
))

data_builder.add_project(MockProject(
    id="proj_cv_processing",
    name="CV Processing Pipeline",
    customer="cust_munich_tech_hub",
    status="exploring",
    team=[MockTeamMember(employee="ana_kovac", role="Lead", time_slice=0.4)],
))


SCENARIO = TestScenario(
    spec_id="ambiguous_project_search",
    description="Agent should ask for clarification when project name is ambiguous",
    category="Search",

    task_text="Show me the status of the CV project",

    identity=identity_consultant(today="2025-07-20"),

    expected=ExpectedResult(
        outcome="none_clarification_needed",
        links=[],  # No links when asking for clarification
    ),

    data_builder=data_builder,

    related_tests=["test_026_exact_match_project"],
    potential_error="Agent picks first result instead of asking for clarification",
    expected_api_calls=["Req_WhoAmI", "Req_SearchProjects"],
)
