from typing import List, Any
from erc3 import ApiException
from erc3.erc3 import client
from .base import ToolContext, Middleware, ActionHandler

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_BLUE = "\x1B[34m"
CLI_YELLOW = "\x1B[33m"
CLI_CLR = "\x1B[0m"

class DefaultActionHandler:
    """Standard handler that executes the action against the API"""
    def handle(self, ctx: ToolContext) -> None:
        action_name = ctx.model.__class__.__name__
        print(f"  {CLI_BLUE}▶ Executing:{CLI_CLR} {action_name}")
        
        # Link Auto-Detection for Respond Action
        if isinstance(ctx.model, client.Req_ProvideAgentResponse) and not ctx.model.links:
            # If no links provided, try to find relevant entities from context history
            # This is a fallback if regex in tools.py missed them or they weren't in the text
            # We can scan the previous results in ctx (though ctx is fresh per action)
            # OR we can scan the shared context if we stored history there.
            # Currently we don't store full history in shared.
            pass

        try:
            # SPECIAL HANDLING: Wiki Search (Local vs Remote)
            if isinstance(ctx.model, client.Req_SearchWiki):
                wiki_manager = ctx.shared.get('wiki_manager')
                if wiki_manager:
                    print(f"  {CLI_BLUE}🔍 Using Local Wiki Search (Smart RAG){CLI_CLR}")
                    search_result_text = wiki_manager.search(ctx.model.query_regex)
                    
                    # We need to wrap this in a Resp object to match the expected return type structure for logging/history
                    # But wait, search returns just text? The tool expects a list of results?
                    # The original API returned Resp_SearchWiki(results=[...])
                    # We can construct a mock response or just return the text as a successful result string.
                    # Since we are modifying the handler, we control what goes into ctx.results.
                    
                    print(f"  {CLI_GREEN}✓ SUCCESS (Local){CLI_CLR}")
                    ctx.results.append(f"Action ({action_name}): SUCCESS\nResult: {search_result_text}")
                    return

            # Default API execution
            try:
                result = ctx.api.dispatch(ctx.model)
            except Exception as e:
                error_str = str(e)
                # Check for "Input should be a valid list" error (Server returning null)
                if "valid list" in error_str and "NoneType" in error_str:
                    print(f"  {CLI_YELLOW}⚠ API returned invalid list (null). Patching response.{CLI_CLR}")
                    
                    from erc3.erc3.dtos import (
                        Resp_SearchWiki, Resp_ProjectSearchResults, Resp_SearchEmployees, 
                        Resp_SearchTimeEntries, Resp_CustomerSearchResults
                    )
                    
                    if "Resp_SearchWiki" in error_str:
                        result = Resp_SearchWiki(results=[])
                    elif "Resp_ProjectSearchResults" in error_str:
                        result = Resp_ProjectSearchResults(projects=[])
                    elif "Resp_SearchEmployees" in error_str:
                        result = Resp_SearchEmployees(employees=[])
                    elif "Resp_SearchTimeEntries" in error_str:
                        result = Resp_SearchTimeEntries(time_entries=[])
                    elif "Resp_CustomerSearchResults" in error_str:
                        result = Resp_CustomerSearchResults(customers=[])
                    else:
                        # Unknown list error, re-raise
                        raise e
                else:
                    raise e
            
            # Update Identity State if response is WhoAmI
            security_manager = ctx.shared.get('security_manager')
            if security_manager and isinstance(result, client.Resp_WhoAmI):
                security_manager.update_identity(result)

            # Check for Wiki Hash updates in response
            # Many responses might contain the hash or trigger a need to check it?
            # Actually only who_am_i and list_wiki return the hash directly.
            
            wiki_manager = ctx.shared.get('wiki_manager')
            if wiki_manager:
                if isinstance(result, client.Resp_WhoAmI) and result.wiki_sha1:
                    wiki_manager.sync(result.wiki_sha1)
                elif isinstance(result, client.Resp_ListWiki) and result.sha1:
                    wiki_manager.sync(result.sha1)

            # Apply Security Redaction (if applicable)
            if security_manager:
                result = security_manager.redact_result(result)

            # Convert result to JSON
            result_json = result.model_dump_json(exclude_none=True)
            
            print(f"  {CLI_GREEN}✓ SUCCESS{CLI_CLR}")
            # print(f"     {result_json[:200]}...") # Truncate log
            
            ctx.results.append(f"Action ({action_name}): SUCCESS\nResult: {result_json}")
            
        except ApiException as e:
            error_msg = e.api_error.error if e.api_error else str(e)
            print(f"  {CLI_RED}✗ FAILED:{CLI_CLR} {error_msg}")
            
            ctx.results.append(f"Action ({action_name}): FAILED\nError: {error_msg}")
            
            # Stop if critical? No, let the agent decide usually.
            # But if it's an internal error, maybe stop?
            
        except Exception as e:
            print(f"  {CLI_RED}✗ SYSTEM ERROR:{CLI_CLR} {e}")
            ctx.results.append(f"Action ({action_name}): SYSTEM ERROR\nError: {str(e)}")


class ActionExecutor:
    """Main executor that orchestrates middleware and handlers"""
    def __init__(self, api, middleware: List[Middleware] = None):
        self.api = api
        self.middleware = middleware or []
        self.handler = DefaultActionHandler()
    
    def execute(self, action_dict: dict, action_model: Any) -> ToolContext:
        ctx = ToolContext(self.api, action_dict, action_model)
        
        # Run middleware
        for mw in self.middleware:
            mw.process(ctx)
            if ctx.stop_execution:
                return ctx
                
        # Run handler
        self.handler.handle(ctx)
        return ctx
