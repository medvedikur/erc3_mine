"""
Test 031: Time Log with Invalid CC Code Format

Test: After merger, time entries require a valid CC code format.
Agent should reject invalid format and ask for correct one.

Scenario:
- Wiki version includes merger.md with CC code format: CC-<Region>-<Unit>-<ProjectCode>
- User provides CC code in wrong format (missing parts)
- Agent should ask for correct format

Potential Error: Agent accepts invalid CC code and logs time.

Category: TimeTracking / M&A Compliance
Related Tests: add_time_entry_lead_v2 (no CC), add_time_entry_lead_v3 (valid CC)

Note: This complements the competition tests:
- v2 tests: no CC code provided → clarification needed
- v3 tests: valid CC code provided → ok_answer
- THIS tests: invalid CC format → clarification needed (different reason)
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink
)
from tests.framework.mock_data import MockWhoAmI


# Post-merger wiki hash (contains merger.md with CC code requirement)
POST_MERGER_WIKI_HASH = "a744c2c01ee8c5a2311f95b6dc496accd3c0ca74"


# Jonas Weiss is Lead of proj_acme_line3_cv_poc
SCENARIO = TestScenario(
    spec_id="time_log_invalid_cc_format",
    description="Time log with invalid CC code format should be rejected",
    category="TimeTracking",

    # User provides CC code but in WRONG format (should be CC-<Region>-<Unit>-<3digits>)
    task_text="Log 4 hours on Line 3 project for today, cost centre ABC123",

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
        # Response should explain correct CC code format
        message_contains=["CC-"],
    ),

    related_tests=["add_time_entry_lead_v2", "add_time_entry_lead_v3"],
    potential_error="Agent accepts invalid CC code format and logs time",
    expected_api_calls=["Req_WhoAmI", "Req_SearchProjects", "Req_GetProject"],
)
