from typing import List, Type, Any
from erc3 import store, ApiException
from .base import ToolContext, Middleware, ActionHandler

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_BLUE = "\x1B[34m"
CLI_CLR = "\x1B[0m"

class DefaultActionHandler:
    """Standard handler that executes the action against the store API"""
    def handle(self, ctx: ToolContext) -> None:
        action_name = ctx.model.__class__.__name__
        print(f"  {CLI_BLUE}▶ Executing:{CLI_CLR} {action_name}")
        print(f"     {ctx.model.model_dump_json()}")
        
        try:
            result = ctx.api.dispatch(ctx.model)
            result_json = result.model_dump_json(exclude_none=True)
            
            print(f"  {CLI_GREEN}✓ SUCCESS:{CLI_CLR}")
            print(f"     {result_json}")
            
            ctx.results.append(f"Action ({action_name}): SUCCESS\nResult: {result_json}")
            
        except ApiException as e:
            error_msg = e.api_error.error if e.api_error else str(e)
            print(f"  {CLI_RED}✗ FAILED:{CLI_CLR} {error_msg}")
            
            ctx.results.append(f"Action ({action_name}): FAILED\nError: {error_msg}")
            
            if action_name == "Req_CheckoutBasket":
                ctx.results.append("[HINT]: Checkout failed. Adjust basket (reduce quantity or change items) and retry checkout.")
            
            ctx.stop_execution = True

class CheckoutVerificationMiddleware:
    """Middleware to verify basket state before checkout"""
    def process(self, ctx: ToolContext) -> None:
        if not isinstance(ctx.model, store.Req_CheckoutBasket):
            return

        args = ctx.raw_action.get("args", {})
        expected_total = args.get("expected_total")
        expected_coupon = args.get("expected_coupon")

        if expected_total is not None or expected_coupon is not None:
            print(f"  {CLI_BLUE}🛡️ Verifying state before checkout...{CLI_CLR}")
            try:
                # Verify state by viewing basket first
                view_res = ctx.api.dispatch(store.Req_ViewBasket())
                
                # 1. Check Coupon
                if expected_coupon is not None:
                    actual_coupon = getattr(view_res, "coupon", "") or ""
                    if (actual_coupon or "").lower() != (expected_coupon or "").lower():
                        error_msg = f"SAFETY BLOCK: Expected coupon '{expected_coupon}' but found '{actual_coupon}'. You must re-apply the correct coupon before checkout."
                        self._fail(ctx, error_msg)
                        return
                
                # 2. Check Total
                if expected_total is not None:
                    actual_total = float(getattr(view_res, "total", 0.0))
                    target_total = float(expected_total)
                    if abs(actual_total - target_total) > 0.01:
                        error_msg = f"SAFETY BLOCK: Expected total {target_total} but found {actual_total}. Your basket state is incorrect."
                        self._fail(ctx, error_msg)
                        return
                
                print(f"  {CLI_GREEN}✓ Verification passed.{CLI_CLR}")

            except Exception as e:
                self._fail(ctx, f"SAFETY BLOCK: Could not verify basket state: {str(e)}")

    def _fail(self, ctx: ToolContext, msg: str):
        print(f"  {CLI_RED}✗ {msg}{CLI_CLR}")
        ctx.results.append(f"Action (Req_CheckoutBasket): FAILED\nError: {msg}")
        ctx.stop_execution = True

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

