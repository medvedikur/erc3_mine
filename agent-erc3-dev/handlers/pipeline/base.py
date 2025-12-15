"""
Base classes for pipeline stages.

Defines the contracts for preprocessors, postprocessors, and execution results.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from ..base import ToolContext


@dataclass
class ExecutionResult:
    """
    Result of action execution.

    Encapsulates the API response along with metadata about the execution.
    """
    success: bool
    result: Optional[Any] = None
    error: Optional[str] = None
    error_type: str = "api"  # "api", "system", "validation"
    hints: List[str] = field(default_factory=list)

    @classmethod
    def ok(cls, result: Any) -> 'ExecutionResult':
        """Create successful result."""
        return cls(success=True, result=result)

    @classmethod
    def fail(cls, error: str, error_type: str = "api") -> 'ExecutionResult':
        """Create failed result."""
        return cls(success=False, error=error, error_type=error_type)


class PipelineStage(ABC):
    """Base class for all pipeline stages."""

    @property
    def name(self) -> str:
        """Stage name for logging."""
        return self.__class__.__name__


class Preprocessor(PipelineStage):
    """
    Prepares request before execution.

    Preprocessors modify ctx.model in-place to normalize, validate,
    or enrich the request before it's sent to the API.
    """

    @abstractmethod
    def can_process(self, ctx: 'ToolContext') -> bool:
        """Check if this preprocessor should run for the given action."""
        pass

    @abstractmethod
    def process(self, ctx: 'ToolContext') -> None:
        """
        Process the request.

        Modifies ctx.model in-place. May also add to ctx.results
        for informational messages.
        """
        pass


class PostProcessor(PipelineStage):
    """
    Processes result after successful execution.

    PostProcessors handle side effects like updating identity state,
    syncing wiki, applying security redaction, etc.
    """

    @abstractmethod
    def can_process(self, ctx: 'ToolContext', result: Any) -> bool:
        """Check if this postprocessor should run for the given result."""
        pass

    @abstractmethod
    def process(self, ctx: 'ToolContext', result: Any) -> Any:
        """
        Process the result.

        May modify the result (e.g., redaction) and return the modified version.
        May also add to ctx.results for informational messages.

        Returns:
            Possibly modified result
        """
        pass
