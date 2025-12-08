"""
Test 040: Get Customer Details

Test: Employee asks for details about a specific customer.

Scenario:
- Helene Stutz asks about ACME Industrial account manager
- Agent should find customer and return details including account_manager

Potential Error: Agent searches employees instead of customers.

Category: Customers / Search
Related Tests: test_041 (search by phase), nordic_followup_customers
"""

from tests.framework.task_builder import (
    TestScenario, ExpectedResult, AgentLink
)
from tests.framework.mock_data import MockWhoAmI


SCENARIO = TestScenario(
    spec_id="customer_get_details",
    description="Get customer details including account manager",
    category="Customers",

    task_text="Who is the account manager for ACME Industrial Systems?",

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
            AgentLink.customer("cust_acme_industrial_systems"),
            AgentLink.employee("richard_klein"),  # Account manager
        ],
        # Response should mention Richard Klein
        message_contains=["Richard Klein"],
    ),

    related_tests=["customer_search_by_phase", "nordic_followup_customers"],
    potential_error="Agent searches employees instead of customers",
    expected_api_calls=["Req_WhoAmI", "Req_SearchCustomers"],
)
