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
    ProjectSearchEnricher, WikiHintEnricher,
    RoleEnricher, ArchiveHintEnricher, TimeEntryHintEnricher,
    CustomerSearchHintEnricher, PaginationHintEnricher,
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
        self._pagination_hints = PaginationHintEnricher()

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

        # Archived project hints
        hint = self._archive_hints.maybe_hint_archived_logging(ctx.model, result, task_text)
        if hint:
            ctx.results.append(hint)

        # Pagination hints
        hint = self._pagination_hints.maybe_hint_pagination(result)
        if hint:
            ctx.results.append(hint)

        # Customer search hints
        hint = self._customer_hints.maybe_hint_empty_customers(ctx.model, result)
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

        # Wiki file hints on wiki_list
        wiki_manager = ctx.shared.get('wiki_manager')
        if isinstance(result, client.Resp_ListWiki) and wiki_manager and wiki_manager.pages:
            hint = self._wiki_hints.get_task_file_hints(
                wiki_manager, task_text, is_public_user=False,
                skip_critical=False, context="wiki_list"
            )
            if hint:
                ctx.results.append(hint)
