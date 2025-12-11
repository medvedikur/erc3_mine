"""
Employee search handler with smart keyword fallback.

This handler implements intelligent employee search that:
1. Executes exact match query with page limit retry
2. If query has multiple words and few results: tries keyword fallback
3. Merges all results and returns unique employees
"""
import re
import copy
from typing import Any
from erc3.erc3 import client
from .base import ActionHandler
from ..base import ToolContext
from utils import CLI_BLUE, CLI_YELLOW, CLI_GREEN, CLI_CLR


class EmployeeSearchHandler(ActionHandler):
    """
    Handler for Req_SearchEmployees with smart keyword fallback.

    When searching with multi-word queries (e.g., "Mira Schaefer"), the API
    may not find results for the full query. This handler tries individual
    keywords to improve recall.
    """

    def can_handle(self, ctx: ToolContext) -> bool:
        """Handle only employee search requests."""
        return isinstance(ctx.model, client.Req_SearchEmployees)

    def handle(self, ctx: ToolContext) -> bool:
        """
        Execute smart employee search with keyword fallback.

        Returns:
            False to let default handler continue with enrichments
        """
        action_name = ctx.model.__class__.__name__
        print(f"  {CLI_BLUE}▶ Executing:{CLI_CLR} {action_name}")

        employees_map = {}
        exact_error = None
        res_exact = None

        # 1. Exact Match (Original Request) with page limit retry
        try:
            res_exact = ctx.api.dispatch(ctx.model)
            if hasattr(res_exact, 'employees') and res_exact.employees:
                for e in res_exact.employees:
                    employees_map[e.id] = e
        except Exception as e:
            error_msg = str(e).lower()
            # Check for page limit exceeded - retry with correct limit
            if 'page limit exceeded' in error_msg:
                res_exact = self._retry_with_correct_limit(ctx, e, employees_map)
                if res_exact is None:
                    exact_error = e
            elif any(x in error_msg for x in ['internal error', 'server error', 'timeout']):
                exact_error = e
                print(f"  {CLI_YELLOW}⚠ Exact search failed with system error: {e}{CLI_CLR}")
            else:
                print(f"  {CLI_YELLOW}⚠ Exact search failed: {e}{CLI_CLR}")

        # 2. Keyword Fallback (if query has multiple words and exact match yielded no/few results)
        query = ctx.model.query
        if query and len(employees_map) < 2 and " " in query.strip():
            print(f"  {CLI_BLUE}🔍 Smart Search: Executing keyword fallback for employees{CLI_CLR}")
            keywords = [k.strip() for k in query.split() if len(k.strip()) > 2]

            for kw in keywords:
                if kw.lower() == query.lower():
                    continue

                print(f"  {CLI_BLUE}  → Searching for keyword: '{kw}'{CLI_CLR}")
                model_kw = copy.deepcopy(ctx.model)
                model_kw.query = kw

                try:
                    res_kw = ctx.api.dispatch(model_kw)
                    if hasattr(res_kw, 'employees') and res_kw.employees:
                        for emp in res_kw.employees:
                            if emp.id not in employees_map:
                                employees_map[emp.id] = emp
                except Exception as e:
                    print(f"  {CLI_YELLOW}⚠ Keyword search '{kw}' failed: {e}{CLI_CLR}")

        # 3. Check if we have a system error that prevents any results
        if exact_error and len(employees_map) == 0:
            # System error prevented search - store error for default handler
            ctx.shared['_search_error'] = exact_error
            return False  # Let default handler handle the error

        # 4. Construct Final Response
        next_offset = res_exact.next_offset if res_exact else -1

        result = client.Resp_SearchEmployees(
            employees=list(employees_map.values()),
            next_offset=next_offset
        )
        print(f"  {CLI_GREEN}✓ SUCCESS{CLI_CLR}")
        if len(employees_map) > 0:
            print(f"  {CLI_BLUE}🔍 Merged {len(employees_map)} unique employees.{CLI_CLR}")

        # Store result in context for DefaultActionHandler enrichments
        ctx.shared['_employee_search_result'] = result
        return False  # Let default handler continue with enrichments

    def _retry_with_correct_limit(self, ctx: ToolContext, error: Exception, employees_map: dict) -> Any:
        """
        Retry search with corrected limit from error message.

        Returns:
            Response object if retry succeeded, None otherwise
        """
        match = re.search(r'(\d+)\s*>\s*(\d+)', str(error))
        if match:
            max_limit = int(match.group(2))
            if max_limit > 0:
                print(f"  {CLI_YELLOW}⚠ Page limit exceeded. Retrying with limit={max_limit}.{CLI_CLR}")
                model_retry = copy.deepcopy(ctx.model)
                model_retry.limit = max_limit
                try:
                    res_exact = ctx.api.dispatch(model_retry)
                    if hasattr(res_exact, 'employees') and res_exact.employees:
                        for emp in res_exact.employees:
                            employees_map[emp.id] = emp
                    return res_exact
                except Exception as retry_e:
                    print(f"  {CLI_YELLOW}⚠ Retry also failed: {retry_e}{CLI_CLR}")
                    return None
            else:
                print(f"  {CLI_YELLOW}⚠ API forbids pagination (max_limit={max_limit}){CLI_CLR}")
                return None
        else:
            print(f"  {CLI_YELLOW}⚠ Exact search failed with system error: {error}{CLI_CLR}")
            return None
