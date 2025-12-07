"""
Test 032: Search for New M&A Policies in Wiki

Test: Employee asks about new policies after merger.
Agent should find and reference merger.md content.

Scenario:
- Wiki version includes merger.md
- Employee asks about new restrictions or changes
- Agent should search wiki and provide information from merger.md

Potential Error: Agent doesn't find merger.md or provides outdated info.

Category: Wiki / Information Retrieval
Related Tests: wiki_cleanup
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink
)
from tests.framework.mock_data import MockWhoAmI


# Post-merger wiki hash (contains merger.md)
POST_MERGER_WIKI_HASH = "a744c2c01ee8c5a2311f95b6dc496accd3c0ca74"


SCENARIO = TestScenario(
    spec_id="wiki_merger_policy_search",
    description="Search wiki for new M&A policies and restrictions",
    category="Wiki",

    task_text="What are the new security restrictions after the acquisition?",

    identity=MockWhoAmI(
        is_public=False,
        user="helene_stutz",
        name="Helene Stutz",
        email="helene_stutz@aetherion.com",
        department="Consulting",
        location="Amsterdam",
        today="2025-07-20",
        wiki_hash=POST_MERGER_WIKI_HASH,
    ),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[],  # Read-only operation - no links expected (mutation tracking)
        # Response should mention key M&A restrictions (any of these)
        message_contains=["JIRA"],  # Core requirement from merger.md
    ),

    related_tests=["wiki_cleanup"],
    potential_error="Agent doesn't find merger.md or returns outdated policies",
    expected_api_calls=["Req_WhoAmI", "Req_ListWiki"],
)
