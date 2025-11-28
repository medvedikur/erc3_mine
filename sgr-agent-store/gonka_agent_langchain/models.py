from typing import List, Union, Literal, Optional
from pydantic import BaseModel, Field
from erc3 import store

# === SGR Action Schema (Discriminated Unions) ===
StoreAction = Union[
    store.Req_ListProducts,
    store.Req_ViewBasket,
    store.Req_ApplyCoupon,
    store.Req_RemoveCoupon,
    store.Req_AddProductToBasket,
    store.Req_RemoveItemFromBasket,
    store.Req_CheckoutBasket,
]

class NextStep(BaseModel):
    """SGR NextStep schema - enforces structured reasoning"""
    thoughts: str = Field(..., description="Step-by-step reasoning following mental checklist")
    action_queue: List[StoreAction] = Field(..., description="Actions to execute in order")
    is_final: bool = Field(False, description="True when task is complete or impossible")

