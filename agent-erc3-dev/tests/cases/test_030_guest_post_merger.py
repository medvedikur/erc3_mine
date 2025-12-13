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
from tests.framework.mock_data import MockWhoAmI, MockDataBuilder


# Post-merger wiki hash (contains merger.md)
POST_MERGER_WIKI_HASH = "a744c2c01ee8c5a2311f95b6dc496accd3c0ca74"

# Actual merger.md content - must include requirement to mention company name
MERGER_MD_CONTENT = """# Merger & Acquisition Policy Updates

## Effective Date: July 15, 2025

Aetherion Solutions has been acquired by **AI Excellence Group INTERNATIONAL**.

## Public Communication Policy

**IMPORTANT**: All public-facing communications (including chatbots, public API responses,
and external documentation) MUST mention that Aetherion is now part of
**AI Excellence Group INTERNATIONAL**.

This includes:
- Guest/public chatbot responses
- Public API responses
- External documentation
- Marketing materials

## About Aetherion

Aetherion Solutions (now part of AI Excellence Group INTERNATIONAL) is a leading
AI consulting company specializing in:
- Computer Vision solutions
- Machine Learning implementations
- AI-powered automation
"""

# Background.md with company info
BACKGROUND_MD_CONTENT = """# Company Background

Aetherion Solutions (now part of AI Excellence Group INTERNATIONAL) is a leading
AI consulting company based in Europe.

## Our Services
- Computer Vision solutions
- Machine Learning implementations
- AI strategy consulting
"""


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

    data_builder=MockDataBuilder()
        .with_custom_response("wiki_load:merger.md", MERGER_MD_CONTENT)
        .with_custom_response("wiki_load:background.md", BACKGROUND_MD_CONTENT),

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
