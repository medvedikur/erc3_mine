"""
Core action handling module (refactored).

Main entry point for executing agent actions against the API.
Uses strategy pattern for special actions and enrichers for response hints.
"""
from typing import List, Any, Optional
from erc3 import ApiException
from erc3.erc3 import client

from .base import ToolContext, Middleware
from .action_handlers import (
    WikiSearchHandler, WikiLoadHandler, CompositeActionHandler,
    ProjectSearchHandler, EmployeeSearchHandler
)
from .enrichers import (
    ProjectSearchEnricher, WikiHintEnricher,
    RoleEnricher, ArchiveHintEnricher, TimeEntryHintEnricher,
    CustomerSearchHintEnricher, PaginationHintEnricher,
)
from .execution import (
    EmployeeUpdateStrategy, TimeEntryUpdateStrategy, ProjectTeamUpdateStrategy,
    handle_pagination_error, extract_learning_from_error, patch_null_list_response,
)
from .execution.update_strategies import merge_non_none
from .intent import detect_intent
from utils import CLI_RED, CLI_GREEN, CLI_BLUE, CLI_YELLOW, CLI_CLR


class DefaultActionHandler:
    """
    Standard handler that executes actions against the API.

    This is the fallback handler used by CompositeActionHandler when no
    specialized handler matches the action type.

    Responsibilities:
    - Pre-process requests (salary integer conversion, field cleanup)
    - Execute actions using appropriate strategy
    - Enrich responses with helpful hints
    - Handle errors with learning extraction
    """

    def __init__(self):
        # Update strategies (fetch-merge-dispatch)
        self._update_strategies = [
            EmployeeUpdateStrategy(),
            TimeEntryUpdateStrategy(),
            ProjectTeamUpdateStrategy(),
        ]

        # Response enrichers
        self._project_search = ProjectSearchEnricher()
        self._wiki_hints = WikiHintEnricher()
        self._role_enricher = RoleEnricher()
        self._archive_hints = ArchiveHintEnricher()
        self._time_entry_hints = TimeEntryHintEnricher()
        self._customer_hints = CustomerSearchHintEnricher()
        self._pagination_hints = PaginationHintEnricher()

    def can_handle(self, ctx: ToolContext) -> bool:
        """Default handler can handle any action."""
        return True

    def handle(self, ctx: ToolContext) -> None:
        """
        Execute action with pre-processing, strategy selection, and enrichments.
        """
        action_name = ctx.model.__class__.__name__
        print(f"  {CLI_BLUE}> Executing:{CLI_CLR} {action_name}")

        try:
            # 1. Pre-process request
            self._preprocess_request(ctx)

            # 2. Execute action
            result = self._execute_action(ctx)

            # 3. Post-process: identity, wiki sync, security
            result = self._postprocess_result(ctx, result)

            # 4. Format and log success
            result_json = result.model_dump_json(exclude_none=True)
            print(f"  {CLI_GREEN}OK{CLI_CLR}")
            self._log_api_call(ctx, action_name, ctx.model, result)

            # 5. Enrich response with hints
            self._enrich_response(ctx, result)

            # 6. Add result to context
            ctx.results.append(f"Action ({action_name}): SUCCESS\nResult: {result_json}")

        except ApiException as e:
            self._handle_api_error(ctx, action_name, e)
        except Exception as e:
            self._handle_system_error(ctx, action_name, e)

    # =========================================================================
    # Pre-processing
    # =========================================================================

    def _preprocess_request(self, ctx: ToolContext) -> None:
        """Pre-process request before execution."""
        if isinstance(ctx.model, client.Req_UpdateEmployeeInfo):
            self._preprocess_employee_update(ctx)

    def _preprocess_employee_update(self, ctx: ToolContext) -> None:
        """Pre-process employee update request."""
        task_text = getattr(ctx.shared.get("task"), "task_text", "") or ""
        intent = detect_intent(task_text)
        salary_only = intent.is_salary_only

        # Ensure salary is always an integer (API requirement)
        if ctx.model.salary is not None:
            ctx.model.salary = int(round(ctx.model.salary))

        # Set changed_by if not already set
        security_manager = ctx.shared.get('security_manager')
        current_user = getattr(security_manager, 'current_user', None) if security_manager else None
        if not getattr(ctx.model, "changed_by", None):
            ctx.model.changed_by = current_user

        # Clear non-essential fields based on intent
        if salary_only:
            # For salary-only updates, explicitly clear all other fields
            for field in ['skills', 'wills', 'notes', 'location', 'department']:
                setattr(ctx.model, field, None)
        else:
            # For other updates, only clear empty fields
            for field in ['skills', 'wills', 'notes', 'location', 'department']:
                val = getattr(ctx.model, field, None)
                if val in ([], "", None):
                    setattr(ctx.model, field, None)

    # =========================================================================
    # Execution
    # =========================================================================

    def _execute_action(self, ctx: ToolContext) -> Any:
        """Execute action using appropriate strategy."""
        # Check for pre-processed results from specialized handlers
        if '_search_error' in ctx.shared:
            raise ctx.shared.pop('_search_error')
        if '_project_search_result' in ctx.shared:
            return ctx.shared.pop('_project_search_result')
        if '_employee_search_result' in ctx.shared:
            return ctx.shared.pop('_employee_search_result')

        # Try update strategies
        for strategy in self._update_strategies:
            if strategy.can_handle(ctx.model):
                return strategy.execute(ctx.model, ctx.api, ctx.shared)

        # Standard dispatch with pagination retry
        return self._dispatch_with_retry(ctx)

    def _dispatch_with_retry(self, ctx: ToolContext) -> Any:
        """Dispatch request with pagination error retry."""
        has_limit = hasattr(ctx.model, 'limit')

        try:
            return ctx.api.dispatch(ctx.model)
        except ApiException as e:
            # Try pagination error handling
            if has_limit:
                handled, result = handle_pagination_error(e, ctx.model, ctx.api)
                if handled:
                    if result is not None:
                        return result
                    else:
                        # Unrecoverable pagination error
                        raise e

            # Try null list patching
            handled, patched = patch_null_list_response(e)
            if handled and patched is not None:
                return patched

            # Re-raise with learning hint
            learning_hint = extract_learning_from_error(e, ctx.model)
            if learning_hint:
                ctx.results.append(f"\n{learning_hint}\n")
            raise e
        except Exception as e:
            # Try null list patching for non-API errors
            handled, patched = patch_null_list_response(e)
            if handled and patched is not None:
                return patched

            learning_hint = extract_learning_from_error(e, ctx.model)
            if learning_hint:
                ctx.results.append(f"\n{learning_hint}\n")
            raise e

    # =========================================================================
    # Post-processing
    # =========================================================================

    def _postprocess_result(self, ctx: ToolContext, result: Any) -> Any:
        """Post-process result: identity, wiki sync, security redaction."""
        security_manager = ctx.shared.get('security_manager')
        wiki_manager = ctx.shared.get('wiki_manager')

        # Update identity state
        if security_manager and isinstance(result, client.Resp_WhoAmI):
            identity_msg = security_manager.update_identity(result)
            if identity_msg:
                ctx.results.append(f"\n{identity_msg}\n")

        # Sync wiki and inject critical docs
        wiki_changed = False
        if wiki_manager:
            if isinstance(result, client.Resp_WhoAmI) and result.wiki_sha1:
                wiki_changed = wiki_manager.sync(result.wiki_sha1)
            elif isinstance(result, client.Resp_ListWiki) and result.sha1:
                wiki_changed = wiki_manager.sync(result.sha1)

        if wiki_changed and wiki_manager:
            self._inject_wiki_updates(ctx, wiki_manager, security_manager)

        # Inject merger policy for public users
        if security_manager and isinstance(result, client.Resp_WhoAmI):
            self._maybe_inject_merger_policy(ctx, security_manager, wiki_manager)

        # Apply security redaction
        if security_manager:
            result = security_manager.redact_result(result)

        return result

    def _inject_wiki_updates(self, ctx: ToolContext, wiki_manager, security_manager) -> None:
        """Inject wiki updates into context."""
        critical_docs = wiki_manager.get_critical_docs()
        if critical_docs:
            print(f"  {CLI_YELLOW}Wiki changed! Injecting critical docs...{CLI_CLR}")
            ctx.results.append(
                f"\nWIKI UPDATED! You MUST read these policy documents before proceeding:\n\n"
                f"{critical_docs}\n\n"
                f"Action based on outdated rules will be REJECTED."
            )

        # Task-relevant file hint
        task = ctx.shared.get('task')
        task_text = getattr(task, 'task_text', '') if task else ''
        is_public = getattr(security_manager, 'is_public', False) if security_manager else False

        hint = self._wiki_hints.get_task_file_hints(
            wiki_manager, task_text, is_public,
            skip_critical=True, context="wiki_change"
        )
        if hint:
            ctx.results.append(hint)

    def _maybe_inject_merger_policy(self, ctx: ToolContext, security_manager, wiki_manager) -> None:
        """Inject merger policy for public users."""
        if not security_manager.is_public:
            return
        if not wiki_manager or not wiki_manager.has_page("merger.md"):
            return

        merger_content = wiki_manager.get_page("merger.md")
        if merger_content:
            print(f"  {CLI_YELLOW}Public user - Injecting merger policy...{CLI_CLR}")
            ctx.results.append(
                f"\nCRITICAL POLICY - You are a PUBLIC chatbot and merger.md exists:\n\n"
                f"=== merger.md ===\n{merger_content}\n\n"
                f"YOU MUST include the acquiring company name (exactly as written in merger.md) "
                f"in EVERY response you give, regardless of the question topic."
            )

    # =========================================================================
    # Enrichments
    # =========================================================================

    def _enrich_response(self, ctx: ToolContext, result: Any) -> None:
        """Add helpful hints to response based on result type."""
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

    # =========================================================================
    # Error Handling
    # =========================================================================

    def _handle_api_error(self, ctx: ToolContext, action_name: str, error: ApiException) -> None:
        """Handle API exception."""
        error_msg = error.api_error.error if error.api_error else str(error)
        print(f"  {CLI_RED}FAILED:{CLI_CLR} {error_msg}")
        ctx.results.append(f"Action ({action_name}): FAILED\nError: {error_msg}")
        self._log_api_call(ctx, action_name, ctx.model, error=error_msg)

    def _handle_system_error(self, ctx: ToolContext, action_name: str, error: Exception) -> None:
        """Handle system exception."""
        print(f"  {CLI_RED}SYSTEM ERROR:{CLI_CLR} {error}")
        ctx.results.append(f"Action ({action_name}): SYSTEM ERROR\nError: {str(error)}")
        self._log_api_call(ctx, action_name, ctx.model, error=str(error))

    # =========================================================================
    # Logging
    # =========================================================================

    def _log_api_call(
        self,
        ctx: ToolContext,
        action_name: str,
        request: Any,
        response: Any = None,
        error: str = None
    ) -> None:
        """Log API call to failure logger if available."""
        failure_logger = ctx.shared.get('failure_logger')
        task_id = ctx.shared.get('task_id')
        if failure_logger and task_id:
            try:
                req_dict = request.model_dump() if hasattr(request, 'model_dump') else str(request)
                resp_dict = None
                if response is not None:
                    resp_dict = response.model_dump() if hasattr(response, 'model_dump') else str(response)
                failure_logger.log_api_call(task_id, action_name, req_dict, resp_dict, error)
            except Exception:
                pass  # Don't break execution on logging errors


class ActionExecutor:
    """Main executor that orchestrates middleware and handlers."""

    def __init__(self, api, middleware: List[Middleware] = None, task: Any = None):
        self.api = api
        self.middleware = middleware or []
        # Use CompositeActionHandler with specialized handlers first, then default
        default_handler = DefaultActionHandler()
        self.handler = CompositeActionHandler(
            handlers=[
                WikiSearchHandler(),
                WikiLoadHandler(),
                ProjectSearchHandler(),
                EmployeeSearchHandler(),
            ],
            default_handler=default_handler
        )
        self.task = task

    def execute(self, action_dict: dict, action_model: Any, initial_shared: dict = None) -> ToolContext:
        """Execute action through middleware chain and handler."""
        ctx = ToolContext(self.api, action_dict, action_model)
        if self.task:
            ctx.shared['task'] = self.task

        # Merge initial shared state from caller
        if initial_shared:
            for key, value in initial_shared.items():
                if key not in ctx.shared:
                    ctx.shared[key] = value

        # Run middleware
        for mw in self.middleware:
            mw.process(ctx)
            if ctx.stop_execution:
                return ctx

        # Run handler
        self.handler.handle(ctx)
        return ctx
