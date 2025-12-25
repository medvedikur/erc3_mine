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
# AICODE-NOTE: Only time-related searches should auto-add to links because they
# filter by specific entity IDs. GET/Search operations should NOT auto-add —
# the agent must explicitly mention entity IDs in the response message for them
# to be linked (via extract_from_message in response.py).
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
                # AICODE-NOTE: Validate args type early to give better feedback
                # LLM sometimes generates {"tool": "X", "args": "string"} instead of {"args": {...}}
                args = action.get("args")
                if args is not None and not isinstance(args, (dict, str)):
                    malformed_count += 1
                    print(f"  {CLI_YELLOW}Malformed args type skipped: {type(args).__name__} in {action}{CLI_CLR}")
                    continue
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

    def _merge_employee_updates(self, action_queue: List[dict]) -> List[dict]:
        """
        Merge consecutive employees_update calls for the same employee.

        AICODE-NOTE: t048 FIX - Benchmark expects exactly ONE employees_update event
        per employee. When agent sends skills and wills as separate calls,
        merge them into a single call to avoid duplicate events.

        Example:
            [{'tool': 'employees_update', 'args': {'employee': 'X', 'skills': [...]}},
             {'tool': 'employees_update', 'args': {'employee': 'X', 'wills': [...]}}]
        Becomes:
            [{'tool': 'employees_update', 'args': {'employee': 'X', 'skills': [...], 'wills': [...]}}]
        """
        if not action_queue:
            return action_queue

        result = []
        # Group consecutive employees_update by employee_id
        pending_update = None
        pending_employee = None

        for action in action_queue:
            tool = action.get('tool', '')
            args = action.get('args', {})

            if tool == 'employees_update' and isinstance(args, dict):
                emp_id = args.get('employee') or args.get('id')

                if pending_update and pending_employee == emp_id:
                    # Same employee - merge args
                    for key, value in args.items():
                        if key not in ('employee', 'id') and value is not None:
                            pending_update['args'][key] = value
                    print(f"  {CLI_YELLOW}Merged employees_update for {emp_id}{CLI_CLR}")
                else:
                    # Different employee or first update
                    if pending_update:
                        result.append(pending_update)
                    pending_update = {'tool': 'employees_update', 'args': dict(args)}
                    pending_employee = emp_id
            else:
                # Non-employees_update action - flush pending and add
                if pending_update:
                    result.append(pending_update)
                    pending_update = None
                    pending_employee = None
                result.append(action)

        # Flush remaining pending
        if pending_update:
            result.append(pending_update)

        return result

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
        # AICODE-NOTE: t048 FIX - Merge consecutive employees_update for same employee
        action_queue = self._merge_employee_updates(action_queue)

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

            # AICODE-NOTE: Warn if doing wiki/search operations BEFORE who_am_i
            # This helps catch cases where guest users read internal data before
            # identity is verified (e.g., t061 failure pattern)
            if not who_am_i_called and not isinstance(action_model, client.Req_WhoAmI):
                if isinstance(action_model, (client.Req_SearchWiki, client.Req_LoadWiki,
                                            client.Req_SearchEmployees, client.Req_GetEmployee,
                                            client.Req_SearchProjects, client.Req_GetProject,
                                            client.Req_SearchCustomers, client.Req_GetCustomer)):
                    results.append(
                        "\n⚠️ IDENTITY NOT VERIFIED: You are executing actions BEFORE calling `who_am_i`!\n"
                        "**CRITICAL**: You may be a GUEST user with NO ACCESS to internal data!\n"
                        "Call `who_am_i` FIRST to verify your identity before proceeding.\n"
                        "If you are a guest (is_public: true), you must return `denied_security` for internal data."
                    )

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

            # AICODE-NOTE: t075/t076 fix - Increment action_counts BEFORE creating shared dict
            # so efficiency hints can see the correct count for pagination warnings.
            tool_name_for_count = action_dict.get('tool', '')
            if tool_name_for_count:
                state.action_counts[tool_name_for_count] = state.action_counts.get(tool_name_for_count, 0) + 1

            # Execute action
            initial_shared = state.to_shared_dict()
            initial_shared['failure_logger'] = self.failure_logger
            initial_shared['task_id'] = self.task.task_id
            # AICODE-NOTE: t094 FIX - Add task_text for guards that need to check task patterns
            initial_shared['task_text'] = self.task.task_text if hasattr(self.task, 'task_text') else ''
            # AICODE-NOTE: t069 FIX - Pass state reference so guards can read live mutation state
            # Guards need to see mutations from CURRENT turn, not snapshot from start of queue
            initial_shared['_state_ref'] = state

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
                    # AICODE-NOTE: action_counts already incremented before execution (t075/t076 fix)

            # Track mutations, searches, and fetched entities
            self._track_mutation(action_model, state, ctx)
            self._track_search(action_model, state, ctx)
            self._track_fetched(action_model, state, ctx)  # t003 fix

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

        # AICODE-NOTE: Generate summary for batch member-based project searches.
        # When agent does multiple projects_search(member=X), show aggregated mapping
        # to prevent LLM confusion about which employee has which projects.
        if state.member_projects_batch and len(state.member_projects_batch) >= 3:
            summary_lines = ["📊 **MEMBER-PROJECTS SUMMARY** (from this batch):"]
            for emp_id, proj_ids in sorted(state.member_projects_batch.items()):
                if proj_ids:
                    summary_lines.append(f"  • {emp_id}: {len(proj_ids)} project(s) — {', '.join(proj_ids)}")
                else:
                    summary_lines.append(f"  • {emp_id}: 0 projects")
            summary_lines.append("\n⚠️ Use this mapping when calculating workloads. Don't confuse employee IDs!")
            results.append("\n".join(summary_lines))
            state.member_projects_batch.clear()

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

            # AICODE-NOTE: Use team_modified_employees from ProjectTeamUpdateStrategy
            # to only include employees whose time_slice/role actually changed,
            # not all team members (t097 fix - "extra link" problem)
            modified_employees = ctx.shared.get('team_modified_employees', [])
            if modified_employees:
                for emp_id in modified_employees:
                    state.mutation_entities.append({"id": emp_id, "kind": "employee"})
            elif hasattr(action_model, 'team') and action_model.team:
                # Fallback: if strategy didn't provide diff, add all (backward compat)
                for member in action_model.team:
                    emp_id = member.get('employee') if isinstance(member, dict) else getattr(member, 'employee', None)
                    if emp_id:
                        state.mutation_entities.append({"id": emp_id, "kind": "employee"})

        elif isinstance(action_model, client.Req_UpdateTimeEntry):
            time_update_entities = ctx.shared.get('time_update_entities', [])
            for entity in time_update_entities:
                state.mutation_entities.append(entity)

        elif isinstance(action_model, client.Req_UpdateWiki):
            # AICODE-NOTE: t067 fix. Track wiki file for auto-linking.
            # For rename operations (create new + delete old), only the NEW file
            # with non-empty content should be linked.
            if hasattr(action_model, 'file') and action_model.file:
                content = getattr(action_model, 'content', '')
                print(f"  [DEBUG mutation] wiki file={action_model.file}, content={repr(content)[:50]}, has_content={bool(content and content.strip())}")
                if content and content.strip():
                    # Non-empty content = creation/update → add to mutation_entities
                    state.mutation_entities.append({"id": action_model.file, "kind": "wiki"})
                    print(f"  [DEBUG mutation] Added wiki mutation, total={len(state.mutation_entities)}")
                else:
                    # Empty content = deletion → track for exclusion from links
                    # AICODE-NOTE: Write directly to state, not ctx.shared, because
                    # sync_from_context was already called before _track_mutation
                    state.deleted_wiki_files.add(action_model.file)

    def _track_search(self, action_model: Any, state: AgentTurnState, ctx: Any):
        """Track search entities for auto-linking."""
        # AICODE-NOTE: Track employees_search queries for name resolution guard (t007, t008)
        # This must be done BEFORE the SEARCH_TYPES check, since Req_SearchEmployees
        # is not in SEARCH_TYPES (it doesn't auto-add to links).
        if isinstance(action_model, client.Req_SearchEmployees):
            if not any("FAILED" in r or "ERROR" in r for r in ctx.results):
                query = getattr(action_model, 'query', None)
                if query and query not in state.employees_search_queries:
                    state.employees_search_queries.append(query)

        if not isinstance(action_model, SEARCH_TYPES):
            return
        if any("FAILED" in r or "ERROR" in r for r in ctx.results):
            return

        # Time-related searches
        if isinstance(action_model, client.Req_SearchTimeEntries):
            if action_model.employee:
                state.search_entities.append({"id": action_model.employee, "kind": "employee"})
            if action_model.project:
                state.search_entities.append({"id": action_model.project, "kind": "project"})

            # AICODE-NOTE: Extract customer from time entries response (t098 fix)
            # The customer is in the response entries, not in the request params
            api_result = ctx.shared.get('_last_api_result')
            if api_result and hasattr(api_result, 'entries') and api_result.entries:
                customers_seen = set()
                for entry in api_result.entries:
                    cust = getattr(entry, 'customer', None)
                    if cust and cust not in customers_seen:
                        customers_seen.add(cust)
                        state.search_entities.append({"id": cust, "kind": "customer"})

        elif isinstance(action_model, client.Req_TimeSummaryByEmployee):
            employees = getattr(action_model, 'employees', None) or []
            for emp in employees:
                state.search_entities.append({"id": emp, "kind": "employee"})

        elif isinstance(action_model, client.Req_TimeSummaryByProject):
            projects = getattr(action_model, 'projects', None) or []
            for proj in projects:
                state.search_entities.append({"id": proj, "kind": "project"})

        # AICODE-NOTE: Extract customer from projects_search response (t098 fix)
        # Project responses contain customer field which should be linked
        elif isinstance(action_model, client.Req_SearchProjects):
            api_result = ctx.shared.get('_last_api_result')
            if api_result and hasattr(api_result, 'projects') and api_result.projects:
                customers_seen = set()
                for proj in api_result.projects:
                    # Extract project ID
                    proj_id = getattr(proj, 'id', None)
                    if proj_id:
                        state.search_entities.append({"id": proj_id, "kind": "project"})
                        # AICODE-NOTE: t016 FIX - Track projects found via search
                        state.found_projects_search.add(proj_id)
                    # Extract customer from project
                    cust = getattr(proj, 'customer', None)
                    if cust and cust not in customers_seen:
                        customers_seen.add(cust)
                        state.search_entities.append({"id": cust, "kind": "customer"})

    def _track_fetched(self, action_model: Any, state: AgentTurnState, ctx: Any):
        """
        Track explicitly fetched entities via GET calls (t003 fix).
        These are auto-linked for ok_answer even if not mentioned in message text.
        """
        if any("FAILED" in r or "ERROR" in r for r in ctx.results):
            return

        # AICODE-NOTE: t003 FIX - Track employees_get targets.
        # When agent explicitly fetches an employee, they're likely the answer
        # (e.g., "who is the lead" → fetches employee → should link even if
        # response just says "from Sales dept" without mentioning ID).
        if isinstance(action_model, client.Req_GetEmployee):
            emp_id = getattr(action_model, 'id', None)
            if emp_id:
                # Only add if not already in list
                if not any(e.get('id') == emp_id and e.get('kind') == 'employee'
                           for e in state.fetched_entities):
                    state.fetched_entities.append({"id": emp_id, "kind": "employee"})

            # AICODE-NOTE: t016 FIX - Track employee salaries for lead salary comparison guard
            api_result = ctx.shared.get('_last_api_result')
            if api_result and hasattr(api_result, 'employee') and api_result.employee:
                employee = api_result.employee
                salary = getattr(employee, 'salary', None)
                emp_name = getattr(employee, 'name', None)
                if emp_id and salary is not None:
                    state.fetched_employee_salaries[emp_id] = salary

                # AICODE-NOTE: t016 FIX - Check if this is the baseline employee for salary comparison
                # Compare by name (case-insensitive, normalized) to handle agent fetching right person
                if state.salary_comparison_baseline_name and emp_name:
                    baseline_norm = state.salary_comparison_baseline_name.lower().strip()
                    emp_norm = emp_name.lower().strip()
                    # Exact match or close match (handling Unicode variations)
                    if baseline_norm == emp_norm or baseline_norm in emp_norm or emp_norm in baseline_norm:
                        state.salary_comparison_baseline_id = emp_id
                        state.salary_comparison_baseline_salary = salary
                        print(f"  {CLI_YELLOW}[t016] Baseline employee identified: {emp_name} ({emp_id}) = {salary}{CLI_CLR}")

        # Track projects_get
        elif isinstance(action_model, client.Req_GetProject):
            proj_id = getattr(action_model, 'id', None)
            if proj_id:
                if not any(e.get('id') == proj_id and e.get('kind') == 'project'
                           for e in state.fetched_entities):
                    state.fetched_entities.append({"id": proj_id, "kind": "project"})
                # AICODE-NOTE: t016 FIX - Track projects processed via GET
                state.processed_projects_get.add(proj_id)

            # AICODE-NOTE: t069 FIX - Extract leads from project team for wiki creation validation
            # AICODE-NOTE: t016 FIX - Also track leads of ACTIVE projects separately
            api_result = ctx.shared.get('_last_api_result')
            if api_result and hasattr(api_result, 'project') and api_result.project:
                project = api_result.project
                project_status = getattr(project, 'status', None)
                is_active = (project_status == 'active')
                team = getattr(project, 'team', None) or []
                for member in team:
                    role = getattr(member, 'role', None)
                    emp_id = getattr(member, 'employee', None)
                    if role == 'Lead' and emp_id:
                        state.found_project_leads.add(emp_id)
                        # AICODE-NOTE: t016 FIX - Only add to active_project_leads if project is active
                        if is_active:
                            state.active_project_leads.add(emp_id)
                            print(f"  {CLI_GREEN}[t016] Active project lead: {emp_id} (project status={project_status}){CLI_CLR}")

        # Track customers_get
        elif isinstance(action_model, client.Req_GetCustomer):
            cust_id = getattr(action_model, 'id', None)
            if cust_id:
                if not any(e.get('id') == cust_id and e.get('kind') == 'customer'
                           for e in state.fetched_entities):
                    state.fetched_entities.append({"id": cust_id, "kind": "customer"})
