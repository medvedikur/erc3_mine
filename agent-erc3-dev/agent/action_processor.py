"""
Action queue processing and execution.

Handles validation, parsing, and execution of agent actions.
"""

import json
from typing import List, Tuple, Optional, Set, Any
from dataclasses import dataclass

from pydantic import ValidationError

from erc3 import TaskInfo
from erc3.erc3 import client

from tools.parser import parse_action
from tools.registry import ParseError
from tools.patches import SafeReq_UpdateEmployeeInfo
from stats import SessionStats, FailureLogger
from handlers import get_executor, WikiManager, SecurityManager
from utils import CLI_RED, CLI_GREEN, CLI_YELLOW, CLI_BLUE, CLI_CLR

from .state import AgentTurnState


# Mutation operation types
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


@dataclass
class ActionResult:
    """Result of action queue processing."""
    results: List[str]
    task_done: bool
    who_am_i_called: bool
    had_errors: bool
    malformed_count: int
    malformed_mutation_tools: List[str]


class ActionProcessor:
    """
    Processes and executes action queues.

    Responsibilities:
    - Validate action format
    - Parse actions into request models
    - Execute actions via handler
    - Track mutations and searches
    - Handle security checks
    """

    def __init__(
        self,
        erc_client: Any,
        wiki_manager: WikiManager,
        security_manager: SecurityManager,
        task: TaskInfo,
        stats: Optional[SessionStats] = None,
        failure_logger: Optional[FailureLogger] = None,
    ):
        """
        Initialize the action processor.

        Args:
            erc_client: ERC3 dev client
            wiki_manager: Wiki manager instance
            security_manager: Security manager instance
            task: Current task info
            stats: Optional session statistics
            failure_logger: Optional failure logger
        """
        self.erc_client = erc_client
        self.wiki_manager = wiki_manager
        self.security_manager = security_manager
        self.task = task
        self.stats = stats
        self.failure_logger = failure_logger

        self.executor = get_executor(
            erc_client, wiki_manager, security_manager, task=task
        )

    def validate_actions(
        self,
        action_queue: List[dict],
        state: AgentTurnState
    ) -> Tuple[List[dict], int, List[str]]:
        """
        Validate action queue and filter malformed actions.

        Args:
            action_queue: List of action dicts from LLM
            state: Current agent turn state

        Returns:
            Tuple of (valid_actions, malformed_count, malformed_mutation_tools)
        """
        valid_actions = []
        malformed_count = 0
        malformed_mutation_tools = []

        for action in action_queue:
            if isinstance(action, dict) and "tool" in action:
                valid_actions.append(action)
            else:
                malformed_count += 1
                print(f"  {CLI_YELLOW}Malformed action skipped: {action}{CLI_CLR}")
                action_str = str(action).lower()
                for mt in MUTATION_TOOL_NAMES:
                    if mt.replace('_', '') in action_str.replace('_', ''):
                        malformed_mutation_tools.append(mt)
                        state.pending_mutation_tools.add(mt)
                        break

        if malformed_count > 0:
            print(f"{CLI_YELLOW}{malformed_count} malformed action(s){CLI_CLR}")

        return valid_actions, malformed_count, malformed_mutation_tools

    def process(
        self,
        action_queue: List[dict],
        state: AgentTurnState,
        who_am_i_called: bool
    ) -> ActionResult:
        """
        Process and execute all actions in the queue.

        Args:
            action_queue: List of validated action dicts
            state: Current agent turn state
            who_am_i_called: Whether who_am_i was called

        Returns:
            ActionResult with execution results
        """
        results = []
        stop_execution = False
        had_errors = False
        task_done = False

        for idx, action_dict in enumerate(action_queue):
            if stop_execution:
                break

            print(f"\n  {CLI_BLUE}Parsing action {idx+1}:{CLI_CLR} {json.dumps(action_dict)}")

            parse_ctx = state.create_context()

            # Parse action
            try:
                action_model = parse_action(action_dict, context=parse_ctx)
            except ValidationError as ve:
                error_msg = f"Validation error: {ve.errors()[0]['msg'] if ve.errors() else str(ve)}"
                print(f"  {CLI_RED}{error_msg}{CLI_CLR}")
                results.append(f"Action {idx+1} VALIDATION ERROR: {error_msg}")
                had_errors = True
                continue

            if isinstance(action_model, ParseError):
                error_msg = str(action_model)
                print(f"  {CLI_RED}Parse error: {error_msg}{CLI_CLR}")
                results.append(f"Action {idx+1} ERROR: {error_msg}")
                had_errors = True
                self._track_missing_tool(action_model, action_dict, state)
                continue

            if not action_model:
                results.append(f"Action {idx+1}: SKIPPED (invalid format)")
                had_errors = True
                continue

            # Track who_am_i
            if isinstance(action_model, client.Req_WhoAmI):
                who_am_i_called = True

            # Security checks for respond
            if isinstance(action_model, client.Req_ProvideAgentResponse):
                block_msg = self._check_respond_blocked(
                    action_model, who_am_i_called, had_errors, state
                )
                if block_msg:
                    print(f"  {CLI_YELLOW}BLOCKED{CLI_CLR}")
                    results.append(f"Action {idx+1} BLOCKED: {block_msg}")
                    continue

            if self.stats:
                self.stats.add_api_call(task_id=self.task.task_id)

            # Execute action
            initial_shared = state.to_shared_dict()
            initial_shared['failure_logger'] = self.failure_logger
            initial_shared['task_id'] = self.task.task_id

            if hasattr(parse_ctx, 'shared') and 'query_specificity' in parse_ctx.shared:
                initial_shared['query_specificity'] = parse_ctx.shared['query_specificity']

            ctx = self.executor.execute(action_dict, action_model, initial_shared=initial_shared)
            results.extend(ctx.results)
            state.sync_from_context(ctx)

            # Log context results
            if self.failure_logger and ctx.results:
                action_name = action_dict.get('tool', action_model.__class__.__name__)
                self.failure_logger.log_context_results(
                    self.task.task_id, action_name, ctx.results
                )

            # Track errors
            if any("FAILED" in r or "ERROR" in r for r in ctx.results):
                had_errors = True
            else:
                tool_name = action_dict.get('tool', '')
                if tool_name:
                    state.action_types_executed.add(tool_name)

            # Track mutations and searches
            self._track_mutation(action_model, state, ctx)
            self._track_search(action_model, state, ctx)

            if ctx.stop_execution:
                stop_execution = True

            # Check final response
            if isinstance(action_model, client.Req_ProvideAgentResponse):
                if ctx.stop_execution:
                    print(f"  {CLI_YELLOW}Response blocked by middleware{CLI_CLR}")
                else:
                    task_done = True
                    print(f"  {CLI_GREEN}FINAL RESPONSE SUBMITTED{CLI_CLR}")
                    stop_execution = True

        return ActionResult(
            results=results,
            task_done=task_done,
            who_am_i_called=who_am_i_called,
            had_errors=had_errors,
            malformed_count=0,
            malformed_mutation_tools=[]
        )

    def _track_missing_tool(
        self,
        action_model: ParseError,
        action_dict: dict,
        state: AgentTurnState
    ):
        """Track non-existent tools for validation."""
        error_msg = str(action_model)
        if "does not exist" in error_msg.lower() or "unknown tool" in error_msg.lower():
            tool_name = action_model.tool if hasattr(action_model, 'tool') else action_dict.get('tool', 'unknown')
            if tool_name and tool_name not in state.missing_tools:
                state.missing_tools.append(tool_name)
                print(f"  {CLI_YELLOW}Tracked missing tool: {tool_name}{CLI_CLR}")

    def _check_respond_blocked(
        self,
        action_model: client.Req_ProvideAgentResponse,
        who_am_i_called: bool,
        had_errors: bool,
        state: AgentTurnState
    ) -> Optional[str]:
        """Check if respond should be blocked."""
        if not who_am_i_called:
            return "You MUST call 'who_am_i' first to verify identity."

        if had_errors and action_model.outcome == "ok_answer":
            return "Cannot respond 'ok_answer' when previous actions FAILED."

        if state.pending_mutation_tools and action_model.outcome == "ok_answer":
            pending = ', '.join(state.pending_mutation_tools)
            return f"Pending mutations not executed: [{pending}]"

        return None

    def _track_mutation(self, action_model: Any, state: AgentTurnState, ctx: Any):
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

    def _track_search(self, action_model: Any, state: AgentTurnState, ctx: Any):
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
