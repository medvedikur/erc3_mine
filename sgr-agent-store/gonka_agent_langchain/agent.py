import json
import time
from typing import List, Optional
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate

from erc3 import TaskInfo, ERC3
from .gonka_llm import GonkaChatModel
from .prompts import SGR_SYSTEM_PROMPT
from .tools import parse_action, execute_action
from .stats import SessionStats, FailureLogger
from .models import NextStep

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_BLUE = "\x1B[34m"
CLI_CYAN = "\x1B[36m"
CLI_CLR = "\x1B[0m"

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

def run_agent(model_name: str, api: ERC3, task: TaskInfo, 
              stats: SessionStats = None, 
              pricing_model: str = None, 
              max_turns: int = 50,
              failure_logger: FailureLogger = None):
    
    # Initialize LangChain Model
    llm = GonkaChatModel(model=model_name)
    store_api = api.get_store_client(task)
    cost_model_id = pricing_model or model_name

    # Initial Messages
    messages = [
        SystemMessage(content=SGR_SYSTEM_PROMPT),
        HumanMessage(content=f"TASK: {task.task_text}\n\nStart by listing products with offset=0, limit=10.")
    ]

    checkout_done = False

    for turn in range(max_turns):
        if checkout_done:
            print(f"{CLI_GREEN}✓ Checkout already completed. Ending agent loop.{CLI_CLR}")
            break

        print(f"\n{CLI_BLUE}═══ Turn {turn + 1}/{max_turns} ═══{CLI_CLR}")

        # Invoke LLM
        started = time.time()
        try:
            # We use generate to get usage info easily
            result = llm.generate([messages])
            generation = result.generations[0][0]
            llm_output = result.llm_output or {}
            
            raw_content = generation.text
            usage = llm_output.get("token_usage")
            
            # Helper class to match usage object structure expected by stats
            class Usage:
                def __init__(self, u):
                    self.prompt_tokens = u.get("prompt_tokens", 0)
                    self.completion_tokens = u.get("completion_tokens", 0)
            
            if stats and usage:
                stats.add_llm_usage(cost_model_id, Usage(usage))

            # Log to ERC3
            api.log_llm(
                task_id=task.task_id,
                model=model_name,
                duration_sec=time.time() - started,
                usage=usage
            )

        except Exception as e:
            print(f"{CLI_RED}✗ LLM call failed: {e}{CLI_CLR}")
            break

        print(f"{CLI_CYAN}[Raw Response]:{CLI_CLR}")
        print(raw_content)
        print()

        # Parse JSON
        try:
            parsed = extract_json(raw_content)
        except json.JSONDecodeError as e:
            print(f"{CLI_RED}✗ JSON parse error: {e}{CLI_CLR}")
            messages.append(AIMessage(content=raw_content))
            messages.append(HumanMessage(content="[SYSTEM ERROR]: Invalid JSON. Respond with ONLY a valid JSON object."))
            continue

        thoughts = parsed.get("thoughts", "")
        action_queue = parsed.get("action_queue", [])
        is_final = parsed.get("is_final", False)

        print(f"{CLI_GREEN}[Thoughts]:{CLI_CLR} {thoughts}")
        print(f"{CLI_GREEN}[Actions]:{CLI_CLR} {len(action_queue)} action(s), is_final={is_final}")
        
        if failure_logger:
            failure_logger.log_llm_turn(task.task_id, turn + 1, raw_content, action_queue)

        messages.append(AIMessage(content=raw_content))

        if is_final and not action_queue:
            print(f"{CLI_GREEN}✓ Agent completed task{CLI_CLR}")
            break

        # Execute Actions
        results = []
        stop_execution = False

        for idx, action_dict in enumerate(action_queue):
            if stop_execution:
                break
            
            print(f"\n  {CLI_BLUE}▶ Parsing action {idx+1}:{CLI_CLR} {json.dumps(action_dict)}")
            
            action_model = parse_action(action_dict)
            if not action_model:
                results.append(f"Action {idx+1}: SKIPPED (invalid format)")
                continue
            
            action_name = action_model.__class__.__name__

            if action_name == "Req_CheckoutBasket":
                if checkout_done:
                     results.append(f"Action {idx+1} ({action_name}): BLOCKED - checkout already performed")
                     stop_execution = True
                     continue

            print(f"  {CLI_BLUE}▶ Executing:{CLI_CLR} {action_name}")
            
            if stats:
                stats.add_api_call()

            result_str = execute_action(action_model, store_api)
            
            if "ERROR:" in result_str:
                print(f"  {CLI_RED}✗ FAILED:{CLI_CLR} {result_str}")
                if failure_logger:
                    failure_logger.log_api_call(task.task_id, action_name, action_model.model_dump(), {}, error=result_str)
            else:
                print(f"  {CLI_GREEN}✓ SUCCESS:{CLI_CLR}")
                print(f"     {result_str}")
                if failure_logger:
                    failure_logger.log_api_call(task.task_id, action_name, action_model.model_dump(), json.loads(result_str))

            results.append(f"Action {idx+1} ({action_name}): {'FAILED' if 'ERROR:' in result_str else 'SUCCESS'}\nResult: {result_str}")

            if action_name == "Req_CheckoutBasket":
                if "ERROR:" not in result_str:
                    checkout_done = True
                    print(f"  {CLI_GREEN}✓ CHECKOUT COMPLETE - task finished{CLI_CLR}")
                    stop_execution = True
                else:
                    results.append("[HINT]: Checkout failed. Adjust basket and retry.")
                    stop_execution = True
            
            if "ERROR:" in result_str:
                 # Stop batch on error mostly, but maybe continue? 
                 # Original logic: stop execution on failure
                 stop_execution = True

        # Feed back results
        if results:
            feedback = "\n---\n".join(results)
            messages.append(HumanMessage(content=f"[EXECUTION LOG]\n{feedback}"))
        else:
             messages.append(HumanMessage(content="[SYSTEM]: No actions executed. Set is_final=true if done, otherwise add actions."))

    print(f"\n{CLI_BLUE}═══ Agent finished ═══{CLI_CLR}")

