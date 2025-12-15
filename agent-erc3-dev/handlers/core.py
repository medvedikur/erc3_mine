"""
Core action handling module.

Main entry point for executing agent actions against the API.
Uses pipeline architecture for composable action processing.

Architecture:
- ActionPipeline: Orchestrates preprocessing, execution, postprocessing, enrichment
- CompositeActionHandler: Routes to specialized handlers (wiki, project, employee)
- ActionExecutor: Runs middleware chain and delegates to handlers
"""

from typing import List, Any

from .base import ToolContext, Middleware
from .action_handlers import (
    WikiSearchHandler, WikiLoadHandler, CompositeActionHandler,
    ProjectSearchHandler, EmployeeSearchHandler
)
from .pipeline import ActionPipeline


# AICODE-NOTE: DefaultActionHandler was refactored into ActionPipeline (handlers/pipeline/).
# The pipeline breaks down the 445-line monolith into composable stages:
# - Preprocessors (request normalization)
# - Executor (API dispatch with retry)
# - PostProcessors (identity, wiki sync, security)
# - Enrichers (context-aware hints)
# Legacy DefaultActionHandler is preserved as alias for backward compatibility.
DefaultActionHandler = ActionPipeline


class ActionExecutor:
    """
    Main executor that orchestrates middleware and handlers.

    Execution flow:
    1. Create ToolContext with action model and shared state
    2. Run middleware chain (guards, validation, etc.)
    3. If not stopped, delegate to CompositeActionHandler
    4. CompositeActionHandler routes to specialized handler or ActionPipeline

    The ActionPipeline (formerly DefaultActionHandler) is the fallback
    that handles standard API calls with preprocessing and enrichment.
    """

    def __init__(self, api, middleware: List[Middleware] = None, task: Any = None):
        """
        Initialize executor.

        Args:
            api: ERC3 API client
            middleware: List of middleware to run before handling
            task: Current task info (injected into context)
        """
        self.api = api
        self.middleware = middleware or []

        # Composite handler with specialized handlers first, then pipeline as default
        self.handler = CompositeActionHandler(
            handlers=[
                WikiSearchHandler(),
                WikiLoadHandler(),
                ProjectSearchHandler(),
                EmployeeSearchHandler(),
            ],
            default_handler=ActionPipeline()
        )
        self.task = task

    def execute(self, action_dict: dict, action_model: Any, initial_shared: dict = None) -> ToolContext:
        """
        Execute action through middleware chain and handler.

        Args:
            action_dict: Raw action dict from LLM
            action_model: Parsed Pydantic model
            initial_shared: Initial shared state to merge

        Returns:
            ToolContext with results and state after execution
        """
        ctx = ToolContext(self.api, action_dict, action_model)

        # Inject task into context
        if self.task:
            ctx.shared['task'] = self.task

        # Merge initial shared state from caller
        if initial_shared:
            for key, value in initial_shared.items():
                if key not in ctx.shared:
                    ctx.shared[key] = value

        # Run middleware chain
        for mw in self.middleware:
            mw.process(ctx)
            if ctx.stop_execution:
                return ctx

        # Run handler (specialized or pipeline)
        self.handler.handle(ctx)
        return ctx
