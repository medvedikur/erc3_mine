"""
Test 061: Tempo Migration Requirements (Wiki Update Test)

Test: Verify agent can work with updated wiki version containing Tempo migration info.

Scenario:
- Wiki has been updated to new version with tempo_migration.md
- Agent must load and combine information from:
  1. tempo_migration.md - Activity Codes (DEV, RES, MTG, DOC, ADM)
  2. rulebook.md - Weekly approval workflow (Section 11)
  3. systems.md - Updated time tracking section
- Agent should provide comprehensive answer combining both sources

Purpose: Verify wiki update mechanism works correctly and agent can synthesize
information from multiple updated wiki pages.

Potential Error: Agent uses cached old wiki version without Activity Codes/approval info.

Category: Wiki Operations / System Updates
Related Tests: wiki_merger_policy_search, employee_asks_merger_info
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink
)
from tests.framework.mock_data import MockWhoAmI, MockDataBuilder


# Post-Tempo wiki hash (contains tempo_migration.md AND updated rulebook.md)
POST_TEMPO_WIKI_HASH = "b8f5d3a01cc9e7b3422f86c7ed597accd4c1db85"

# Actual tempo_migration.md content
TEMPO_MIGRATION_CONTENT = """# Tempo Migration Guide

## Effective Date: August 1, 2025

The company has migrated from the legacy time tracking system to Tempo.

## New Time Tracking Requirements

### Activity Codes (Required)
All time entries must include an Activity Code:
- **DEV** - Development work
- **RES** - Research and investigation
- **MTG** - Meetings and calls
- **DOC** - Documentation
- **ADM** - Administrative tasks

### Approval Workflow
- All time entries require weekly approval by your direct manager
- Entries must be submitted by Friday 5 PM
- Manager approval deadline: Monday 12 PM
- Unapproved entries will not be processed for billing

### Integration with JIRA
- Development time should reference JIRA ticket numbers
- Format: DEV - JIRA-123 - Description
"""

# Merger.md content (also included in post-tempo wiki)
MERGER_MD_CONTENT = """# Merger & Acquisition Policy Updates

## Effective Date: July 15, 2025

Aetherion Solutions has been acquired by **AI Excellence Group INTERNATIONAL**.

## New Security Restrictions

### Project Change Requirements
- All project status changes require a **JIRA ticket** reference
- Project Lead approval is still required
"""


SCENARIO = TestScenario(
    spec_id="tempo_migration_requirements",
    description="Agent retrieves Tempo migration requirements from updated wiki",
    category="Wiki Operations",

    # Question requires combining info from multiple wiki sources
    task_text="What are the new time tracking requirements after the Tempo migration?",

    identity=MockWhoAmI(
        is_public=False,
        user="felix_baum",
        name="Felix Baum",
        email="felix_baum@aetherion.com",
        department="AI Engineering",
        location="Vienna",
        today="2025-08-15",  # After Tempo migration date (Aug 1)
        wiki_hash=POST_TEMPO_WIKI_HASH,
    ),

    data_builder=MockDataBuilder()
        .with_custom_response("wiki_load:tempo_migration.md", TEMPO_MIGRATION_CONTENT)
        .with_custom_response("wiki_load:merger.md", MERGER_MD_CONTENT),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[],  # Wiki-only query, no entity links needed
        # Must mention both Activity Codes AND approval workflow
        message_contains=["Activity Code", "approval"],
    ),

    related_tests=["wiki_merger_policy_search", "employee_asks_merger_info"],
    potential_error="Agent uses old wiki version without Tempo information",
    expected_api_calls=["Req_WhoAmI", "Req_LoadWiki"],
)
