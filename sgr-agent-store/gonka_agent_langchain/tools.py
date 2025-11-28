from typing import Optional, Dict, Any, Literal
import json
from erc3 import store
from pydantic import BaseModel, Field

CLI_RED = "\x1B[31m"
CLI_CLR = "\x1B[0m"

class SearchArgs(BaseModel):
    query: str = Field(..., description="Query to search in the store manual, e.g. 'how do coupons stack'")

class Req_SearchManual(BaseModel):
    tool: Literal["search_manual"] = "search_manual"
    query: str

def parse_action(action_dict: dict) -> Optional[Any]:
    """Parse action dict into Pydantic model (SGR dispatch)"""
    tool = action_dict.get("tool", "").lower().replace("_", "").replace("-", "")
    
    # Flatten args
    args = action_dict.get("args", {})
    if args:
        action_dict = {**action_dict, **args}
    
    try:
        if tool in ["listproducts", "list", "search", "browse"]:
            return store.Req_ListProducts(
                offset=int(action_dict.get("offset", 0)),
                limit=int(action_dict.get("limit", 10))
            )
        
        elif tool in ["searchmanual", "manual", "help", "rules"]:
            return Req_SearchManual(
                query=action_dict.get("query", "")
            )
        
        elif tool in ["viewbasket", "basket", "cart", "view"]:
            return store.Req_ViewBasket()
        
        elif tool in ["addproducttobasket", "add", "addproduct", "addtobasket"]:
            sku = action_dict.get("sku") or action_dict.get("product_id")
            if not sku:
                return None
            return store.Req_AddProductToBasket(
                sku=sku,
                quantity=int(action_dict.get("quantity", 1))
            )
        
        elif tool in ["removeitemfrombasket", "remove", "removeitem", "removefrombasket", "removeproduct"]:
            sku = action_dict.get("sku") or action_dict.get("item_id") or action_dict.get("product_id")
            return store.Req_RemoveItemFromBasket(
                sku=sku or "",
                quantity=int(action_dict.get("quantity", 0))
            )
        
        elif tool in ["applycoupon", "apply"]:
            coupon = action_dict.get("coupon") or action_dict.get("code") or ""
            return store.Req_ApplyCoupon(coupon=coupon)
        
        elif tool in ["removecoupon", "unapplycoupon", "unapply"]:
            return store.Req_RemoveCoupon()
        
        elif tool in ["checkoutbasket", "checkout", "buy", "purchase", "complete"]:
            return store.Req_CheckoutBasket()
        
        else:
            return None
            
    except Exception as e:
        print(f"{CLI_RED}⚠ Error parsing action: {e}{CLI_CLR}")
        return None

def execute_action(action_model, store_api) -> str:
    """Execute a parsed action model"""
    try:
        result = store_api.dispatch(action_model)
        return result.model_dump_json(exclude_none=True)
    except Exception as e:
        # Check if it's an API exception
        if hasattr(e, 'api_error') and e.api_error:
             return f"ERROR: {e.api_error.error}"
        return f"ERROR: {str(e)}"

