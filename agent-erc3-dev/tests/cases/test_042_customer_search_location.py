"""
Test 042: Search Customers by Location

Test: Employee searches for customers in a specific location.

Scenario:
- Employee asks for customers located in Munich
- System has customers in various locations
- Agent should use customers_search with locations filter

Potential Error: Agent searches by query text instead of location filter.

Category: Customers / Search
Related Tests: test_041 (by phase), test_040 (get details)
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink
)
from tests.framework.mock_data import MockWhoAmI


SCENARIO = TestScenario(
    spec_id="customer_search_by_location",
    description="Search customers by location",
    category="Customers",

    task_text="Which customers are based in Munich?",

    identity=MockWhoAmI(
        is_public=False,
        user="jonas_weiss",
        name="Jonas Weiss",
        email="jonas_weiss@aetherion.com",
        department="Software Engineering",
        location="Munich",
        today="2025-07-20",
    ),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            # Munich customers from mock data:
            AgentLink.customer("cust_acme_industrial_systems"),
            AgentLink.customer("cust_munich_tech_hub"),
        ],
        # Response should mention Munich
        message_contains=["Munich"],
    ),

    related_tests=["customer_search_by_phase", "customer_get_details"],
    potential_error="Agent uses text query instead of location filter",
    expected_api_calls=["Req_WhoAmI", "Req_SearchCustomers"],
)
