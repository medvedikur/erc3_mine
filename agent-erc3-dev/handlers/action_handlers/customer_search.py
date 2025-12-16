"""
Customer search handler with smart keyword fallback.

This handler implements intelligent customer search that:
1. Executes exact match query
2. If query has multiple words and few results: tries keyword fallback
3. Merges all results and returns unique customers
"""
import copy
from typing import Any
from erc3.erc3 import client
from .base import ActionHandler
from ..base import ToolContext
from utils import CLI_BLUE, CLI_YELLOW, CLI_GREEN, CLI_CLR


class CustomerSearchHandler(ActionHandler):
    """
    Handler for Req_SearchCustomers with smart keyword fallback.

    When searching with long descriptive queries (e.g., "German cold-storage
    operator group for Nordics"), the API may not find results for the full
    query. This handler tries individual keywords to improve recall.
    """

    # Skip common words that don't help with search
    STOP_WORDS = {
        'the', 'a', 'an', 'and', 'or', 'for', 'in', 'on', 'at', 'to', 'of',
        'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been', 'be',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare',
        'group', 'company', 'customer', 'client', 'operator', 'fabricator',
        'manufacturer', 'supplier', 'provider', 'more', 'less', 'which', 'that',
    }

    def can_handle(self, ctx: ToolContext) -> bool:
        """Handle only customer search requests."""
        return isinstance(ctx.model, client.Req_SearchCustomers)

    def handle(self, ctx: ToolContext) -> bool:
        """
        Execute smart customer search with keyword fallback.

        Returns:
            False to let default handler continue with enrichments
        """
        action_name = ctx.model.__class__.__name__
        print(f"  {CLI_BLUE}▶ Executing:{CLI_CLR} {action_name}")

        customers_map = {}
        exact_error = None
        res_exact = None

        # 1. Exact Match (Original Request)
        try:
            res_exact = ctx.api.dispatch(ctx.model)
            if hasattr(res_exact, 'companies') and res_exact.companies:
                for c in res_exact.companies:
                    customers_map[c.id] = c
        except Exception as e:
            error_msg = str(e).lower()
            if any(x in error_msg for x in ['internal error', 'server error', 'timeout']):
                exact_error = e
                print(f"  {CLI_YELLOW}⚠ Exact search failed with system error: {e}{CLI_CLR}")
            else:
                print(f"  {CLI_YELLOW}⚠ Exact search failed: {e}{CLI_CLR}")

        # 2. Keyword Fallback (if query has multiple words and exact match yielded no/few results)
        query = ctx.model.query
        if query and len(customers_map) < 2:
            # Extract meaningful keywords (skip stop words, min 3 chars)
            words = [w.strip().lower() for w in query.replace('-', ' ').split()]
            keywords = [
                w for w in words
                if len(w) >= 3 and w not in self.STOP_WORDS
            ]

            if len(keywords) > 1:
                print(f"  {CLI_BLUE}🔍 Smart Search: Executing keyword fallback for customers{CLI_CLR}")

                for kw in keywords:
                    if kw.lower() == query.lower():
                        continue

                    print(f"  {CLI_BLUE}  → Searching for keyword: '{kw}'{CLI_CLR}")
                    model_kw = copy.deepcopy(ctx.model)
                    model_kw.query = kw

                    try:
                        res_kw = ctx.api.dispatch(model_kw)
                        if hasattr(res_kw, 'companies') and res_kw.companies:
                            for cust in res_kw.companies:
                                if cust.id not in customers_map:
                                    customers_map[cust.id] = cust
                    except Exception as e:
                        print(f"  {CLI_YELLOW}⚠ Keyword search '{kw}' failed: {e}{CLI_CLR}")

        # 3. Check if we have a system error that prevents any results
        if exact_error and len(customers_map) == 0:
            ctx.shared['_search_error'] = exact_error
            return False

        # 4. Construct Final Response
        next_offset = res_exact.next_offset if res_exact else -1

        result = client.Resp_CustomerSearchResults(
            companies=list(customers_map.values()) if customers_map else None,
            next_offset=next_offset
        )
        print(f"  {CLI_GREEN}✓ SUCCESS{CLI_CLR}")
        if len(customers_map) > 0:
            print(f"  {CLI_BLUE}🔍 Merged {len(customers_map)} unique customers.{CLI_CLR}")

        # Store result in context for DefaultActionHandler enrichments
        ctx.shared['_customer_search_result'] = result
        return False  # Let default handler continue with enrichments
