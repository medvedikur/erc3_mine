"""
Main action pipeline orchestrator.

Coordinates preprocessors, executor, postprocessors, and enrichers.
"""

from typing import Any, List, TYPE_CHECKING

from erc3.erc3 import client

from .base import Preprocessor, PostProcessor
from .preprocessors import EmployeeUpdatePreprocessor
from .postprocessors import (
    IdentityPostProcessor,
    WikiSyncPostProcessor,
    MergerPolicyPostProcessor,
    BonusHintPostProcessor,
    SecurityRedactionPostProcessor,
)
from .executor import PipelineExecutor
from .error_handler import ErrorHandler, SuccessLogger
from ..enrichers import (
    ProjectSearchEnricher, WikiHintEnricher, EfficiencyHintEnricher,
    RoleEnricher, ArchiveHintEnricher, TimeEntryHintEnricher,
    CustomerSearchHintEnricher, EmployeeSearchHintEnricher, PaginationHintEnricher,
    CustomerProjectsHintEnricher, SearchResultExtractionHintEnricher,
    ProjectNameNormalizationHintEnricher, WorkloadHintEnricher,
    SkillSearchStrategyHintEnricher, EmployeeNameResolutionHintEnricher,
    SkillComparisonHintEnricher, QuerySubjectHintEnricher, TieBreakerHintEnricher,
    RecommendationQueryHintEnricher, TimeSummaryFallbackHintEnricher,
    ProjectTeamNameResolutionHintEnricher,
)
from utils import CLI_BLUE, CLI_GREEN, CLI_YELLOW, CLI_CLR

if TYPE_CHECKING:
    from ..base import ToolContext


class ActionPipeline:
    """
    Orchestrates action processing through pipeline stages.

    Pipeline flow:
    1. Preprocessors: Normalize/validate request
    2. Executor: Execute API call
    3. PostProcessors: Handle side effects (identity, wiki, security)
    4. Enrichers: Add context-aware hints

    This replaces the monolithic DefaultActionHandler with composable stages.
    """

    def __init__(self):
        # Preprocessors (order matters)
        self._preprocessors: List[Preprocessor] = [
            EmployeeUpdatePreprocessor(),
        ]

        # Executor
        self._executor = PipelineExecutor()

        # PostProcessors (order matters - security redaction LAST)
        self._postprocessors: List[PostProcessor] = [
            IdentityPostProcessor(),
            WikiSyncPostProcessor(),
            MergerPolicyPostProcessor(),
            BonusHintPostProcessor(),
            SecurityRedactionPostProcessor(),  # Must be last
        ]

        # Enrichers
        self._project_search = ProjectSearchEnricher()
        self._wiki_hints = WikiHintEnricher()
        self._role_enricher = RoleEnricher()
        self._archive_hints = ArchiveHintEnricher()
        self._time_entry_hints = TimeEntryHintEnricher()
        self._customer_hints = CustomerSearchHintEnricher()
        self._employee_hints = EmployeeSearchHintEnricher()
        self._pagination_hints = PaginationHintEnricher()
        self._efficiency_hints = EfficiencyHintEnricher()
        self._customer_projects_hints = CustomerProjectsHintEnricher()
        self._id_extraction_hints = SearchResultExtractionHintEnricher()
        self._project_name_hints = ProjectNameNormalizationHintEnricher()
        self._workload_hints = WorkloadHintEnricher()
        self._skill_strategy_hints = SkillSearchStrategyHintEnricher()
        self._name_resolution_hints = EmployeeNameResolutionHintEnricher()
        self._skill_comparison_hints = SkillComparisonHintEnricher()
        self._query_subject_hints = QuerySubjectHintEnricher()
        self._tie_breaker_hints = TieBreakerHintEnricher()
        self._recommendation_hints = RecommendationQueryHintEnricher()
        self._time_summary_fallback_hints = TimeSummaryFallbackHintEnricher()
        self._project_team_name_hints = ProjectTeamNameResolutionHintEnricher()

        # Error/Success handling
        self._error_handler = ErrorHandler()
        self._success_logger = SuccessLogger()

    def can_handle(self, ctx: 'ToolContext') -> bool:
        """Pipeline can handle any action (default handler)."""
        return True

    def handle(self, ctx: 'ToolContext') -> None:
        """
        Process action through the pipeline.

        Args:
            ctx: Tool context with model, api, shared state, results
        """
        action_name = ctx.model.__class__.__name__
        print(f"  {CLI_BLUE}> Executing:{CLI_CLR} {action_name}")

        # 1. Run preprocessors
        self._run_preprocessors(ctx)

        # 2. Execute action
        exec_result = self._executor.execute(ctx)

        # 3. Handle error or success
        if not exec_result.success:
            self._error_handler.handle(ctx, action_name, exec_result)
            return

        result = exec_result.result
        print(f"  {CLI_GREEN}OK{CLI_CLR}")

        # AICODE-NOTE: Store last API result for entity extraction in action_processor
        ctx.shared['_last_api_result'] = result

        # AICODE-NOTE: Track pending pagination for LIST query guard (t016, t086)
        # If API returns next_offset > 0, agent should continue paginating
        next_offset = getattr(result, 'next_offset', -1)
        if next_offset > 0:
            pending = ctx.shared.get('pending_pagination', {})
            action_name = type(ctx.model).__name__
            pending[action_name] = {
                'next_offset': next_offset,
                'current_count': len(getattr(result, 'employees', []) or
                                    getattr(result, 'projects', []) or
                                    getattr(result, 'customers', []) or [])
            }
            ctx.shared['pending_pagination'] = pending
        elif next_offset == -1:
            # Pagination complete for this action type - clear it
            pending = ctx.shared.get('pending_pagination', {})
            action_name = type(ctx.model).__name__
            if action_name in pending:
                del pending[action_name]
                ctx.shared['pending_pagination'] = pending

        # 4. Run postprocessors
        result = self._run_postprocessors(ctx, result)

        # 5. Log success
        self._success_logger.log(ctx, action_name, result)

        # 6. Run enrichers
        self._run_enrichers(ctx, result)

        # 7. Add final result to context
        result_json = result.model_dump_json(exclude_none=True)
        ctx.results.append(f"Action ({action_name}): SUCCESS\nResult: {result_json}")

    def _run_preprocessors(self, ctx: 'ToolContext') -> None:
        """Run all applicable preprocessors."""
        for preprocessor in self._preprocessors:
            if preprocessor.can_process(ctx):
                preprocessor.process(ctx)

    def _run_postprocessors(self, ctx: 'ToolContext', result: Any) -> Any:
        """Run all applicable postprocessors, returning modified result."""
        for postprocessor in self._postprocessors:
            if postprocessor.can_process(ctx, result):
                result = postprocessor.process(ctx, result)
        return result

    def _run_enrichers(self, ctx: 'ToolContext', result: Any) -> None:
        """Run all applicable enrichers."""
        task = ctx.shared.get('task')
        task_text = getattr(task, 'task_text', '') if task else ''
        security_manager = ctx.shared.get('security_manager')
        current_user = getattr(security_manager, 'current_user', None) if security_manager else None

        # Role hints for project responses
        if isinstance(ctx.model, (client.Req_SearchProjects, client.Req_GetProject)):
            if current_user:
                hint = self._role_enricher.enrich_projects_with_user_role(result, current_user)
                if hint:
                    ctx.results.append(hint)

        # Debug: Print project search results
        if isinstance(ctx.model, client.Req_SearchProjects):
            result_json = result.model_dump_json(exclude_none=True)
            print(f"  {CLI_YELLOW}PROJECTS API Response:{CLI_CLR}")
            print(f"     {result_json}")

            # AICODE-NOTE: Aggregate member-based project searches for clear mapping.
            # When agent does batch projects_search(member=X), we track results
            # to show a summary at the end, preventing LLM confusion about which
            # employee has which projects.
            member_filter = getattr(ctx.model, 'member', None)
            if member_filter:
                batch = ctx.shared.get('member_projects_batch', {})
                project_ids = []
                if hasattr(result, 'projects') and result.projects:
                    project_ids = [p.id for p in result.projects]
                batch[member_filter] = project_ids
                ctx.shared['member_projects_batch'] = batch

        # Archived project hints
        hint = self._archive_hints.maybe_hint_archived_logging(ctx.model, result, task_text)
        if hint:
            ctx.results.append(hint)

        # Pagination hints (pass model, task_text, and ctx for context-specific hints)
        # AICODE-NOTE: t075 fix - pass ctx for turn budget awareness
        hint = self._pagination_hints.maybe_hint_pagination(result, ctx.model, task_text, ctx)
        if hint:
            ctx.results.append(hint)

        # Customer search hints
        hint = self._customer_hints.maybe_hint_empty_customers(ctx.model, result)
        if hint:
            ctx.results.append(hint)

        # Employee search hints
        hint = self._employee_hints.maybe_hint_empty_employees(ctx.model, result)
        if hint:
            ctx.results.append(hint)

        # Employee name mismatch hints (t087) - when search returns wrong person
        hint = self._employee_hints.maybe_hint_wrong_name_match(ctx.model, result)
        if hint:
            ctx.results.append(hint)

        # Customer contact search hint (t087) - when looking for contact email
        hint = self._employee_hints.maybe_hint_customer_contact_search(ctx.model, result, task_text)
        if hint:
            ctx.results.append(hint)

        # Employee name resolution hints (t007)
        hint = self._name_resolution_hints.maybe_hint_name_resolution(ctx.model, result, task_text)
        if hint:
            ctx.results.append(hint)

        # Query subject hints (t077) - detect coachee/mentee who should NOT be in links
        hint = self._query_subject_hints.maybe_hint_query_subject(ctx.model, result, task_text, ctx)
        if hint:
            ctx.results.append(hint)

        # Skill search strategy hints (t013, t074)
        hint = self._skill_strategy_hints.maybe_hint_skill_strategy(ctx.model, result, task_text)
        if hint:
            ctx.results.append(hint)

        # Skill comparison hints (t094)
        hint = self._skill_comparison_hints.maybe_hint_skill_comparison(ctx.model, result, task_text)
        if hint:
            ctx.results.append(hint)

        # Tie-breaker hints (t010, t075)
        hint = self._tie_breaker_hints.maybe_hint_tie_breaker(ctx.model, result, task_text)
        if hint:
            ctx.results.append(hint)

        # Recommendation query hints (t017) - remind to return ALL qualifying employees
        # AICODE-NOTE: t017 FIX - now pass model for pagination tracking
        if isinstance(ctx.model, client.Req_SearchEmployees):
            next_offset = getattr(result, 'next_offset', -1)
            hint = self._recommendation_hints.maybe_hint_recommendation_query(
                result, task_text, next_offset, ctx.model
            )
            if hint:
                ctx.results.append(hint)

        # Time entry update hints
        if isinstance(ctx.model, client.Req_SearchTimeEntries):
            hint = self._time_entry_hints.maybe_hint_time_update(result, task_text)
            if hint:
                ctx.results.append(hint)

        # Project search disambiguation hints
        if isinstance(ctx.model, client.Req_SearchProjects):
            for hint in self._project_search.enrich(ctx, result, task_text):
                ctx.results.append(hint)

            # Workload calculation hints (t079)
            hint = self._workload_hints.maybe_hint_workload(ctx.model, result, task_text)
            if hint:
                ctx.results.append(hint)

        # Time summary fallback hints (t009)
        if isinstance(ctx.model, client.Req_TimeSummaryByEmployee):
            hint = self._time_summary_fallback_hints.maybe_hint_time_summary_fallback(
                ctx.model, result, task_text
            )
            if hint:
                ctx.results.append(hint)

        # Project team name resolution hints (t081)
        if isinstance(ctx.model, client.Req_GetProject):
            hint = self._project_team_name_hints.maybe_hint_team_name_resolution(
                ctx.model, result, task_text
            )
            if hint:
                ctx.results.append(hint)

        # AICODE-NOTE: t087 FIX - Track customer contact info for link extraction.
        # When customers_get returns contact info, store it for later lookup
        # so that response parser can link customer when contact email is mentioned.
        if isinstance(ctx.model, client.Req_GetCustomer):
            # API returns 'company' field, not 'customer'
            customer = getattr(result, 'company', None) or getattr(result, 'customer', None) or result
            cust_id = getattr(ctx.model, 'id', None)
            if cust_id and customer:
                contact_name = getattr(customer, 'primary_contact_name', None)
                contact_email = getattr(customer, 'primary_contact_email', None)
                if contact_name or contact_email:
                    customer_contacts = ctx.shared.get('customer_contacts', {})
                    customer_contacts[cust_id] = {
                        'name': contact_name or '',
                        'email': contact_email or ''
                    }
                    ctx.shared['customer_contacts'] = customer_contacts

        # Wiki file hints on wiki_list
        wiki_manager = ctx.shared.get('wiki_manager')
        if isinstance(result, client.Resp_ListWiki) and wiki_manager and wiki_manager.pages:
            hint = self._wiki_hints.get_task_file_hints(
                wiki_manager, task_text, is_public_user=False,
                skip_critical=False, context="wiki_list"
            )
            if hint:
                ctx.results.append(hint)

        # Efficiency hints for sequential lookups and excessive pagination
        action_name = ctx.model.__class__.__name__
        # Map class names to tool names
        action_name_map = {
            'Req_GetProject': 'projects_get',
            'Req_GetEmployee': 'employees_get',
            'Req_GetCustomer': 'customers_get',
            'Req_SearchProjects': 'projects_search',
            'Req_SearchEmployees': 'employees_search',
            'Req_SearchCustomers': 'customers_search',
        }
        tool_name = action_name_map.get(action_name, '')

        if tool_name:
            # Parallel call hints
            hint = self._efficiency_hints.maybe_hint_parallel_calls(ctx, tool_name)
            if hint:
                ctx.results.append(hint)

            # Pagination limit hints
            hint = self._efficiency_hints.maybe_hint_pagination_limit(ctx, tool_name, task_text)
            if hint:
                ctx.results.append(hint)

            # Filter usage hints
            hint = self._efficiency_hints.maybe_hint_filter_usage(ctx, tool_name, result)
            if hint:
                ctx.results.append(hint)

            # Total pagination budget warning (across all search types)
            hint = self._efficiency_hints.get_total_pagination_warning(ctx)
            if hint:
                ctx.results.append(hint)

            # Turn budget warning
            current_turn = ctx.shared.get('current_turn', 0)
            max_turns = ctx.shared.get('max_turns', 20)
            hint = self._efficiency_hints.get_turn_warning(current_turn, max_turns)
            if hint:
                ctx.results.append(hint)

        # Customer projects filter confusion hint (owner vs customer)
        if isinstance(ctx.model, client.Req_SearchProjects):
            hint = self._customer_projects_hints.maybe_hint_customer_filter(ctx.model, task_text)
            if hint:
                ctx.results.append(hint)

            # Project name normalization hint
            hint = self._project_name_hints.maybe_hint_name_normalization(ctx.model, result)
            if hint:
                ctx.results.append(hint)

        # ID extraction warning for failed gets
        if action_name in ('Req_GetProject', 'Req_GetEmployee', 'Req_GetCustomer'):
            hint = self._id_extraction_hints.maybe_hint_id_extraction(ctx.model, result, action_name)
            if hint:
                ctx.results.append(hint)

    def clear_task_caches(self) -> None:
        """
        Clear all per-task caches in enrichers.

        AICODE-NOTE: Call this at the start of each new task to prevent
        state leaking between tasks. Critical for enrichers that track
        accumulated results across pagination (e.g., RecommendationQueryHintEnricher).
        """
        # Clear efficiency hints turn cache
        self._efficiency_hints.clear_turn_cache()

        # Clear customer projects filter cache
        self._customer_projects_hints.clear_cache()

        # Clear recommendation query accumulated results (t017 fix)
        self._recommendation_hints.clear_cache()
