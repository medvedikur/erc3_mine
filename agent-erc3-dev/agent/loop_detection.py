"""
Loop detection for agent execution.

Detects when the agent is stuck repeating the same actions
without making progress.
"""
from typing import List, Any, Tuple


class LoopDetector:
    """
    Detects repetitive action patterns that indicate the agent is stuck.

    Tracks the last N action patterns and triggers when they're identical.
    """

    def __init__(self, history_size: int = 3):
        """
        Initialize loop detector.

        Args:
            history_size: Number of patterns to track (loop triggers when
                         all patterns are identical)
        """
        self.history_size = history_size
        self.action_history: List[Tuple] = []

    def clear(self) -> None:
        """Clear action history."""
        self.action_history.clear()

    def record_and_check(self, action_queue: List[dict]) -> bool:
        """
        Record action pattern and check for loop.

        Args:
            action_queue: List of action dicts from current turn

        Returns:
            True if loop detected, False otherwise
        """
        pattern = self._make_pattern(action_queue)
        self.action_history.append(pattern)

        if len(self.action_history) > self.history_size:
            self.action_history.pop(0)

        # Check if all patterns are identical and non-empty
        if len(self.action_history) == self.history_size and pattern:
            if all(p == pattern for p in self.action_history):
                return True

        return False

    def _make_pattern(self, action_queue: List[dict]) -> Tuple:
        """
        Convert action queue to hashable pattern.

        Includes tool names AND argument values so that iterating through
        different entities doesn't look like a loop.
        """
        return tuple(
            (a.get('tool'), tuple(sorted(
                (k, self._make_hashable(v))
                for k, v in a.get('args', {}).items()
            )))
            for a in action_queue
        )

    def _make_hashable(self, value: Any) -> Any:
        """Convert value to hashable form for pattern comparison."""
        if isinstance(value, dict):
            return tuple(sorted(
                (k, self._make_hashable(v))
                for k, v in value.items()
            ))
        elif isinstance(value, list):
            return tuple(self._make_hashable(item) for item in value)
        return value
