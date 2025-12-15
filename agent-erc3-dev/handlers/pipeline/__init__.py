"""
Action processing pipeline.

Breaks down the monolithic DefaultActionHandler into composable stages:
- Preprocessors: Prepare request before execution
- Executor: Execute API calls with retry/error handling
- PostProcessors: Handle identity, wiki sync, security redaction
- Enrichers: Add context-aware hints to responses
"""

from .base import (
    PipelineStage,
    Preprocessor,
    PostProcessor,
    ExecutionResult,
)
from .preprocessors import EmployeeUpdatePreprocessor
from .postprocessors import (
    IdentityPostProcessor,
    WikiSyncPostProcessor,
    SecurityRedactionPostProcessor,
    BonusHintPostProcessor,
)
from .executor import PipelineExecutor
from .error_handler import ErrorHandler
from .pipeline import ActionPipeline

__all__ = [
    # Base
    'PipelineStage',
    'Preprocessor',
    'PostProcessor',
    'ExecutionResult',
    # Preprocessors
    'EmployeeUpdatePreprocessor',
    # PostProcessors
    'IdentityPostProcessor',
    'WikiSyncPostProcessor',
    'SecurityRedactionPostProcessor',
    'BonusHintPostProcessor',
    # Core
    'PipelineExecutor',
    'ErrorHandler',
    'ActionPipeline',
]
