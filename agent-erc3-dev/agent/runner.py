"""
Agent execution loop.

Main entry point for running the ERC3 agent on a task.
"""

import json
from typing import Optional

from langchain_core.messages import AIMessage

from erc3 import TaskInfo, ERC3

from llm_provider import get_llm
from stats import SessionStats, FailureLogger
from handlers import WikiManager, SecurityManager
from utils import CLI_GREEN, CLI_YELLOW, CLI_BLUE, CLI_CYAN, CLI_CLR

from .state import AgentTurnState
from .parsing import extract_json
from .loop_detection import LoopDetector
from .llm_invoker import LLMInvoker
from .message_builder import MessageBuilder
from .action_processor import ActionProcessor

import config


def run_agent(
    model_name: str,
    api: ERC3,
    task: TaskInfo,
    stats: SessionStats = None,
    pricing_model: str = None,
    max_turns: int = None,
    failure_logger: FailureLogger = None,
    wiki_manager: WikiManager = None,
    backend: str = "gonka"
):
    """
    Run the agent loop for a single task.

    Args:
        model_name: LLM model identifier
        api: ERC3 API client
        task: Task info from benchmark
        stats: Session statistics tracker
        pricing_model: Model ID for pricing (defaults to model_name)
        max_turns: Maximum turns before stopping
        failure_logger: Logger for debugging failures
        wiki_manager: Wiki manager instance (creates new if None)
        backend: LLM backend ("gonka", "openrouter", etc.)
    """
    # Set defaults
    if max_turns is None:
        max_turns = config.MAX_TURNS_PER_TASK

    # Initialize components
    llm = get_llm(model_name, backend=backend)
    erc_client = api.get_erc_dev_client(task)
    cost_model_id = pricing_model or model_name

    # Initialize managers
    if wiki_manager:
        wiki_manager.set_api(erc_client)
    else:
        wiki_manager = WikiManager(erc_client)

    security_manager = SecurityManager()
    loop_detector = LoopDetector()

    # Initialize helpers
    llm_invoker = LLMInvoker(llm, api, task, model_name, cost_model_id, stats)
    message_builder = MessageBuilder(wiki_manager)
    action_processor = ActionProcessor(
        erc_client, wiki_manager, security_manager,
        task, stats, failure_logger
    )

    # Initialize conversation
    messages = message_builder.build_initial_messages(task.task_text)

    # Initialize turn state
    state = AgentTurnState(
        security_manager=security_manager,
        task=task,
        api=erc_client,
        max_turns=max_turns,
    )

    # AICODE-NOTE: t016 FIX - Parse baseline employee name from task text for salary comparisons
    # Pattern: "salary higher than [Name]" or "salary greater than [Name]"
    import re
    salary_pattern = r'salary\s+(?:higher|greater|more)\s+than\s+([A-Z][a-zà-ÿ]+(?:\s+[A-Z][a-zà-ÿ]+)+)'
    match = re.search(salary_pattern, task.task_text, re.IGNORECASE)
    if match:
        baseline_name = match.group(1).strip()
        state.salary_comparison_baseline_name = baseline_name
        print(f"{CLI_YELLOW}[t016] Detected salary comparison baseline: {baseline_name}{CLI_CLR}")

    task_done = False
    who_am_i_called = False

    # Log task info at start
    print(f"{CLI_BLUE}=== Task: {task.task_id} ==={CLI_CLR}")
    print(f"{CLI_CYAN}[Question]:{CLI_CLR} {task.task_text}")

    # Main agent loop
    for turn in range(max_turns):
        if task_done:
            print(f"{CLI_GREEN}Task marked done. Ending agent loop.{CLI_CLR}")
            break

        # Update turn in state for budget awareness
        state.current_turn = turn

        print(f"\n{CLI_BLUE}=== Turn {turn + 1}/{max_turns} ==={CLI_CLR}")

        # Invoke LLM
        raw_content, usage = llm_invoker.invoke(messages)
        if raw_content is None:
            break

        print(f"{CLI_CYAN}[Raw Response]:{CLI_CLR}")
        print(raw_content)
        print()

        # Parse JSON response
        try:
            parsed = extract_json(raw_content)
        except json.JSONDecodeError as e:
            print(f"{CLI_YELLOW}JSON parse error: {e}{CLI_CLR}")
            messages.append(AIMessage(content=raw_content))
            messages.append(message_builder.build_json_error_message())
            continue

        # Extract parsed fields
        thoughts = parsed.get("thoughts", "")
        plan = parsed.get("plan", [])
        action_queue = parsed.get("action_queue", [])
        is_final = parsed.get("is_final", False)

        # Save thoughts for criteria guards
        state.last_thoughts = thoughts

        _print_turn_info(thoughts, plan, action_queue, is_final)

        # Validate actions
        valid_actions, malformed_count, malformed_mutation_tools = \
            action_processor.validate_actions(action_queue, state)

        if malformed_count > 0:
            messages.append(message_builder.build_malformed_actions_message(
                malformed_count, malformed_mutation_tools
            ))
            if not valid_actions:
                continue

        if failure_logger:
            failure_logger.log_llm_turn(task.task_id, turn + 1, raw_content, valid_actions)

        messages.append(AIMessage(content=raw_content))

        # Check is_final without respond
        if is_final and not valid_actions:
            print(f"{CLI_YELLOW}is_final=true but no respond tool{CLI_CLR}")
            messages.append(message_builder.build_is_final_error_message())
            continue

        # AICODE-NOTE: t012 FIX - Check for empty action_queue without is_final
        # Agent is stuck if it returns no actions but claims task is not done.
        # Force it to either take action or respond.
        # AICODE-NOTE: t077 FIX - Pass context to generate coaching-aware hints
        if not valid_actions and not is_final:
            print(f"{CLI_YELLOW}Empty action_queue without is_final - agent stuck{CLI_CLR}")
            messages.append(message_builder.build_empty_actions_message(
                task_text=task.task_text,
                current_turn=turn,
                max_turns=max_turns
            ))
            continue

        # Loop detection
        if loop_detector.record_and_check(valid_actions):
            print(f"{CLI_YELLOW}LOOP DETECTED{CLI_CLR}")
            messages.append(message_builder.build_loop_detected_message())
            loop_detector.clear()
            continue

        # Execute actions
        result = action_processor.process(valid_actions, state, who_am_i_called)

        task_done = result.task_done
        who_am_i_called = result.who_am_i_called

        # Feed back results with turn budget info
        messages.append(message_builder.build_results_message(
            result.results,
            current_turn=turn,
            max_turns=max_turns
        ))

    print(f"\n{CLI_BLUE}=== Agent finished ==={CLI_CLR}")


def _print_turn_info(thoughts: str, plan: list, action_queue: list, is_final: bool):
    """Print parsed turn information."""
    print(f"{CLI_GREEN}[Thoughts]:{CLI_CLR} {thoughts}")

    if plan:
        print(f"{CLI_GREEN}[Plan]:{CLI_CLR}")
        for item in plan:
            if isinstance(item, dict):
                status = item.get('status', 'pending')
                step = item.get('step', item.get('goal', 'unknown'))
                icon = "" if status == 'completed' else "" if status == 'pending' else ""
                print(f"  {icon} {step} ({status})")
            else:
                print(f"  - {item}")

    print(f"{CLI_GREEN}[Actions]:{CLI_CLR} {len(action_queue)} action(s), is_final={is_final}")
