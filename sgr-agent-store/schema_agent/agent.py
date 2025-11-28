import time
import os
from typing import List, Union, Literal
from pydantic import BaseModel, Field
from erc3 import store, ApiException, TaskInfo, ERC3
from openai import OpenAI
# Add parent directory to path to import pricing
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pricing import calculator  

client = OpenAI(
    default_headers={
        "HTTP-Referer": os.getenv("HTTP_REFERER", ""),
        "X-Title": os.getenv("X_TITLE", "ERC3 Agent"),
    }
)

# --- Статистика и Биллинг ---
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
            
            # Считаем деньги через утилиту pricing
            cost = calculator.calculate_cost(
                model, 
                usage.prompt_tokens, 
                usage.completion_tokens
            )
            self.total_cost_usd += cost

    def add_api_call(self):
        self.store_api_requests += 1

    def print_report(self):
        total_tokens = self.total_prompt_tokens + self.total_completion_tokens
        print("\n" + "="*45)
        print(f"📊 SESSION STATISTICS REPORT")
        print("="*45)
        print(f"🧠 LLM Requests:      {self.llm_requests}")
        print(f"🛒 Store API Calls:   {self.store_api_requests}")
        print("-" * 25)
        print(f"📥 Input Tokens:      {self.total_prompt_tokens}")
        print(f"📤 Output Tokens:     {self.total_completion_tokens}")
        print(f"∑  Total Tokens:      {total_tokens}")
        print("-" * 25)
        print(f"💰 TOTAL COST:        ${self.total_cost_usd:.6f}")
        print("="*45 + "\n")


# --- Схема Данных (SGR Interface) ---

class ReportTaskCompletion(BaseModel):
    tool: Literal["report_completion"]
    completed_steps_laconic: List[str]
    code: Literal["completed", "failed"]

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
    # Теперь описание чистое и универсальное. Вся логика "как думать" ушла в промпт.
    thoughts: str = Field(..., description="Detailed reasoning following the 'Mental Checklist' logic.")
    action_queue: List[StoreAction] = Field(..., description="Sequence of actions to execute. Stops on error.")
    is_final: bool = Field(False, description="Set True only when task is fully done or strictly impossible.")

# --- System Prompt (The Brain) ---
system_prompt = """
You are a strategic, highly reliable e-commerce shopping agent.

### MENTAL CHECKLIST (Must follow in `thoughts`):
1. **Search Status**: Have I seen ALL pages? If `next_offset != -1`, I MUST paginate before saying "Item not found".
2. **Inventory Math**: Do I have enough stock? If requesting 5 but only 2 available -> STOP (unless "buy available").
3. **Coupon Strategy**: Coupon names are misleading. Have I empirically tested ALL codes on the FINAL basket?
4. **Execution Plan**: Can I batch these actions?

### CRITICAL RULES:

1. **Exhaustive Search**: 
   - Never conclude an item is missing based on the first page. 
   - **Loop**: `List(offset) -> List(offset+limit)` until `next_offset` is -1.

2. **Empirical Coupon Testing (Brute Force)**:
   - Don't guess. **Test**.
   - Protocol: `[Apply(A), View, Remove, Apply(B), View, Remove...]`
   - Pick the winner strictly by the lowest `total`.

3. **Strict Constraints**:
   - If user asks for specific quantity/bundle and it's missing -> **ABORT**.
   - If checkout fails (inventory trap) -> Remove excess -> Retry.

4. **Technical**:
   - Start with `limit: 10`. If error "limit exceeded", retry with allowed limit.
   - Batch logic: Group `Add` + `Apply` + `Checkout` to save turns.
"""

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_BLUE = "\x1B[34m"
CLI_CLR = "\x1B[0m"

def run_agent(model: str, api: ERC3, task: TaskInfo, stats: SessionStats = None):
    store_api = api.get_store_client(task)

    log = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"TASK: {task.task_text}"},
    ]

    # 25 ходов достаточно для сложных симуляций купонов и долгой пагинации
    for i in range(25): 
        step_label = f"turn_{i + 1}"
        print(f"\n{CLI_BLUE}Thinking {step_label}...{CLI_CLR}")

        started = time.time()
        
        try:
            completion = client.beta.chat.completions.parse(
                model=model,
                response_format=NextStep,
                messages=log,
                temperature=0.0, # Строгость для математики и логики
            )
        except Exception as e:
            print(f"{CLI_RED}LLM Critical Error: {e}{CLI_CLR}")
            break

        # --- TELEMETRY (Cost & Tokens) ---
        if stats:
            try:
                stats.add_llm_usage(model, completion.usage)
            except Exception:
                # Fallback если сигнатура метода отличается или usage None
                pass

        api.log_llm(
            task_id=task.task_id,
            model=model,
            duration_sec=time.time() - started,
            usage=completion.usage,
        )

        plan = completion.choices[0].message.parsed
        print(f"[Thoughts]: {plan.thoughts}")

        log.append({
            "role": "assistant", 
            "content": plan.model_dump_json()
        })

        if plan.is_final and not plan.action_queue:
            print(f"{CLI_GREEN}Agent finished (Final & Empty Queue).{CLI_CLR}")
            break

        # --- Execution Loop ---
        execution_results = []
        stop_queue = False

        for idx, action in enumerate(plan.action_queue):
            if stop_queue: break

            act_name = action.__class__.__name__
            print(f"  > Executing {idx+1}/{len(plan.action_queue)}: {act_name}")
            
            try:
                # --- TELEMETRY (API Calls) ---
                if stats:
                    stats.add_api_call()
                    
                result = store_api.dispatch(action)
                res_json = result.model_dump_json(exclude_none=True)
                
                preview = (res_json[:100] + '..') if len(res_json) > 100 else res_json
                print(f"    {CLI_GREEN}OK{CLI_CLR}: {preview}")
                
                execution_results.append(f"Action {idx+1} ({act_name}): SUCCESS\nResult: {res_json}")

            except ApiException as e:
                error_msg = e.api_error.error if e.api_error else str(e)
                print(f"    {CLI_RED}FAIL{CLI_CLR}: {error_msg}")
                
                execution_results.append(f"Action {idx+1} ({act_name}): FAILED\nError: {error_msg}\n[SYSTEM]: Execution stopped. Re-evaluate strategy based on error.")
                stop_queue = True 

        # Возврат результатов в историю
        if execution_results:
            feedback_msg = "\n".join(execution_results)
            log.append({
                "role": "user",
                "content": f"[System Execution Log]\n{feedback_msg}"
            })
        else:
            log.append({
                "role": "user", 
                "content": "[System]: Action queue was empty. If task is done, set is_final=True. If searching, verify pagination."
            })