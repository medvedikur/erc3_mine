"""
Customer tool parsers.
"""
from typing import Any
from erc3.erc3 import client
from ..registry import ToolParser, ParseContext


@ToolParser.register("customers_list", "customerslist", "listcustomers")
def _parse_customers_list(ctx: ParseContext) -> Any:
    """List all customers with pagination."""
    # AICODE-NOTE: Support both page (1-indexed) and offset (0-indexed) pagination (t009 fix)
    # AICODE-NOTE: offset takes precedence over page when both are provided (t009 regression)
    limit = int(ctx.args.get("limit", ctx.args.get("per_page", 5)))
    explicit_offset = ctx.args.get("offset")
    page = ctx.args.get("page")
    if explicit_offset is not None:
        offset = int(explicit_offset)
    elif page is not None:
        offset = (int(page) - 1) * limit
    else:
        offset = 0

    return client.Req_ListCustomers(
        offset=offset,
        limit=limit
    )


@ToolParser.register("customers_get", "customersget", "getcustomer")
def _parse_customers_get(ctx: ParseContext) -> Any:
    """Get customer by ID."""
    cust_id = (
        ctx.args.get("id") or
        ctx.args.get("customer_id") or
        ctx.args.get("customer") or
        ctx.args.get("Customer")  # PascalCase support
    )
    if not cust_id:
        return None
    return client.Req_GetCustomer(id=cust_id)


@ToolParser.register("customers_search", "customerssearch", "searchcustomers")
def _parse_customers_search(ctx: ParseContext) -> Any:
    """Search customers by location, deal phase, or account manager."""
    # Handle location -> locations (list)
    locations = ctx.args.get("locations")
    if not locations and ctx.args.get("location"):
        locations = [ctx.args.get("location")]
    elif isinstance(locations, str):
        locations = [locations]

    # Handle status/stage -> deal_phase (list)
    deal_phase = ctx.args.get("deal_phase") or ctx.args.get("status") or ctx.args.get("stage")
    if deal_phase:
        if isinstance(deal_phase, str):
            deal_phase = [deal_phase]
    else:
        deal_phase = None

    # Handle account_manager -> account_managers (list)
    account_managers = ctx.args.get("account_managers")
    if not account_managers and ctx.args.get("account_manager"):
        account_managers = [ctx.args.get("account_manager")]
    elif isinstance(account_managers, str):
        account_managers = [account_managers]

    # AICODE-NOTE: Support both page (1-indexed) and offset (0-indexed) pagination (t009 fix)
    # AICODE-NOTE: offset takes precedence over page when both are provided (t009 regression)
    limit = int(ctx.args.get("limit", ctx.args.get("per_page", 5)))
    explicit_offset = ctx.args.get("offset")
    page = ctx.args.get("page")
    if explicit_offset is not None:
        offset = int(explicit_offset)
    elif page is not None:
        offset = (int(page) - 1) * limit
    else:
        offset = 0

    search_args = {
        "query": ctx.args.get("query") or ctx.args.get("query_regex"),
        "deal_phase": deal_phase,
        "locations": locations,
        "account_managers": account_managers,
        "offset": offset,
        "limit": limit
    }
    valid_args = {k: v for k, v in search_args.items() if v is not None}
    return client.Req_SearchCustomers(**valid_args)
