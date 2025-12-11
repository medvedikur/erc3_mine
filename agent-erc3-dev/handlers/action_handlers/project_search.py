"""
Project search handler with smart broadening and keyword fallback.

This handler implements intelligent project search that:
1. Executes exact match query
2. If team + query provided: executes broad search (no text filter)
3. If few results: tries keyword fallback on individual words
4. Merges all results and returns unique projects
"""
import copy
from typing import Any
from erc3.erc3 import client
from .base import ActionHandler
from ..base import ToolContext
from utils import CLI_BLUE, CLI_YELLOW, CLI_GREEN, CLI_CLR


class ProjectSearchHandler(ActionHandler):
    """
    Handler for Req_SearchProjects with smart broadening.

    When searching with both team and query filters, the API only matches
    projects where the query appears in the name. This handler executes
    multiple searches to find projects where the query matches ID or
    description as well.
    """

    def can_handle(self, ctx: ToolContext) -> bool:
        """Handle only project search requests."""
        return isinstance(ctx.model, client.Req_SearchProjects)

    def handle(self, ctx: ToolContext) -> bool:
        """
        Execute smart project search with broadening and fallback.

        Returns:
            False to let default handler continue with enrichments
        """
        action_name = ctx.model.__class__.__name__
        print(f"  {CLI_BLUE}▶ Executing:{CLI_CLR} {action_name}")

        projects_map = {}
        exact_error = None
        res_exact = None

        # 1. Exact Match (Original Request)
        try:
            res_exact = ctx.api.dispatch(ctx.model)
            if hasattr(res_exact, 'projects') and res_exact.projects:
                for p in res_exact.projects:
                    projects_map[p.id] = p
        except Exception as e:
            error_msg = str(e).lower()
            # Check if this is a system error (not "no results")
            if any(x in error_msg for x in ['limit exceeded', 'internal error', 'server error', 'timeout']):
                exact_error = e
                print(f"  {CLI_YELLOW}⚠ Exact search failed with system error: {e}{CLI_CLR}")
            else:
                print(f"  {CLI_YELLOW}⚠ Exact search failed: {e}{CLI_CLR}")

        # 2. Smart Broadening (if team + query)
        if ctx.model.team and ctx.model.query:
            print(f"  {CLI_BLUE}🔍 Smart Search: Executing dual-pass project search (Query + Broad){CLI_CLR}")
            model_broad = copy.deepcopy(ctx.model)
            model_broad.query = None  # Remove text filter

            try:
                res_broad = ctx.api.dispatch(model_broad)
                if hasattr(res_broad, 'projects') and res_broad.projects:
                    for p in res_broad.projects:
                        if p.id not in projects_map:
                            projects_map[p.id] = p
            except Exception as e:
                print(f"  {CLI_YELLOW}⚠ Broad search failed: {e}{CLI_CLR}")

        # 3. Keyword Fallback (if query has multiple words and exact match yielded few results)
        query = ctx.model.query
        if query and len(projects_map) < 3 and " " in query.strip():
            print(f"  {CLI_BLUE}🔍 Smart Search: Executing keyword fallback search{CLI_CLR}")
            keywords = [k.strip() for k in query.split() if len(k.strip()) > 3]

            for kw in keywords:
                if kw.lower() == query.lower():
                    continue

                print(f"  {CLI_BLUE}  → Searching for keyword: '{kw}'{CLI_CLR}")
                model_kw = copy.deepcopy(ctx.model)
                model_kw.query = kw

                try:
                    res_kw = ctx.api.dispatch(model_kw)
                    if hasattr(res_kw, 'projects') and res_kw.projects:
                        for p in res_kw.projects:
                            if p.id not in projects_map:
                                projects_map[p.id] = p
                except Exception as e:
                    print(f"  {CLI_YELLOW}⚠ Keyword search '{kw}' failed: {e}{CLI_CLR}")

        # 4. Check if we have a system error that prevents any results
        if exact_error and len(projects_map) == 0:
            # System error prevented search - store error for default handler
            ctx.shared['_search_error'] = exact_error
            return False  # Let default handler handle the error

        # 5. Construct Final Response
        if hasattr(client, 'Resp_ProjectSearchResults'):
            ResponseClass = client.Resp_ProjectSearchResults
        else:
            from erc3.erc3 import dtos
            ResponseClass = dtos.Resp_ProjectSearchResults

        next_offset = res_exact.next_offset if res_exact else 0

        result = ResponseClass(
            projects=list(projects_map.values()),
            next_offset=next_offset
        )
        print(f"  {CLI_GREEN}✓ SUCCESS{CLI_CLR}")
        print(f"  {CLI_BLUE}🔍 Merged {len(projects_map)} unique projects.{CLI_CLR}")

        # Store result in context for DefaultActionHandler enrichments
        ctx.shared['_project_search_result'] = result
        return False  # Let default handler continue with enrichments
