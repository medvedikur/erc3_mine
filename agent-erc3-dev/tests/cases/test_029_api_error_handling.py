"""
Test 029: API Error Handling

Test: Agent correctly handles API errors.

Scenario:
- User requests list of projects
- API projects_search returns an error (simulation)
- Agent should return error_internal

Potential Error: Agent crashes or gives incorrect response.

Category: Error Handling
Related Tests: broken_system
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink,
    identity_consultant, MockDataBuilder
)


# Create data builder with API error
data_builder = MockDataBuilder()
data_builder.with_api_error(
    "projects_search",
    Exception("Internal server error: database connection failed")
)


SCENARIO = TestScenario(
    spec_id="api_error_handling",
    description="Agent handles API errors gracefully",
    category="ErrorHandling",

    task_text="Show me all my projects",

    identity=identity_consultant(today="2025-07-20"),

    expected=ExpectedResult(
        outcome="error_internal",
        links=[],  # No links for internal errors
    ),

    data_builder=data_builder,

    related_tests=["broken_system", "not_available_feature"],
    potential_error="Agent crashes or returns incorrect outcome on API error",
    expected_api_calls=["Req_WhoAmI", "Req_SearchProjects"],
)
