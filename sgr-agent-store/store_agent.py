import time
import os
import re
from typing import Annotated, List, Union, Literal
from annotated_types import MaxLen
from pydantic import BaseModel, Field
from erc3 import store, ApiException, TaskInfo, ERC3
from openai import OpenAI

# Initialize OpenAI client with optional OpenRouter headers
client = OpenAI(
    default_headers={
        "HTTP-Referer": os.getenv("HTTP_REFERER", ""),
        "X-Title": os.getenv("X_TITLE", "ERC3 Agent"),
    }
)

class ReportTaskCompletion(BaseModel):
    """Only use AFTER successful checkout or when task is truly impossible."""
    tool: Literal["report_completion"]
    summary: Annotated[str, MaxLen(200)] = Field(..., description="Brief summary of what was done")
    code: Literal["completed", "failed"]

class NextStep(BaseModel):
    """Keep responses SHORT to avoid token limits."""
    # Brief reasoning - keep it under 100 chars
    thought: Annotated[str, MaxLen(150)] = Field(..., description="Brief reasoning about next action")
    # Single action to execute
    function: Union[
        ReportTaskCompletion,
        store.Req_ListProducts,
        store.Req_ViewBasket,
        store.Req_ApplyCoupon,
        store.Req_RemoveCoupon,
        store.Req_AddProductToBasket,
        store.Req_RemoveItemFromBasket,
        store.Req_CheckoutBasket,
    ] = Field(..., description="Next action to execute")

system_prompt = """
You are an e-commerce assistant. Keep ALL responses SHORT.

CRITICAL RULES:
1. Purchase = Checkout must succeed. No checkout = task NOT done.
2. Parse task for: products, quantities, coupon codes.
3. KEEP "thought" field under 100 characters!

PAGINATION:
- Start: limit=10, offset=0
- Error "limit exceeded: X > Y" → use limit=Y
- Paginate until next_offset=-1

WORKFLOW: List → Add → Apply coupon (if any) → Checkout → Report

ERRORS:
- "limit exceeded: X > Y" → retry with limit=Y
- "insufficient inventory: available N" → remove excess, retry checkout
"""

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_CLR = "\x1B[0m"

def run_agent(model: str, api: ERC3, task: TaskInfo):

    store_api = api.get_store_client(task)

    # log will contain conversation context for the agent within task
    log = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task.task_text},
    ]

    parse_retries = 0  # Track consecutive parse failures
    MAX_PARSE_RETRIES = 2

    # let's limit number of reasoning steps by 25, to allow for error recovery
    for i in range(25):
        step = f"step_{i + 1}"
        print(f"Next {step}... ", end="")

        started = time.time()

        try:
            completion = client.beta.chat.completions.parse(
                model=model,
                response_format=NextStep,
                messages=log,
                max_completion_tokens=2000,  # Reduced from 10000 to prevent endless reasoning
                temperature=0,  # Deterministic output
            )
        except Exception as e:
            print(f"{CLI_RED}LLM API Error: {e}{CLI_CLR}")
            break

        api.log_llm(
            task_id=task.task_id,
            model=model,
            duration_sec=time.time() - started,
            usage=completion.usage,
        )

        # Handle parsing failures (length limit reached or invalid JSON)
        job = completion.choices[0].message.parsed
        if job is None:
            parse_retries += 1
            raw_content = completion.choices[0].message.content or ""
            print(f"{CLI_RED}Parse failed (attempt {parse_retries}/{MAX_PARSE_RETRIES}){CLI_CLR}")
            
            if parse_retries >= MAX_PARSE_RETRIES:
                print(f"{CLI_RED}Max parse retries reached, aborting{CLI_CLR}")
                break
            
            # Add error message and retry
            log.append({
                "role": "assistant",
                "content": raw_content[:500] if raw_content else "Failed to generate valid JSON"
            })
            log.append({
                "role": "user",
                "content": "ERROR: Your response was not valid JSON or was truncated. Keep 'thought' under 100 chars. Try again with a shorter response."
            })
            continue
        
        parse_retries = 0  # Reset on successful parse

        # if SGR wants to finish, then quit loop
        if isinstance(job.function, ReportTaskCompletion):
            # Validate that checkout was actually done for "completed" status
            if job.function.code == "completed":
                checkout_done = any(
                    "Req_CheckoutBasket" in str(msg.get("tool_calls", []))
                    for msg in log if msg.get("role") == "assistant"
                )
                if not checkout_done:
                    # Force agent to continue - checkout is required
                    print(f"{CLI_RED}VALIDATION: No checkout found, forcing continuation{CLI_CLR}")
                    log.append({
                        "role": "user", 
                        "content": "ERROR: No Checkout was done. A purchase requires Checkout. Do Checkout now."
                    })
                    continue
            
            print(f"Agent {job.function.code}: {job.function.summary}")
            break

        # print next step for debugging
        print(f"{job.thought}\n  {job.function}")

        # Let's add tool request to conversation history
        log.append({
            "role": "assistant",
            "content": job.thought,
            "tool_calls": [{
                "type": "function",
                "id": step,
                "function": {
                    "name": job.function.__class__.__name__,
                    "arguments": job.function.model_dump_json(),
                }}]
        })

        # now execute the tool by dispatching command to our handler
        try:
            result = store_api.dispatch(job.function)
            txt = result.model_dump_json(exclude_none=True, exclude_unset=True)
            print(f"{CLI_GREEN}OUT{CLI_CLR}: {txt}")
        except ApiException as e:
            txt = e.detail
            # Enrich error message with hints for the agent
            if "page limit exceeded" in txt:
                # Extract max limit from error like "page limit exceeded: 50 > 10"
                match = re.search(r'> (\d+)', txt)
                if match:
                    txt += f"\nHINT: Use limit={match.group(1)} or less."
            elif "insufficient inventory" in txt:
                # Extract available count from error
                match = re.search(r'available (\d+)', txt)
                if match:
                    txt += f"\nHINT: Only {match.group(1)} available. Remove excess from basket and retry checkout."
            # print to console as ascii red
            print(f"{CLI_RED}ERR: {e.api_error.error}{CLI_CLR}")

        # and now we add results back to the conversation history
        log.append({"role": "tool", "content": txt, "tool_call_id": step})