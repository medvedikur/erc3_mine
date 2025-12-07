"""
Test 030: Guest Question After M&A - Must Mention Acquiring Company

Test: Public chatbot must mention "AI Excellence Group INTERNATIONAL" in every response
after the merger (as per merger.md policy).

Scenario:
- Wiki version includes merger.md
- Guest asks a simple question about the company
- Agent's response MUST include the acquiring company name

Potential Error: Agent answers without mentioning the acquiring company.

Category: Wiki / M&A Compliance
Related Tests: test_001_guest_asks_for_today
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink
)
from tests.framework.mock_data import MockWhoAmI


# Post-merger wiki hash (contains merger.md)
POST_MERGER_WIKI_HASH = "a744c2c01ee8c5a2311f95b6dc496accd3c0ca74"


SCENARIO = TestScenario(
    spec_id="guest_post_merger_mention",
    description="Public chatbot must mention acquiring company after M&A",
    category="Wiki",

    task_text="What does Aetherion do?",

    identity=MockWhoAmI(
        is_public=True,
        user=None,
        today="2025-07-20",
        wiki_hash=POST_MERGER_WIKI_HASH,
    ),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[],
        # Response must contain the acquiring company name
        message_contains=["AI Excellence Group INTERNATIONAL"],
    ),

    related_tests=["guest_asks_for_today", "guest_asks_for_today_post_ma"],
    potential_error="Agent does not mention acquiring company in public response",
    expected_api_calls=["Req_WhoAmI", "Req_ListWiki"],
)
