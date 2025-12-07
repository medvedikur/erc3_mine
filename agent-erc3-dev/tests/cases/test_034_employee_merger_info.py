"""
Test 034: Employee Asks About Merger Details

Test: Employee asks about the acquisition and who acquired the company.
Agent should search wiki and provide accurate information from merger.md.

Scenario:
- Wiki version includes merger.md
- Employee asks about the acquiring company
- Agent should provide accurate info about AI Excellence Group INTERNATIONAL

Potential Error: Agent doesn't find merger info or provides wrong company name.

Category: Wiki / Information Retrieval
Related Tests: wiki_merger_policy_search
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink
)
from tests.framework.mock_data import MockWhoAmI


# Post-merger wiki hash (contains merger.md)
POST_MERGER_WIKI_HASH = "a744c2c01ee8c5a2311f95b6dc496accd3c0ca74"


SCENARIO = TestScenario(
    spec_id="employee_asks_merger_info",
    description="Employee asks about merger and acquiring company",
    category="Wiki",

    task_text="Who acquired Aetherion? Tell me about the merger.",

    identity=MockWhoAmI(
        is_public=False,
        user="ana_kovac",
        name="Ana Kovac",
        email="ana_kovac@aetherion.com",
        department="Software Engineering",
        location="Vienna",
        today="2025-07-20",
        wiki_hash=POST_MERGER_WIKI_HASH,
    ),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[],  # Read-only operation - no links expected (mutation tracking)
        # Response should mention acquiring company (partial match OK)
        message_contains=["AI Excellence"],
    ),

    related_tests=["wiki_merger_policy_search", "guest_post_merger_mention"],
    potential_error="Agent doesn't find merger.md or provides incorrect company name",
    expected_api_calls=["Req_WhoAmI", "Req_ListWiki"],
)
