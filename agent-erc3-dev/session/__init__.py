"""
Session management module.

Contains benchmark session runner and sequential execution logic.
"""

from .benchmark_runner import (
    BenchmarkRunner,
    run_sequential,
    run_local_tests,
)

__all__ = [
    "BenchmarkRunner",
    "run_sequential",
    "run_local_tests",
]
