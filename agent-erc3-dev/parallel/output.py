"""
Thread-local output capture for parallel execution.

Provides utilities for capturing and routing stdout/stderr to
per-task log files in parallel mode.
"""

import sys
import threading
from pathlib import Path
from typing import Optional

from utils import CLI, CLI_CLR


# Colors for thread identification
THREAD_COLORS = [
    CLI.CYAN,
    "\x1B[35m",  # Magenta
    CLI.YELLOW,
    CLI.GREEN,
    CLI.BLUE,
    "\x1B[91m",  # Light Red
    "\x1B[92m",  # Light Green
    "\x1B[93m",  # Light Yellow
]

# Global console lock for status messages
_console_lock = threading.Lock()

# Save original stdout/stderr BEFORE any redirection
_original_stdout = sys.stdout
_original_stderr = sys.stderr


class ThreadLogCapture:
    """
    Captures output for a specific task and writes to a file.

    Used in parallel mode to separate output from different threads.
    Each task gets its own log file for debugging and analysis.
    """

    def __init__(
        self,
        spec_id: str,
        thread_id: int,
        log_dir: Path,
        verbose: bool = False,
        task_id: str = None,
        task_text: str = None,
    ):
        """
        Initialize log capture for a task.

        Args:
            spec_id: Task specification ID (used in filename)
            thread_id: Thread index for color coding
            log_dir: Directory to write log files
            verbose: If True, also print to console with prefix
            task_id: Task ID from benchmark
            task_text: Original task question/request
        """
        self.spec_id = spec_id
        self.thread_id = thread_id
        self.verbose = verbose
        self.task_id = task_id
        self.task_text = task_text
        self.color = THREAD_COLORS[thread_id % len(THREAD_COLORS)]
        self.prefix = f"{self.color}[T{thread_id}:{spec_id[:20]}]{CLI_CLR}"
        self._closed = False

        # Create log file
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / f"{spec_id}.log"
        self.log_file = open(self.log_path, 'w', encoding='utf-8')

        # Write header with task context
        self._write_header()

    def _write_header(self):
        """Write task context header at the start of the log file."""
        header = "═" * 60 + "\n"
        header += "TASK CONTEXT\n"
        header += "═" * 60 + "\n"
        if self.task_id:
            header += f"Task ID:  {self.task_id}\n"
        header += f"Spec ID:  {self.spec_id}\n"
        if self.task_text:
            header += f"Question: {self.task_text}\n"
        header += "═" * 60 + "\n\n"
        self.log_file.write(header)
        self.log_file.flush()

    def write(self, text: str):
        """Write to log file, optionally to console."""
        if self._closed or not text.strip():
            return

        # Always write to file
        self.log_file.write(text)
        self.log_file.flush()

        # In verbose mode, also print to console with prefix
        if self.verbose:
            with _console_lock:
                for line in text.splitlines():
                    if line.strip():
                        _original_stdout.write(f"{self.prefix} {line}\n")
                _original_stdout.flush()

    def flush(self):
        """Flush the log file buffer."""
        if not self._closed:
            self.log_file.flush()

    def close(self):
        """Close the log file."""
        if not self._closed:
            self._closed = True
            self.log_file.close()


class ThreadLocalStdout:
    """
    A stdout replacement that routes output to thread-local log captures.

    When a thread registers a log_capture, all its print() calls go there.
    When no capture is registered (main thread), output goes to original stdout.

    Thread-safety: Each thread has its own capture via threading.local().
    """

    def __init__(self, original_stdout):
        """
        Initialize with the original stdout to fall back to.

        Args:
            original_stdout: The real sys.stdout to use when no capture is registered
        """
        self._original = original_stdout
        self._local = threading.local()

    def register(self, log_capture: ThreadLogCapture):
        """Register a log capture for the current thread."""
        self._local.capture = log_capture

    def unregister(self):
        """Unregister log capture for the current thread."""
        self._local.capture = None

    def write(self, text: str):
        """Write to the appropriate destination."""
        capture = getattr(self._local, 'capture', None)
        if capture and not capture._closed:
            capture.write(text)
        else:
            self._original.write(text)

    def flush(self):
        """Flush the appropriate destination."""
        capture = getattr(self._local, 'capture', None)
        if capture and not capture._closed:
            capture.flush()
        else:
            self._original.flush()

    def __getattr__(self, name):
        """Forward other attributes to original stdout."""
        return getattr(self._original, name)


# Create thread-local stdout/stderr dispatchers
_thread_stdout = ThreadLocalStdout(_original_stdout)
_thread_stderr = ThreadLocalStdout(_original_stderr)


def thread_status(thread_id: int, spec_id: str, message: str):
    """
    Print a thread status message to console (always to real stdout).

    Used for progress reporting in parallel mode - these messages
    bypass the thread-local capture and go directly to console.
    """
    color = THREAD_COLORS[thread_id % len(THREAD_COLORS)]
    prefix = f"{color}[T{thread_id}:{spec_id[:15]:15}]{CLI_CLR}"
    with _console_lock:
        _original_stdout.write(f"{prefix} {message}\n")
        _original_stdout.flush()


def get_console_lock() -> threading.Lock:
    """Get the console lock for synchronized output."""
    return _console_lock


def get_original_stdout():
    """Get the original stdout (before any redirection)."""
    return _original_stdout


def get_original_stderr():
    """Get the original stderr (before any redirection)."""
    return _original_stderr


def get_thread_stdout() -> ThreadLocalStdout:
    """Get the thread-local stdout dispatcher."""
    return _thread_stdout


def get_thread_stderr() -> ThreadLocalStdout:
    """Get the thread-local stderr dispatcher."""
    return _thread_stderr
