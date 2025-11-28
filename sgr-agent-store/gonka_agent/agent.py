"""
SGR (Schema-Guided Reasoning) Agent for Gonka Network

Based on: https://abdullin.com/schema-guided-reasoning/
         https://abdullin.com/schema-guided-reasoning/demo

SGR enforces step-by-step reasoning through predefined schemas with:
- Discriminated unions via `tool: Literal["tool_name"]`
- Explicit JSON schema in prompt for models without native structured output
- Manual dispatch based on tool type
"""
import time
import os
import json
import random
import requests
from typing import List, Union, Literal, Optional
from pydantic import BaseModel, Field
from erc3 import store, ApiException, TaskInfo, ERC3
from gonka_openai import GonkaOpenAI

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pricing import calculator


# === Statistics & Billing ===
class SessionStats:
    def __init__(self):
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.llm_requests = 0
        self.store_api_requests = 0
        self.total_cost_usd = 0.0

    def add_llm_usage(self, model: str, usage):
        if usage:
            self.total_prompt_tokens += usage.prompt_tokens
            self.total_completion_tokens += usage.completion_tokens
            self.llm_requests += 1
            cost = calculator.calculate_cost(model, usage.prompt_tokens, usage.completion_tokens)
            self.total_cost_usd += cost

    def add_api_call(self):
        self.store_api_requests += 1

    def print_report(self):
        total_tokens = self.total_prompt_tokens + self.total_completion_tokens
        print("\n" + "="*50)
        print(f"📊 SESSION STATISTICS REPORT (GONKA + SGR)")
        print("="*50)
        print(f"🧠 LLM Requests:      {self.llm_requests}")
        print(f"🛒 Store API Calls:   {self.store_api_requests}")
        print("-" * 30)
        print(f"📥 Input Tokens:      {self.total_prompt_tokens}")
        print(f"📤 Output Tokens:     {self.total_completion_tokens}")
        print(f"∑  Total Tokens:      {total_tokens}")
        print("-" * 30)
        print(f"💰 TOTAL COST:        ${self.total_cost_usd:.6f}")
        print("="*50 + "\n")


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


# === Genesis Nodes for Gonka Network ===
GENESIS_NODES = [
    "http://node1.gonka.ai:8000",
    "http://node2.gonka.ai:8000", 
    "http://node3.gonka.ai:8000",
    "http://185.216.21.98:8000",
    "http://47.236.26.199:8000",
    "http://47.236.19.22:18000",
    "http://gonka.spv.re:8000",
]

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_BLUE = "\x1B[34m"
CLI_YELLOW = "\x1B[33m"
CLI_CYAN = "\x1B[36m"
CLI_CLR = "\x1B[0m"


def fetch_active_nodes(source_node: str = None) -> List[str]:
    """Fetch list of active participant nodes from current epoch"""
    if source_node is None:
        source_node = random.choice(GENESIS_NODES)
    
    try:
        response = requests.get(
            f"{source_node}/v1/epochs/current/participants",
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            participants = data.get("participants", [])
            nodes = [p.get("inference_url") for p in participants if p.get("inference_url")]
            if nodes:
                return nodes
    except Exception as e:
        print(f"{CLI_YELLOW}⚠ Could not fetch participants from {source_node}: {e}{CLI_CLR}")
    
    return []


def get_available_nodes() -> List[str]:
    """Get all available nodes: active participants + genesis nodes"""
    active = fetch_active_nodes()
    all_nodes = list(set(active + GENESIS_NODES))
    random.shuffle(all_nodes)
    return all_nodes


def create_gonka_client_with_retry(max_retries: int = 3) -> tuple[GonkaOpenAI, str]:
    """Create GonkaOpenAI client with automatic node failover."""
    private_key = os.getenv("GONKA_PRIVATE_KEY")
    if not private_key:
        raise ValueError("GONKA_PRIVATE_KEY not found in environment variables")
    
    fixed_node = os.getenv("GONKA_NODE_URL")
    if fixed_node:
        print(f"{CLI_CYAN}🔗 Using fixed node from ENV: {fixed_node}{CLI_CLR}")
        client = GonkaOpenAI(gonka_private_key=private_key, source_url=fixed_node)
        return client, fixed_node
    
    nodes = get_available_nodes()
    print(f"{CLI_CYAN}🌐 Found {len(nodes)} available nodes{CLI_CLR}")
    
    for node_url in nodes[:max_retries]:
        try:
            print(f"{CLI_YELLOW}🔗 Trying node: {node_url}{CLI_CLR}")
            client = GonkaOpenAI(gonka_private_key=private_key, source_url=node_url)
            return client, node_url
        except Exception as e:
            print(f"{CLI_RED}✗ Failed to connect to {node_url}: {e}{CLI_CLR}")
    
    fallback = GENESIS_NODES[0]
    print(f"{CLI_YELLOW}⚠ Using fallback genesis node: {fallback}{CLI_CLR}")
    client = GonkaOpenAI(gonka_private_key=private_key, source_url=fallback)
    return client, fallback


# === SGR System Prompt (V4 - Reasoning Enhanced) ===
SGR_SYSTEM_PROMPT = '''You are the ERC3 Store Agent, a strategic and highly reliable autonomous shopper.

Your goal is to complete the user's task efficiently and accurately. You must THINK before you ACT.

## 🧠 MENTAL PROTOCOL (Follow in "thoughts")

1. **ANALYZE STATE**:
   - What is in my basket right now?
   - What coupon is active?
   - What is the total price?
   - Do I have enough information? (If not, `list_products`)

2. **PLANNING**:
   - **Quantity First**: If task says "Buy 24", I need exactly 24 units. Not 23, not 25.
   - **Integer Partitioning**: If need N units, do not just test homogeneous packs.
     - You MUST test mixed combinations if they sum to N.
     - Example: Need 24. Available: 6pk, 12pk.
     - Test: 4x6pk.
     - Test: 2x12pk.
     - Test: 2x6pk + 1x12pk (Mixed!).
   - **Search Strategy**: If I haven't found the item, keep searching (pagination).
   - **Coupon Matrix Testing (MANDATORY)**:
     - **Rule**: For EVERY basket configuration you build, you MUST cycle through ALL available coupons.
     - **Example**: If you have a "COMBO" basket, apply `COMBO`, then `SALEX`, then `BULK24`.
     - **Why**: `SALEX` might give a bigger discount on the "COMBO" basket than `COMBO` does!
     - **Bundle Strategy**: If a coupon suggests a bundle (e.g. "BUNDLE30"), test minimal additions.
       - Test: Product + Accessory A.
       - Test: Product + Accessory B.
       - NOT just: Product + Accessory A + Accessory B.

3. **VERIFICATION (Crucial)**:
   - **STATE CHECK**: Look at the LAST `view_basket` output in the conversation.
     - Does it have the *best* coupon applied?
     - Is the total price the *lowest* you found?
     - If NO: You MUST apply the best coupon/items again.
   - **RESTORE BEFORE CHECKOUT**: If you tested Coupon B (worse) after Coupon A (best), the basket currently has Coupon B. You MUST re-apply Coupon A.
   - ASK: "Did I miss a permutation?" (e.g. Printer+Paper vs Printer+Paper+Cable)

## ⛔ CRITICAL RULES
1. **NO COUPOUN BIAS**: Never assume a coupon ONLY works for what its name implies. Test it.
2. **NO PHANTOM CHECKOUTS**: Do not checkout if the price is not what you expect.
3. **IMPOSSIBLE TASK**: If you cannot fulfill the exact quantity requested (e.g. want 5 but only 2 in stock), DO NOT checkout. Set `is_final: true` and explain why.
4. **PAGINATION**: If `limit exceeded`, retry with `limit=5`. If `next_offset != -1`, there are more products.
5. **RESTORE STATE**: The API is stateful. If you change the basket to test a hypothesis, you must change it back if that hypothesis failed. ALWAYS re-apply the best coupon before checkout.

## 🛠 API TOOLS

| Tool | Args | Usage |
|------|------|-------|
| `list_products` | `offset` (int), `limit` (int) | Explore catalog. Start limit=10. |
| `view_basket` | - | Check subtotal, discount, final total. |
| `add_product_to_basket` | `sku` (str), `quantity` (int) | Add items. |
| `remove_item_from_basket` | `sku` (str), `quantity` (int) | Remove items. |
| `apply_coupon` | `coupon` (str) | Apply a code. Overwrites previous. |
| `remove_coupon` | - | Clear coupon. |
| `checkout_basket` | - | Finalize purchase. Irreversible. |

## 📋 RESPONSE FORMAT

You must respond with a JSON object.

```json
{
  "thoughts": "1. [State Analysis] 2. [Hypothesis/Plan] 3. [Verification]",
  "action_queue": [
    {"tool": "tool_name", "args": {"arg1": "value"}}
  ],
  "is_final": false
}
```

- `is_final`: Set to `true` ONLY after successful checkout or if task is impossible.
- `action_queue`: You can batch actions (e.g. Add + Apply + View).

## 💡 HINTS FOR SUCCESS

- **"Best Discount" Tasks**: Test ALL coupons. Keep a mental log: "Coupon A: $40, Coupon B: $30". Winner: B. Apply B -> Checkout.
- **"Cheapest Basket" Tasks**: You might need to test completely different baskets.
  - Basket 1 (24x Single): $50.
  - Basket 2 (4x 6-Pack): $45.
  - Compare -> Build Winner -> Checkout.
- **Failures**: If checkout fails, read the error. "Insufficient stock"? Reduce quantity. "Invalid coupon"? Try another.

Begin!
'''


def parse_action(action_dict: dict) -> Optional[StoreAction]:
    """Parse action dict into Pydantic model (SGR dispatch)"""
    tool = action_dict.get("tool", "").lower().replace("_", "").replace("-", "")
    
    # BUGFIX: LLM sometimes wraps params in "args" dict, flatten it
    args = action_dict.get("args", {})
    if args:
        # Merge args into top-level (args take precedence)
        action_dict = {**action_dict, **args}
    
    try:
        if tool in ["listproducts", "list", "search", "browse"]:
            # Req_ListProducts: offset, limit (NO category/search!)
            return store.Req_ListProducts(
                offset=action_dict.get("offset", 0),
                limit=action_dict.get("limit", 10)
            )
        
        elif tool in ["viewbasket", "basket", "cart", "view"]:
            return store.Req_ViewBasket()
        
        elif tool in ["addproducttobasket", "add", "addproduct", "addtobasket"]:
            # Req_AddProductToBasket: sku, quantity
            sku = action_dict.get("sku") or action_dict.get("product_id")
            if not sku:
                print(f"{CLI_RED}⚠ Missing 'sku' field in add_product_to_basket{CLI_CLR}")
                return None
            return store.Req_AddProductToBasket(
                sku=sku,
                quantity=action_dict.get("quantity", 1)
            )
        
        elif tool in ["removeitemfrombasket", "remove", "removeitem", "removefrombasket", "removeproduct"]:
            # Req_RemoveItemFromBasket: sku, quantity
            sku = action_dict.get("sku") or action_dict.get("item_id") or action_dict.get("product_id")
            return store.Req_RemoveItemFromBasket(
                sku=sku or "",
                quantity=action_dict.get("quantity", 0)
            )
        
        elif tool in ["applycoupon", "apply"]:
            # Req_ApplyCoupon: coupon (NOT code!)
            coupon = action_dict.get("coupon") or action_dict.get("code") or ""
            return store.Req_ApplyCoupon(coupon=coupon)
        
        elif tool in ["removecoupon", "unapplycoupon", "unapply"]:
            # Req_RemoveCoupon: NO parameters!
            return store.Req_RemoveCoupon()
        
        elif tool in ["checkoutbasket", "checkout", "buy", "purchase", "complete"]:
            return store.Req_CheckoutBasket()
        
        else:
            print(f"{CLI_RED}⚠ Unknown tool: {tool}{CLI_CLR}")
            return None
            
    except KeyError as e:
        print(f"{CLI_RED}⚠ Missing field {e} for tool {tool}{CLI_CLR}")
        return None
    except Exception as e:
        print(f"{CLI_RED}⚠ Error parsing action: {e}{CLI_CLR}")
        return None


def extract_json(content: str) -> dict:
    """Extract JSON from LLM response (handles markdown blocks)"""
    content = content.strip()
    
    # Remove markdown code blocks
    if "```json" in content:
        start = content.find("```json") + 7
        end = content.find("```", start)
        if end > start:
            content = content[start:end].strip()
    elif "```" in content:
        start = content.find("```") + 3
        end = content.find("```", start)
        if end > start:
            content = content[start:end].strip()
    
    # Find JSON object boundaries
    if not content.startswith("{"):
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            content = content[start:end]
    
    return json.loads(content)


def call_llm_with_retry(client: GonkaOpenAI, model: str, messages: list, 
                        max_retries: int = 3, timeout: int = 120) -> Optional[dict]:
    """Call LLM with retry logic on single node."""
    last_error = None
    
    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.0,
                timeout=timeout,
            )
            return completion
        except Exception as e:
            last_error = e
            wait_time = (attempt + 1) * 2
            print(f"{CLI_YELLOW}⚠ Attempt {attempt+1}/{max_retries} failed: {e}{CLI_CLR}")
            if attempt < max_retries - 1:
                print(f"{CLI_YELLOW}   Retrying in {wait_time}s...{CLI_CLR}")
                time.sleep(wait_time)
    
    return None  # Signal to try another node


def call_llm_with_failover(client: GonkaOpenAI, model: str, messages: list,
                           retries_per_node: int = 3, max_node_switches: int = 5,
                           timeout: int = 120) -> tuple[Optional[dict], GonkaOpenAI]:
    """
    Call LLM with failover: try 3 times per node, switch nodes up to 5 times.
    Returns (completion, current_client) - client may have changed!
    """
    current_client = client
    tried_nodes = set()
    
    for node_attempt in range(max_node_switches):
        # Try current node
        result = call_llm_with_retry(current_client, model, messages, retries_per_node, timeout)
        
        if result is not None:
            return result, current_client
        
        # Node failed after retries - try switching to another node
        print(f"{CLI_YELLOW}⚠ Node failed after {retries_per_node} retries. Switching node ({node_attempt+1}/{max_node_switches})...{CLI_CLR}")
        
        # Get available nodes and pick a random one we haven't tried
        available_nodes = get_available_nodes()
        
        # Try to find a node we haven't used yet
        new_node = None
        random.shuffle(available_nodes)
        for node_url in available_nodes:
            if node_url not in tried_nodes:
                new_node = node_url
                break
        
        if new_node is None:
            # All nodes tried, pick random from all
            new_node = random.choice(available_nodes) if available_nodes else None
        
        if new_node is None:
            print(f"{CLI_RED}✗ No available nodes to failover to{CLI_CLR}")
            break
        
        tried_nodes.add(new_node)
        
        try:
            print(f"{CLI_CYAN}🔄 Switching to node: {new_node}{CLI_CLR}")
            private_key = os.getenv("GONKA_PRIVATE_KEY")
            current_client = GonkaOpenAI(gonka_private_key=private_key, source_url=new_node)
        except Exception as e:
            print(f"{CLI_RED}✗ Failed to connect to {new_node}: {e}{CLI_CLR}")
            continue
    
    print(f"{CLI_RED}✗ All {max_node_switches} node switches failed. Giving up.{CLI_CLR}")
    return None, current_client


def run_agent(model: str, api: ERC3, task: TaskInfo, 
              stats: SessionStats = None, client: GonkaOpenAI = None, 
              pricing_model: str = None, max_turns: int = 50,
              failure_logger = None):
    """Run SGR agent for a single task."""
    if client is None:
        client, _ = create_gonka_client_with_retry()
    
    cost_model_id = pricing_model or model
    store_api = api.get_store_client(task)
    
    # === STATE TRACKING ===
    checkout_done = False  # CRITICAL: Prevent multiple checkouts
    
    messages = [
        {"role": "system", "content": SGR_SYSTEM_PROMPT},
        {"role": "user", "content": f"TASK: {task.task_text}\n\nStart by listing products with offset=0, limit=10."},
    ]
    
    for turn in range(max_turns):
        # === EARLY EXIT: Stop if checkout already done ===
        if checkout_done:
            print(f"{CLI_GREEN}✓ Checkout already completed. Ending agent loop.{CLI_CLR}")
            break
        
        print(f"\n{CLI_BLUE}═══ Turn {turn + 1}/{max_turns} ═══{CLI_CLR}")
        
        started = time.time()
        # Use failover: 3 retries per node, up to 5 node switches
        completion, client = call_llm_with_failover(client, model, messages)
        duration = time.time() - started
        
        if completion is None:
            print(f"{CLI_RED}✗ LLM call failed after all failover attempts{CLI_CLR}")
            break
        
        # Telemetry
        if stats and completion.usage:
            stats.add_llm_usage(cost_model_id, completion.usage)
        
        api.log_llm(
            task_id=task.task_id,
            model=model,
            duration_sec=duration,
            usage=completion.usage,
        )
        
        raw_content = completion.choices[0].message.content
        
        # FULL LOG - no truncation
        print(f"{CLI_CYAN}[Raw Response]:{CLI_CLR}")
        print(raw_content)
        print()
        
        # Parse JSON
        try:
            parsed = extract_json(raw_content)
        except json.JSONDecodeError as e:
            print(f"{CLI_RED}✗ JSON parse error: {e}{CLI_CLR}")
            messages.append({"role": "assistant", "content": raw_content})
            messages.append({
                "role": "user",
                "content": "[SYSTEM ERROR]: Invalid JSON. Respond with ONLY a valid JSON object."
            })
            continue
        
        thoughts = parsed.get("thoughts", "")
        action_queue = parsed.get("action_queue", [])
        is_final = parsed.get("is_final", False)
        
        print(f"{CLI_GREEN}[Thoughts]:{CLI_CLR} {thoughts}")
        print(f"{CLI_GREEN}[Actions]:{CLI_CLR} {len(action_queue)} action(s), is_final={is_final}")
        print(f"{CLI_GREEN}[Action Queue]:{CLI_CLR} {json.dumps(action_queue, indent=2)}")
        
        # Log LLM turn for failure analysis
        if failure_logger:
            failure_logger.log_llm_turn(task.task_id, turn + 1, raw_content, action_queue)
        
        messages.append({"role": "assistant", "content": raw_content})
        
        if is_final and not action_queue:
            print(f"{CLI_GREEN}✓ Agent completed task{CLI_CLR}")
            break
        
        # Execute actions
        results = []
        stop_execution = False
        
        for idx, action_dict in enumerate(action_queue):
            if stop_execution:
                break
            
            print(f"\n  {CLI_BLUE}▶ Parsing action {idx+1}:{CLI_CLR} {json.dumps(action_dict)}")
            
            action = parse_action(action_dict)
            if action is None:
                results.append(f"Action {idx+1}: SKIPPED (invalid format)")
                continue
            
            action_name = action.__class__.__name__
            
            # === CHECKOUT GUARD: Prevent multiple successful checkouts ===
            if action_name == "Req_CheckoutBasket":
                if checkout_done:
                    print(f"  {CLI_RED}⚠ BLOCKED: Checkout already succeeded! Task is complete.{CLI_CLR}")
                    results.append(f"Action {idx+1} ({action_name}): BLOCKED - checkout already performed")
                    stop_execution = True
                    continue
            
            print(f"  {CLI_BLUE}▶ Executing:{CLI_CLR} {action_name}")
            print(f"     {action.model_dump_json()}")
            
            try:
                if stats:
                    stats.add_api_call()
                
                result = store_api.dispatch(action)
                result_json = result.model_dump_json(exclude_none=True)
                result_dict = result.model_dump(exclude_none=True)
                
                # FULL result - no truncation
                print(f"  {CLI_GREEN}✓ SUCCESS:{CLI_CLR}")
                print(f"     {result_json}")
                
                # Log API call for failure analysis
                if failure_logger:
                    failure_logger.log_api_call(
                        task.task_id, action_name,
                        action.model_dump(), result_dict
                    )
                
                results.append(f"Action {idx+1} ({action_name}): SUCCESS\nResult: {result_json}")
                
                # After SUCCESSFUL checkout: mark done and stop
                if action_name == "Req_CheckoutBasket":
                    checkout_done = True  # Only mark done on SUCCESS
                    print(f"  {CLI_GREEN}✓ CHECKOUT COMPLETE - task finished{CLI_CLR}")
                    stop_execution = True
                
            except ApiException as e:
                error_msg = e.api_error.error if e.api_error else str(e)
                print(f"  {CLI_RED}✗ FAILED:{CLI_CLR} {error_msg}")
                
                # Log API error for failure analysis
                if failure_logger:
                    failure_logger.log_api_call(
                        task.task_id, action_name,
                        action.model_dump(), {}, error=error_msg
                    )
                
                results.append(f"Action {idx+1} ({action_name}): FAILED\nError: {error_msg}")
                
                # On checkout failure: allow retry (don't mark checkout_done)
                # On other failures: stop current batch but allow agent to continue
                if action_name == "Req_CheckoutBasket":
                    results.append("[HINT]: Checkout failed. Adjust basket (reduce quantity or change items) and retry checkout.")
                stop_execution = True
        
        # Feed results back
        if results:
            feedback = "\n---\n".join(results)
            messages.append({
                "role": "user",
                "content": f"[EXECUTION LOG]\n{feedback}"
            })
        else:
            messages.append({
                "role": "user",
                "content": "[SYSTEM]: No actions executed. Set is_final=true if done, otherwise add actions."
            })
    
    print(f"\n{CLI_BLUE}═══ Agent finished ═══{CLI_CLR}")
