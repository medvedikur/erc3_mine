"""
Test 041: Search Customers by Deal Phase

Test: Employee searches for customers in a specific deal phase.

Scenario:
- Employee asks for all customers in "exploring" phase
- System has multiple customers in different phases
- Agent should use customers_search with deal_phase filter

Potential Error: Agent lists all customers without filtering.

Category: Customers / Search
Related Tests: test_040 (get details), test_042 (by location)
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink
)
from tests.framework.mock_data import MockWhoAmI


SCENARIO = TestScenario(
    spec_id="customer_search_by_phase",
    description="Search customers by deal phase",
    category="Customers",

    task_text="List all customers in the exploring phase",

    identity=MockWhoAmI(
        is_public=False,
        user="helene_stutz",
        name="Helene Stutz",
        email="helene_stutz@aetherion.com",
        department="Consulting",
        location="Amsterdam",
        today="2025-07-20",
    ),

    expected=ExpectedResult(
        outcome="ok_answer",
        links=[
            # Agent should only return customers where user is Account Manager
            # helene_stutz is AM for scandi_foods, but NOT for nordic_logistics
            AgentLink.customer("cust_scandi_foods_ab"),
        ],
        # Response should list exploring customers
        message_contains=["exploring"],
    ),

    related_tests=["customer_get_details", "customer_search_by_location"],
    potential_error="Agent doesn't filter by deal_phase",
    expected_api_calls=["Req_WhoAmI", "Req_SearchCustomers"],
)
