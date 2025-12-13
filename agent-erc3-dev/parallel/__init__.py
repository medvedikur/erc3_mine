"""
Parallel execution support module.

Contains thread-safe utilities for parallel task execution.
"""

from .output import (
    ThreadLogCapture,
    ThreadLocalStdout,
    thread_status,
    get_console_lock,
    get_original_stdout,
    get_original_stderr,
    get_thread_stdout,
    get_thread_stderr,
    THREAD_COLORS,
)
from .executor import run_parallel, run_task_worker
from .resources import get_thread_wiki_manager, get_thread_session

__all__ = [
    # output.py
    "ThreadLogCapture",
    "ThreadLocalStdout",
    "thread_status",
    "get_console_lock",
    "get_original_stdout",
    "get_original_stderr",
    "get_thread_stdout",
    "get_thread_stderr",
    "THREAD_COLORS",
    # executor.py
    "run_parallel",
    "run_task_worker",
    # resources.py
    "get_thread_wiki_manager",
    "get_thread_session",
]
