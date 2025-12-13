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
from tests.framework.mock_data import MockWhoAmI, MockDataBuilder


# Post-merger wiki hash (contains merger.md)
POST_MERGER_WIKI_HASH = "a744c2c01ee8c5a2311f95b6dc496accd3c0ca74"

# Actual merger.md content that agent should find
MERGER_MD_CONTENT = """# Merger & Acquisition Policy Updates

## Effective Date: July 15, 2025

Aetherion Solutions has been acquired by **AI Excellence Group INTERNATIONAL**.

## About the Acquisition

AI Excellence Group INTERNATIONAL is a leading global technology company
specializing in enterprise AI solutions. The acquisition brings:
- Access to global markets
- Enhanced R&D capabilities
- Expanded customer base

## New Security Restrictions

Following the acquisition, the following security measures are now in effect:

### Project Change Requirements
- All project status changes now require a **JIRA ticket** reference
- Project Lead approval is still required for status changes
"""


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

    data_builder=MockDataBuilder()
        .with_custom_response("wiki_load:merger.md", MERGER_MD_CONTENT),

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
