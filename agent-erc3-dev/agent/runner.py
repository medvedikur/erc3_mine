"""
Agent execution loop.

Main entry point for running the ERC3 agent on a task.
"""
import json
import time
from typing import Optional

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from pydantic import ValidationError

from erc3 import TaskInfo, ERC3
from erc3.erc3 import client

from llm_provider import get_llm
from prompts import SGR_SYSTEM_PROMPT
from tools.parser import parse_action
from tools.registry import ParseError
from tools.patches import SafeReq_UpdateEmployeeInfo
from stats import SessionStats, FailureLogger
from handlers import get_executor, WikiManager, SecurityManager
from utils import CLI_RED, CLI_GREEN, CLI_YELLOW, CLI_BLUE, CLI_CYAN, CLI_CLR

from .state import AgentTurnState
from .parsing import extract_json, OpenAIUsage
from .loop_detection import LoopDetector


# Mutation operation types - these modify state and require current_user in links
MUTATION_TYPES = (
    client.Req_LogTimeEntry,
    client.Req_UpdateEmployeeInfo,
    SafeReq_UpdateEmployeeInfo,
    client.Req_UpdateProjectStatus,
    client.Req_UpdateProjectTeam,
    client.Req_UpdateWiki,
    client.Req_UpdateTimeEntry,
)

# Search types for auto-linking
SEARCH_TYPES = (
    client.Req_SearchTimeEntries,
    client.Req_TimeSummaryByEmployee,
    client.Req_TimeSummaryByProject,
)

# Tool names that are mutations
MUTATION_TOOL_NAMES = {
    'projects_update', 'projects_team_update', 'projects_status_update',
    'employees_update', 'time_log', 'time_update', 'wiki_update'
}


def run_agent(
    model_name: str,
    api: ERC3,
    task: TaskInfo,
    stats: SessionStats = None,
    pricing_model: str = None,
    max_turns: int = 70,
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
    # Initialize LLM and client
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

    # Initial messages
    messages = [
        SystemMessage(content=SGR_SYSTEM_PROMPT),
        HumanMessage(content=f"TASK: {task.task_text}\n\nContext: {wiki_manager.get_context_summary()}")
    ]

    task_done = False
    who_am_i_called = False

    # Initialize turn state
    state = AgentTurnState(
        security_manager=security_manager,
        task=task,
        api=erc_client,
    )

    for turn in range(max_turns):
        if task_done:
            print(f"{CLI_GREEN}✓ Task marked done. Ending agent loop.{CLI_CLR}")
            break

        print(f"\n{CLI_BLUE}═══ Turn {turn + 1}/{max_turns} ═══{CLI_CLR}")

        # Invoke LLM
        raw_content, usage = _invoke_llm(llm, messages, api, task, model_name, cost_model_id, stats)
        if raw_content is None:
            break

        print(f"{CLI_CYAN}[Raw Response]:{CLI_CLR}")
        print(raw_content)
        print()

        # Parse JSON
        try:
            parsed = extract_json(raw_content)
        except json.JSONDecodeError as e:
            print(f"{CLI_RED}✗ JSON parse error: {e}{CLI_CLR}")
            print(f"{CLI_YELLOW}⚠ JSON parse failed - asking model to retry{CLI_CLR}")
            messages.append(AIMessage(content=raw_content))
            messages.append(HumanMessage(content=_JSON_ERROR_MSG))
            continue

        # Extract parsed fields
        thoughts = parsed.get("thoughts", "")
        plan = parsed.get("plan", [])
        action_queue = parsed.get("action_queue", [])
        is_final = parsed.get("is_final", False)

        _print_turn_info(thoughts, plan, action_queue, is_final)

        # Validate actions
        action_queue, malformed_msg = _validate_actions(action_queue, state)
        if malformed_msg:
            messages.append(HumanMessage(content=malformed_msg))
            if not action_queue:
                continue

        if failure_logger:
            failure_logger.log_llm_turn(task.task_id, turn + 1, raw_content, action_queue)

        messages.append(AIMessage(content=raw_content))

        # Check is_final without respond
        if is_final and not action_queue:
            print(f"{CLI_YELLOW}⚠ is_final=true but no respond tool{CLI_CLR}")
            messages.append(HumanMessage(content=_IS_FINAL_NO_RESPOND_MSG))
            continue

        # Loop detection
        if loop_detector.record_and_check(action_queue):
            print(f"{CLI_YELLOW}⚠ LOOP DETECTED{CLI_CLR}")
            messages.append(HumanMessage(content=_LOOP_DETECTED_MSG))
            loop_detector.clear()
            continue

        # Execute actions
        results, task_done, who_am_i_called = _execute_actions(
            action_queue, state, erc_client, wiki_manager, security_manager,
            task, stats, failure_logger, who_am_i_called
        )

        # Feed back results
        if results:
            feedback = "\n---\n".join(results)
            messages.append(HumanMessage(content=f"[EXECUTION LOG]\n{feedback}"))
        else:
            messages.append(HumanMessage(content=_NO_ACTIONS_MSG))

    print(f"\n{CLI_BLUE}═══ Agent finished ═══{CLI_CLR}")


def _invoke_llm(llm, messages, api, task, model_name, cost_model_id, stats):
    """Invoke LLM and handle response/usage tracking."""
    started = time.time()
    try:
        result = llm.generate([messages])
        generation = result.generations[0][0]
        llm_output = result.llm_output or {}

        raw_content = generation.text
        usage = llm_output.get("token_usage", {})

        # Fallback if usage is missing
        if not usage or usage.get("completion_tokens", 0) == 0:
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
            stats.add_llm_usage(cost_model_id, usage_obj, task_id=task.task_id)

        api.log_llm(
            task_id=task.task_id,
            completion=raw_content,
            model=model_name,
            duration_sec=time.time() - started,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            cached_prompt_tokens=0
        )

        return raw_content, usage

    except Exception as e:
        print(f"{CLI_RED}✗ LLM call failed: {e}{CLI_CLR}")
        return None, None


def _print_turn_info(thoughts, plan, action_queue, is_final):
    """Print parsed turn information."""
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


def _validate_actions(action_queue, state):
    """Validate action queue and filter malformed actions."""
    valid_actions = []
    malformed_count = 0
    malformed_mutation_tools = []

    for action in action_queue:
        if isinstance(action, dict) and "tool" in action:
            valid_actions.append(action)
        else:
            malformed_count += 1
            print(f"  {CLI_YELLOW}⚠ Malformed action skipped: {action}{CLI_CLR}")
            action_str = str(action).lower()
            for mt in MUTATION_TOOL_NAMES:
                if mt.replace('_', '') in action_str.replace('_', ''):
                    malformed_mutation_tools.append(mt)
                    state.pending_mutation_tools.add(mt)
                    break

    if malformed_count > 0:
        print(f"{CLI_YELLOW}⚠ {malformed_count} malformed action(s){CLI_CLR}")
        mutation_warning = ""
        if malformed_mutation_tools:
            mutation_warning = f"\n\n⚠️ CRITICAL: Malformed mutation(s): {', '.join(malformed_mutation_tools)}. NOT executed!"
        msg = f"""[SYSTEM ERROR]: {malformed_count} action(s) were malformed.

Each action MUST have: {{"tool": "tool_name", "args": {{...}}}}{mutation_warning}

The malformed actions were NOT executed. Please retry."""
        return valid_actions, msg

    return valid_actions, None


def _execute_actions(action_queue, state, erc_client, wiki_manager, security_manager,
                     task, stats, failure_logger, who_am_i_called):
    """Execute all actions in the queue."""
    results = []
    stop_execution = False
    had_errors = False
    task_done = False

    executor = get_executor(erc_client, wiki_manager, security_manager, task=task)

    for idx, action_dict in enumerate(action_queue):
        if stop_execution:
            break

        print(f"\n  {CLI_BLUE}▶ Parsing action {idx+1}:{CLI_CLR} {json.dumps(action_dict)}")

        parse_ctx = state.create_context()

        # Parse action
        try:
            action_model = parse_action(action_dict, context=parse_ctx)
        except ValidationError as ve:
            error_msg = f"Validation error: {ve.errors()[0]['msg'] if ve.errors() else str(ve)}"
            print(f"  {CLI_RED}✗ {error_msg}{CLI_CLR}")
            results.append(f"Action {idx+1} VALIDATION ERROR: {error_msg}")
            had_errors = True
            continue

        if isinstance(action_model, ParseError):
            error_msg = str(action_model)
            print(f"  {CLI_RED}✗ Parse error: {error_msg}{CLI_CLR}")
            results.append(f"Action {idx+1} ERROR: {error_msg}")
            had_errors = True
            _track_missing_tool(action_model, action_dict, state)
            continue

        if not action_model:
            results.append(f"Action {idx+1}: SKIPPED (invalid format)")
            had_errors = True
            continue

        # Security checks
        if isinstance(action_model, client.Req_WhoAmI):
            who_am_i_called = True

        if isinstance(action_model, client.Req_ProvideAgentResponse):
            block_msg = _check_respond_blocked(action_model, who_am_i_called, had_errors, state)
            if block_msg:
                print(f"  {CLI_YELLOW}⚠ BLOCKED{CLI_CLR}")
                results.append(f"Action {idx+1} BLOCKED: {block_msg}")
                continue

        if stats:
            stats.add_api_call(task_id=task.task_id)

        # Execute
        initial_shared = state.to_shared_dict()
        initial_shared['failure_logger'] = failure_logger
        initial_shared['task_id'] = task.task_id

        if hasattr(parse_ctx, 'shared') and 'query_specificity' in parse_ctx.shared:
            initial_shared['query_specificity'] = parse_ctx.shared['query_specificity']

        ctx = executor.execute(action_dict, action_model, initial_shared=initial_shared)
        results.extend(ctx.results)
        state.sync_from_context(ctx)

        # Track errors
        if any("FAILED" in r or "ERROR" in r for r in ctx.results):
            had_errors = True
        else:
            tool_name = action_dict.get('tool', '')
            if tool_name:
                state.action_types_executed.add(tool_name)

        # Track mutations
        _track_mutation(action_model, state, ctx)
        _track_search(action_model, state, ctx)

        if ctx.stop_execution:
            stop_execution = True

        # Check final response
        if isinstance(action_model, client.Req_ProvideAgentResponse):
            if ctx.stop_execution:
                print(f"  {CLI_YELLOW}⚠ Response blocked by middleware{CLI_CLR}")
            else:
                task_done = True
                print(f"  {CLI_GREEN}✓ FINAL RESPONSE SUBMITTED{CLI_CLR}")
                stop_execution = True

    return results, task_done, who_am_i_called


def _track_missing_tool(action_model, action_dict, state):
    """Track non-existent tools for validation."""
    error_msg = str(action_model)
    if "does not exist" in error_msg.lower() or "unknown tool" in error_msg.lower():
        tool_name = action_model.tool if hasattr(action_model, 'tool') else action_dict.get('tool', 'unknown')
        if tool_name and tool_name not in state.missing_tools:
            state.missing_tools.append(tool_name)
            print(f"  {CLI_YELLOW}📝 Tracked missing tool: {tool_name}{CLI_CLR}")


def _check_respond_blocked(action_model, who_am_i_called, had_errors, state):
    """Check if respond should be blocked."""
    if not who_am_i_called:
        return "You MUST call 'who_am_i' first to verify identity."

    if had_errors and action_model.outcome == "ok_answer":
        return "Cannot respond 'ok_answer' when previous actions FAILED."

    if state.pending_mutation_tools and action_model.outcome == "ok_answer":
        pending = ', '.join(state.pending_mutation_tools)
        return f"Pending mutations not executed: [{pending}]"

    return None


def _track_mutation(action_model, state, ctx):
    """Track successful mutation operations."""
    if not isinstance(action_model, MUTATION_TYPES):
        return
    if any("FAILED" in r or "ERROR" in r for r in ctx.results):
        return

    state.had_mutations = True

    # Clear from pending
    mutation_map = {
        client.Req_LogTimeEntry: ['time_log'],
        client.Req_UpdateEmployeeInfo: ['employees_update'],
        client.Req_UpdateProjectStatus: ['projects_status_update', 'projects_update'],
        client.Req_UpdateProjectTeam: ['projects_team_update', 'projects_update'],
        client.Req_UpdateTimeEntry: ['time_update'],
        client.Req_UpdateWiki: ['wiki_update'],
    }

    for req_type, tool_names in mutation_map.items():
        if isinstance(action_model, req_type):
            for name in tool_names:
                state.pending_mutation_tools.discard(name)
            break

    # Extract entity IDs for auto-linking
    if isinstance(action_model, client.Req_LogTimeEntry):
        if action_model.project:
            state.mutation_entities.append({"id": action_model.project, "kind": "project"})
        if action_model.employee:
            state.mutation_entities.append({"id": action_model.employee, "kind": "employee"})
        if action_model.logged_by and action_model.logged_by != action_model.employee:
            state.mutation_entities.append({"id": action_model.logged_by, "kind": "employee"})

    elif isinstance(action_model, client.Req_UpdateEmployeeInfo):
        if action_model.employee:
            state.mutation_entities.append({"id": action_model.employee, "kind": "employee"})

    elif isinstance(action_model, client.Req_UpdateProjectStatus):
        if hasattr(action_model, 'id') and action_model.id:
            state.mutation_entities.append({"id": action_model.id, "kind": "project"})

    elif isinstance(action_model, client.Req_UpdateProjectTeam):
        if hasattr(action_model, 'id') and action_model.id:
            state.mutation_entities.append({"id": action_model.id, "kind": "project"})
        if hasattr(action_model, 'team') and action_model.team:
            for member in action_model.team:
                emp_id = member.get('employee') if isinstance(member, dict) else getattr(member, 'employee', None)
                if emp_id:
                    state.mutation_entities.append({"id": emp_id, "kind": "employee"})

    elif isinstance(action_model, client.Req_UpdateTimeEntry):
        time_update_entities = ctx.shared.get('time_update_entities', [])
        for entity in time_update_entities:
            state.mutation_entities.append(entity)


def _track_search(action_model, state, ctx):
    """Track search entities for auto-linking."""
    if not isinstance(action_model, SEARCH_TYPES):
        return
    if any("FAILED" in r or "ERROR" in r for r in ctx.results):
        return

    if isinstance(action_model, client.Req_SearchTimeEntries):
        if action_model.employee:
            state.search_entities.append({"id": action_model.employee, "kind": "employee"})
        if action_model.project:
            state.search_entities.append({"id": action_model.project, "kind": "project"})

    elif isinstance(action_model, client.Req_TimeSummaryByEmployee):
        employees = getattr(action_model, 'employees', None) or []
        for emp in employees:
            state.search_entities.append({"id": emp, "kind": "employee"})

    elif isinstance(action_model, client.Req_TimeSummaryByProject):
        projects = getattr(action_model, 'projects', None) or []
        for proj in projects:
            state.search_entities.append({"id": proj, "kind": "project"})


# Error message templates
_JSON_ERROR_MSG = "[SYSTEM ERROR]: Invalid JSON. Respond with ONLY valid JSON: {\"thoughts\": \"...\", \"plan\": [...], \"action_queue\": [...], \"is_final\": false}"

_IS_FINAL_NO_RESPOND_MSG = """[SYSTEM ERROR]: You set is_final=true but didn't call 'respond' tool!

Add respond to action_queue:
{
  "action_queue": [{"tool": "respond", "args": {"outcome": "...", "message": "...", "links": [...]}}],
  "is_final": false
}"""

_LOOP_DETECTED_MSG = """[SYSTEM ERROR]: Loop detected - same actions for 3 turns!

This usually means:
1. Feature doesn't exist → respond 'none_unsupported'
2. Missing info → respond 'none_clarification_needed'
3. Permissions issue → respond 'denied_security'

STOP repeating and call 'respond' with appropriate outcome."""

_NO_ACTIONS_MSG = """[SYSTEM ERROR]: ⚠️ NO ACTIONS EXECUTED!

Your action_queue may have had:
- Malformed JSON
- Unknown tool names
- Missing required fields

Please retry with correct syntax."""
