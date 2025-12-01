import json
import time
from typing import List, Optional, Any
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import ValidationError

from erc3 import TaskInfo, ERC3, ApiException
from erc3.erc3 import client
from gonka_llm import GonkaChatModel
from prompts import SGR_SYSTEM_PROMPT
from tools import parse_action, Req_Respond
from stats import SessionStats, FailureLogger
from handlers import get_executor, WikiManager, SecurityManager

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

# Define a simple usage class that mimics the OpenAI usage object structure expected by erc3
class OpenAIUsage:
    def __init__(self, prompt_tokens=0, completion_tokens=0, total_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
    
    def model_dump(self, mode: str = 'python', include = None, exclude = None, by_alias: bool = False, exclude_unset: bool = False, exclude_defaults: bool = False, exclude_none: bool = False, round_trip: bool = False, warnings: bool = True):
        data = {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens
        }
        return data

def run_agent(model_name: str, api: ERC3, task: TaskInfo, 
              stats: SessionStats = None, 
              pricing_model: str = None, 
              max_turns: int = 20,
              failure_logger: FailureLogger = None,
              wiki_manager: WikiManager = None):
    
    # Initialize LangChain Model
    llm = GonkaChatModel(model=model_name)
    erc_client = api.get_erc_dev_client(task)
    cost_model_id = pricing_model or model_name

    # Initialize Managers
    if wiki_manager:
        wiki_manager.set_api(erc_client)
    else:
        # Fallback if not provided (should be provided by main)
        wiki_manager = WikiManager(erc_client)
    
    security_manager = SecurityManager()
    
    # Initial Messages
    # We add a hint about available tools and the wiki state
    
    # We don't provide a simulated date initially, forcing the agent to check via who_am_i
    # This prevents the agent from hallucinating or using a stale date from a previous run
    
    messages = [
        SystemMessage(content=SGR_SYSTEM_PROMPT),
        HumanMessage(content=f"TASK: {task.task_text}\n\nContext: {wiki_manager.get_context_summary()}")
    ]

    task_done = False

    for turn in range(max_turns):
        if task_done:
            print(f"{CLI_GREEN}✓ Task marked done. Ending agent loop.{CLI_CLR}")
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
            usage = llm_output.get("token_usage", {})
            
            if not usage or usage.get("total_tokens", 0) == 0:
                est_completion = len(raw_content) // 4
                est_prompt = sum(len(m.content) for m in messages) // 4
                usage = {
                    "prompt_tokens": est_prompt,
                    "completion_tokens": est_completion,
                    "total_tokens": est_prompt + est_completion
                }
            
            usage_obj = OpenAIUsage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0)
            )

            if stats:
                stats.add_llm_usage(cost_model_id, usage_obj)

            # Log to ERC3
            api.log_llm(
                task_id=task.task_id,
                model=model_name,
                duration_sec=time.time() - started,
                usage=usage_obj
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
        plan = parsed.get("plan", [])
        action_queue = parsed.get("action_queue", [])
        is_final = parsed.get("is_final", False)

        print(f"{CLI_GREEN}[Thoughts]:{CLI_CLR} {thoughts}")

        if plan:
            print(f"{CLI_GREEN}[Plan]:{CLI_CLR}")
            for item in plan:
                if isinstance(item, dict):
                    status = item.get('status', 'pending')
                    step = item.get('step', item.get('goal', 'unknown'))
                    icon = "✓" if status == 'completed' else "○" if status == 'pending' else "▶"
                    print(f"  {icon} {step} ({status})")
                else:
                    print(f"  - {item}")

        print(f"{CLI_GREEN}[Actions]:{CLI_CLR} {len(action_queue)} action(s), is_final={is_final}")
        
        if failure_logger:
            failure_logger.log_llm_turn(task.task_id, turn + 1, raw_content, action_queue)

        messages.append(AIMessage(content=raw_content))

        # Check explicit stop condition - agent wants to stop but may have forgotten to call respond
        if is_final and not action_queue:
            # Agent might put respond info in different places:
            # 1. Top-level "outcome" field
            # 2. Inside "args" dict (when agent puts tool/args at top level instead of action_queue)
            outcome = parsed.get("outcome")
            message = parsed.get("message") or thoughts
            
            # Check if agent put tool/args at top level (common LLM mistake)
            if not outcome and parsed.get("tool") == "respond":
                args = parsed.get("args", {})
                outcome = args.get("outcome")
                message = args.get("message") or message
            
            # Also check nested args
            if not outcome and "args" in parsed:
                outcome = parsed["args"].get("outcome")
                message = parsed["args"].get("message") or message
            
            # HEURISTIC: If agent didn't specify outcome, try to infer from thoughts/context
            if not outcome and thoughts:
                thoughts_lower = thoughts.lower()
                # Check for error indicators
                if any(word in thoughts_lower for word in ["internal error", "system error", "system failure", "technical error", "cannot retrieve", "failed to", "service error"]):
                    outcome = "error_internal"
                    print(f"{CLI_CYAN}⚠ Inferred outcome 'error_internal' from thoughts{CLI_CLR}")
                elif any(word in thoughts_lower for word in ["no permission", "denied", "not allowed", "unauthorized", "access denied"]):
                    outcome = "denied_security"
                    print(f"{CLI_CYAN}⚠ Inferred outcome 'denied_security' from thoughts{CLI_CLR}")
                elif any(word in thoughts_lower for word in ["not found", "no results", "does not exist", "couldn't find"]):
                    outcome = "ok_not_found"
                    print(f"{CLI_CYAN}⚠ Inferred outcome 'ok_not_found' from thoughts{CLI_CLR}")
            
            if outcome and not task_done:
                # Agent forgot to call respond - do it automatically!
                print(f"{CLI_CYAN}⚠ Agent set is_final=true with outcome '{outcome}' but no respond action. Auto-submitting...{CLI_CLR}")
                
                try:
                    # Build respond action
                    respond_model = client.Req_ProvideAgentResponse(
                        message=message or f"Task completed with outcome: {outcome}",
                        outcome=outcome,
                        links=parsed.get("links", [])
                    )
                    
                    # Execute respond
                    executor = get_executor(erc_client, wiki_manager, security_manager, task=task)
                    from handlers.core import ActionContext
                    ctx = ActionContext(
                        api=erc_client,
                        model=respond_model,
                        raw_action={"tool": "respond", "args": {"outcome": outcome, "message": message}},
                        shared={'security_manager': security_manager, 'wiki_manager': wiki_manager}
                    )
                    result = erc_client.dispatch(respond_model)
                    task_done = True
                    print(f"  {CLI_GREEN}✓ AUTO-SUBMITTED RESPONSE: {outcome}{CLI_CLR}")
                except Exception as e:
                    print(f"  {CLI_RED}✗ Auto-submit failed: {e}{CLI_CLR}")
            
            print(f"{CLI_GREEN}✓ Agent decided to stop.{CLI_CLR}")
            break

        # Execute Actions
        results = []
        stop_execution = False
        
        # Initialize executor with fresh context managers
        executor = get_executor(erc_client, wiki_manager, security_manager, task=task)

        for idx, action_dict in enumerate(action_queue):
            if stop_execution:
                break
            
            print(f"\n  {CLI_BLUE}▶ Parsing action {idx+1}:{CLI_CLR} {json.dumps(action_dict)}")
            
            # FIX: Pass a DummyContext with the real security_manager to parse_action
            # This allows parsing logic to inject current_user if needed
            class DummyContext:
                def __init__(self, sm):
                    self.shared = {'security_manager': sm}
            
            dummy_ctx = DummyContext(security_manager)
            action_model = parse_action(action_dict, context=dummy_ctx)
            
            if not action_model:
                results.append(f"Action {idx+1}: SKIPPED (invalid format/unknown tool)")
                continue
            
            action_name = action_model.__class__.__name__

            if stats:
                stats.add_api_call()
            
            # Execute with handler
            ctx = executor.execute(action_dict, action_model)
            results.extend(ctx.results)
            
            if ctx.stop_execution:
                stop_execution = True
            
            # Check if this was the final response
            if isinstance(action_model, client.Req_ProvideAgentResponse):
                 task_done = True
                 print(f"  {CLI_GREEN}✓ FINAL RESPONSE SUBMITTED{CLI_CLR}")
                 stop_execution = True

        # Feed back results
        if results:
            feedback = "\n---\n".join(results)
            messages.append(HumanMessage(content=f"[EXECUTION LOG]\n{feedback}"))
            
            # If wiki changed during execution, append note
            # (The middleware syncs it, but we can remind the agent)
            if wiki_manager.current_sha1:
                 # Check if we should remind? Maybe too noisy.
                 pass
        else:
             messages.append(HumanMessage(content="[SYSTEM]: No actions executed. Set is_final=true if done, otherwise add actions."))

    print(f"\n{CLI_BLUE}═══ Agent finished ═══{CLI_CLR}")
