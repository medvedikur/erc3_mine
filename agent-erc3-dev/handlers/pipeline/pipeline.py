"""
Main action pipeline orchestrator.

Coordinates preprocessors, executor, postprocessors, and enrichers.
"""

from typing import Any, List, TYPE_CHECKING

from erc3.erc3 import client

from .base import Preprocessor, PostProcessor
from .preprocessors import (
    EmployeeUpdatePreprocessor,
    SkillNameCorrectionPreprocessor,
    SendToLocationPreprocessor,
    LocationNameCorrectionPreprocessor,
    CoachingSkillOnlyPreprocessor,
)
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
    SkillSearchStrategyHintEnricher, CombinedSkillWillHintEnricher, ProjectCustomerSearchHintEnricher,
    EmployeeNameResolutionHintEnricher,
    SkillComparisonHintEnricher, QuerySubjectHintEnricher, TieBreakerHintEnricher,
    RecommendationQueryHintEnricher, TimeSummaryFallbackHintEnricher,
    ProjectTeamNameResolutionHintEnricher, ProjectSkillsHintEnricher,
    SwapWorkloadsHintEnricher, KeyAccountExplorationHintEnricher,
    LeadSalaryComparisonHintEnricher, BusiestEmployeeTimeSliceEnricher,
    LeastBusyEmployeeTimeSliceEnricher, SendToLocationHintEnricher,
    CoachingWillHintEnricher, SelfCheckEnricher, LocationFilterSummaryEnricher,
    EmptyLocationSkillSearchEnricher,
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
            SkillNameCorrectionPreprocessor(),  # t056 fix: auto-correct skill names
            LocationNameCorrectionPreprocessor(),  # t012/t086: "City (Country)" -> "City Office – Country"
            SendToLocationPreprocessor(),  # t013 fix: warn about location filter with "send to"
            CoachingSkillOnlyPreprocessor(),  # t077 fix: remove will filter for skill-only coaching
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
        self._project_skills_hints = ProjectSkillsHintEnricher()
        self._swap_workloads_hints = SwapWorkloadsHintEnricher()
        self._key_account_exploration_hints = KeyAccountExplorationHintEnricher()
        self._lead_salary_hints = LeadSalaryComparisonHintEnricher()
        self._busiest_time_slice_hints = BusiestEmployeeTimeSliceEnricher()
        self._least_busy_time_slice_hints = LeastBusyEmployeeTimeSliceEnricher()
        self._send_to_hints = SendToLocationHintEnricher()
        self._coaching_will_hints = CoachingWillHintEnricher()
        self._combined_skill_will_hints = CombinedSkillWillHintEnricher()
        self._project_customer_search_hints = ProjectCustomerSearchHintEnricher()
        self._self_check_hints = SelfCheckEnricher()
        self._location_filter_hints = LocationFilterSummaryEnricher()  # t086 fix
        self._empty_location_skill_hints = EmptyLocationSkillSearchEnricher()  # t086 fix

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

        # AICODE-NOTE: t037 FIX - Check if preprocessor blocked execution
        # EmployeeUpdatePreprocessor sets stop_execution=True for salary-related notes
        if getattr(ctx, 'stop_execution', False):
            # Preprocessor blocked execution - don't call API!
            return

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

        # AICODE-NOTE: t067 fix. Store wiki content from API for rename operations.
        # When LLM copies content, it may corrupt Unicode. We store API content
        # so wiki_update parser can use the exact bytes from API.
        if isinstance(ctx.model, client.Req_LoadWiki):
            wiki_content = getattr(result, 'content', None)
            wiki_file = getattr(ctx.model, 'file', None)
            if wiki_content and wiki_file:
                if '_loaded_wiki_content_api' not in ctx.shared:
                    ctx.shared['_loaded_wiki_content_api'] = {}
                ctx.shared['_loaded_wiki_content_api'][wiki_file] = wiki_content
                print(f"  [t067] Stored API wiki content for {wiki_file} ({len(wiki_content)} bytes)")

        # AICODE-NOTE: Track pending pagination for LIST query guard (t016, t086)
        # If API returns next_offset > 0, agent should continue paginating
        next_offset = getattr(result, 'next_offset', -1)
        if next_offset > 0:
            pending = ctx.shared.get('pending_pagination', {})
            action_name = type(ctx.model).__name__
            # AICODE-NOTE: t087 FIX - customers_list returns `companies`, not `customers`.
            # Keep this generic so pagination guards can show correct fetched counts.
            page_items = (
                getattr(result, 'employees', None) or
                getattr(result, 'projects', None) or
                getattr(result, 'customers', None) or
                getattr(result, 'companies', None) or
                []
            )
            pending[action_name] = {
                'next_offset': next_offset,
                'current_count': len(page_items or [])
            }
            ctx.shared['pending_pagination'] = pending
        elif next_offset == -1:
            # Pagination complete for this action type - clear it
            pending = ctx.shared.get('pending_pagination', {})
            action_name = type(ctx.model).__name__
            if action_name in pending:
                del pending[action_name]
                ctx.shared['pending_pagination'] = pending

        # AICODE-NOTE: t087 FIX - Track ALL customer IDs seen via customers_list for later validation.
        # This enables response guards to ensure exhaustive scanning before concluding ok_not_found.
        if isinstance(ctx.model, client.Req_ListCustomers):
            companies = getattr(result, 'companies', None) or []
            seen_ids = ctx.shared.get('_customers_list_ids', set())
            if not isinstance(seen_ids, set):
                try:
                    seen_ids = set(seen_ids)  # type: ignore[arg-type]
                except Exception:
                    seen_ids = set()
            for comp in companies:
                comp_id = comp.get('id') if isinstance(comp, dict) else getattr(comp, 'id', None)
                if comp_id:
                    seen_ids.add(comp_id)
            ctx.shared['_customers_list_ids'] = seen_ids

            # Track completion explicitly for easier guard checks
            ctx.shared['_customers_list_complete'] = (next_offset == -1)

        # AICODE-NOTE: t087 FIX - Track which customers were actually checked via customers_get.
        if isinstance(ctx.model, client.Req_GetCustomer):
            cust_id = getattr(ctx.model, 'id', None)
            if cust_id:
                checked = ctx.shared.get('_customers_get_checked_ids', set())
                if not isinstance(checked, set):
                    try:
                        checked = set(checked)  # type: ignore[arg-type]
                    except Exception:
                        checked = set()
                checked.add(cust_id)
                ctx.shared['_customers_get_checked_ids'] = checked

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
                # AICODE-NOTE: t054 FIX - Pass shared to store user role for guards
                # AICODE-NOTE: t051 FIX - Pass task_text to detect status change requests
                hint = self._role_enricher.enrich_projects_with_user_role(
                    result, current_user, ctx.shared, task_text
                )
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

            # AICODE-NOTE: t069 FIX - Accumulate ALL project IDs from projects_search
            # When pagination completes (next_offset <= 0), show complete list to prevent
            # LLM from losing track of projects when aggregating large result sets
            if hasattr(result, 'projects') and result.projects:
                accumulated = ctx.shared.get('accumulated_project_ids', [])
                for proj in result.projects:
                    if proj.id and proj.id not in accumulated:
                        accumulated.append(proj.id)
                ctx.shared['accumulated_project_ids'] = accumulated

                # When pagination is complete, show summary of ALL project IDs
                next_offset = getattr(result, 'next_offset', None)
                if next_offset is not None and next_offset <= 0 and len(accumulated) > 5:
                    # Check if this is an exhaustive project query
                    task_lower = task_text.lower() if task_text else ''
                    is_exhaustive = any(kw in task_lower for kw in [
                        'every lead', 'all leads', 'every project', 'all projects',
                        'for each lead', 'create wiki', 'each project'
                    ])
                    if is_exhaustive:
                        ids_list = ', '.join(accumulated)
                        ctx.results.append(
                            f"\n📊 **PROJECT SEARCH COMPLETE** — {len(accumulated)} projects found:\n"
                            f"IDs: [{ids_list}]\n\n"
                            f"⚠️ IMPORTANT: Use EXACTLY these {len(accumulated)} project IDs for projects_get!\n"
                            f"Do NOT miss any project — copy this list to your action_queue."
                        )

        # AICODE-NOTE: t026 FIX - Track when project has internal customer (cust_bellini_internal)
        # This flag is used by InternalProjectContactGuard to block ok_answer for contact queries
        if isinstance(ctx.model, client.Req_SearchProjects):
            if hasattr(result, 'projects') and result.projects:
                for proj in result.projects:
                    customer = getattr(proj, 'customer', '') or ''
                    if 'internal' in customer.lower() or 'bellini_internal' in customer.lower():
                        ctx.shared['_internal_customer_contact_blocked'] = True
                        break

        if isinstance(ctx.model, client.Req_GetProject):
            project = getattr(result, 'project', None)
            if project:
                customer = getattr(project, 'customer', '') or ''
                if 'internal' in customer.lower() or 'bellini_internal' in customer.lower():
                    ctx.shared['_internal_customer_contact_blocked'] = True

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

        # Key account + exploration deals hints (t042)
        hint = self._key_account_exploration_hints.maybe_hint_key_account_exploration(
            ctx.model, result, task_text
        )
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

        # Project role search hint (t081) - when task asks "role of X at Y"
        hint = self._employee_hints.maybe_hint_project_role_search(ctx.model, result, task_text)
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
        # AICODE-NOTE: t013 FIX - pass shared context for state tracking
        hint = self._skill_strategy_hints.maybe_hint_skill_strategy(ctx.model, result, task_text, ctx.shared)
        if hint:
            ctx.results.append(hint)

        # AICODE-NOTE: t013 FIX - Show hint for "send to" location mismatch
        if hint := self._send_to_hints.maybe_hint_location_check(
            ctx.model, result, task_text
        ):
            ctx.results.append(hint)

        # AICODE-NOTE: t077 FIX - Clarify valid coaching wills
        if hint := self._coaching_will_hints.maybe_hint_coaching_wills(
            ctx.model, task_text
        ):
            ctx.results.append(hint)

        # Combined skill + will search hints (t056)
        hint = self._combined_skill_will_hints.maybe_hint_combined_filter(ctx.model, result, task_text)
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
        # AICODE-NOTE: t056 FIX - pass shared context to store accumulated employee IDs
        if isinstance(ctx.model, client.Req_SearchEmployees):
            next_offset = getattr(result, 'next_offset', -1)
            hint = self._recommendation_hints.maybe_hint_recommendation_query(
                result, task_text, next_offset, ctx.model, ctx.shared
            )
            if hint:
                ctx.results.append(hint)

            # AICODE-NOTE: t086 FIX - Location breakdown for "list employees in X" queries
            # When pagination completes, show all employees grouped by location
            # to prevent LLM from forgetting employees during manual filtering
            hint = self._location_filter_hints.maybe_show_location_breakdown(
                ctx.model, result, task_text, ctx.shared
            )
            if hint:
                ctx.results.append(hint)

            # AICODE-NOTE: t086 FIX - When location + skills/wills returns 0 results,
            # suggest removing location filter and filtering manually
            hint = self._empty_location_skill_hints.maybe_suggest_manual_filter(
                ctx.model, result, task_text
            )
            if hint:
                ctx.results.append(hint)

        # Time entry update hints
        if isinstance(ctx.model, client.Req_SearchTimeEntries):
            hint = self._time_entry_hints.maybe_hint_time_update(result, task_text)
            if hint:
                ctx.results.append(hint)

            # AICODE-NOTE: t097 FIX - Detect when agent uses time_search for "swap workloads" task
            # In project context, "workload" = time_slice, not time entries. Redirect agent.
            hint = self._swap_workloads_hints.maybe_hint_swap_wrong_tool(ctx.model, result, task_text)
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

        # Project skills hints (t096)
        if isinstance(ctx.model, client.Req_GetProject):
            hint = self._project_skills_hints.maybe_hint_project_skills(
                ctx.model, result, task_text
            )
            if hint:
                ctx.results.append(hint)

        # Swap workloads/roles hints (t092, t097) - explain time_slice/role swap via projects_team_update
        # AICODE-NOTE: t092 FIX - Pass department to enricher for exec permission hint
        if isinstance(ctx.model, client.Req_GetProject):
            security_manager = ctx.shared.get('security_manager')
            department = getattr(security_manager, 'department', '') if security_manager else ''
            hint = self._swap_workloads_hints.maybe_hint_swap_workloads(
                ctx.model, result, task_text, department=department
            )
            if hint:
                ctx.results.append(hint)

        # AICODE-NOTE: t012 FIX - Track time_slice for busiest employee calculation
        # When agent fetches many projects via fallback (time_summary_employee returns None),
        # we accumulate time_slice per employee and show summary when threshold reached.
        if isinstance(ctx.model, client.Req_GetProject):
            hint = self._busiest_time_slice_hints.maybe_accumulate_time_slice(
                ctx.model, result, ctx.shared, task_text
            )
            if hint:
                ctx.results.append(hint)

        # AICODE-NOTE: t010 FIX - Track projects per employee for least busy calculation
        # When agent uses projects_search(member=...) fallback to find least busy,
        # we track all employees and show ALL with minimum workload (not just one).
        if isinstance(ctx.model, client.Req_SearchProjects):
            hint = self._least_busy_time_slice_hints.maybe_track_employee_projects(
                ctx.model, result, ctx.shared, task_text
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

        # Project customer search hints on wiki_search (t028)
        hint = self._project_customer_search_hints.maybe_hint_project_customer_search(
            ctx.model, result, task_text
        )
        if hint:
            ctx.results.append(hint)

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

        # AICODE-NOTE: t016 FIX - Lead salary comparison calculation
        # When fetching baseline employee for "project leads with salary > X" task,
        # automatically calculate and return the complete answer
        # Triggers on BOTH employees_get AND employees_search
        if action_name in ('Req_GetEmployee', 'Req_SearchEmployees'):
            hint = self._lead_salary_hints.maybe_calculate_leads_with_higher_salary(
                ctx, ctx.model, result, task_text
            )
            if hint:
                ctx.results.append(hint)

        # AICODE-NOTE: t094 FIX - Self-check hint for "skills I don't have" queries
        # When agent fetches current_user via employees_get, add reminder about their skills
        if action_name == 'Req_GetEmployee' and current_user:
            emp_id = getattr(ctx.model, 'id', None)
            if emp_id == current_user:
                employee = getattr(result, 'employee', None)
                if employee:
                    skills = getattr(employee, 'skills', []) or []
                    hint = self._self_check_hints.enrich_for_skill_query(
                        task_text, skills
                    )
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

        # Clear lead salary comparison cache (t016 fix)
        self._lead_salary_hints._calculation_done = False
        self._lead_salary_hints._result_cache = None

        # Clear combined skill+will hint cache (t056 fix)
        self._combined_skill_will_hints._hint_shown = False

        # Clear coaching will hint cache (context bloat fix)
        self._coaching_will_hints._hint_shown = False

        # Clear pagination hint counter (context bloat fix)
        self._pagination_hints.reset_hint_count()

        # Clear project customer search hint cache (t028 fix)
        self._project_customer_search_hints._hint_shown = False

        # Clear employee search hint cache (context bloat fix)
        self._employee_hints._customer_contact_hint_shown = False
        self._employee_hints._project_role_hint_shown = False

        # Clear location filter hint cache (t086 fix)
        self._location_filter_hints.clear_cache()
