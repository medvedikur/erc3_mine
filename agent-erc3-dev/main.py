#!/usr/bin/env python3
"""
ERC3 Agent - Main entry point.

Usage:
    python main.py                           # Sequential (1 thread)
    python main.py -threads 4                # Parallel with 4 threads
    python main.py -threads 2 -task task1,task2  # Parallel with task filter
    python main.py -openrouter               # Use OpenRouter instead of Gonka
    python main.py -threads 4 -verbose       # Parallel with real-time output
    python main.py -tests_on                 # Run local tests instead of benchmark
    python main.py -tests_on -threads 4      # Run tests in parallel

Output modes (parallel):
    - Default: Per-thread log files + summary in console
    - Use -verbose for interleaved console output (messy but real-time)
"""

import textwrap
import os
import sys
import logging
import argparse
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Configure logging to suppress noisy httpx/httpcore logs from OpenAI client
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Ensure we can import erc3 and pricing from parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Parse command line arguments FIRST (before loading env)
parser = argparse.ArgumentParser(description='ERC3 Agent')
parser.add_argument('-openrouter', '--openrouter', action='store_true',
                    help='Use OpenRouter API instead of Gonka Network')
parser.add_argument('-task', '--task', type=str, default=None,
                    help='Filter to run only specific task spec_id (comma-separated)')
parser.add_argument('-threads', '--threads', type=int, default=1,
                    help='Number of parallel threads (default: 1 = sequential)')
parser.add_argument('-verbose', '--verbose', action='store_true',
                    help='Show all output in console (for parallel: interleaved but real-time)')
parser.add_argument('-tests_on', '--tests_on', action='store_true',
                    help='Run local tests instead of benchmark tasks')
args = parser.parse_args()

# Load environment variables
env_path = os.path.join(os.path.dirname(__file__), '..', 'sgr-agent-store', '.env')
loaded = load_dotenv(env_path)

if not loaded:
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    loaded = load_dotenv(env_path)

# Also try local .env
local_env = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(local_env):
    load_dotenv(local_env, override=True)

from erc3 import ERC3
from erc3.core import TaskInfo
from pricing import calculator
from agent import run_agent
from stats import SessionStats, failure_logger
from handlers.wiki import WikiManager, get_embedding_model

# Configuration
USE_OPENROUTER = args.openrouter
NUM_THREADS = args.threads
VERBOSE_MODE = args.verbose
PARALLEL_MODE = NUM_THREADS > 1

from utils import CLI

# Colors for thread identification (parallel mode)
THREAD_COLORS = [
    CLI.CYAN,
    "\x1B[35m",  # Magenta (not in CLI class yet)
    CLI.YELLOW,
    CLI.GREEN,
    CLI.BLUE,
    "\x1B[91m",  # Light Red
    "\x1B[92m",  # Light Green
    "\x1B[93m",  # Light Yellow
]
CLI_CLR = CLI.RESET

# Create parallel logs directory (only if parallel mode)
PARALLEL_LOGS_DIR = None
if PARALLEL_MODE:
    PARALLEL_LOGS_DIR = Path(__file__).parent / "logs" / f"parallel_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


# ============================================================================
# Parallel execution support classes
# ============================================================================

class ThreadLogCapture:
    """
    Captures output for a specific task and writes to a file.
    Used in parallel mode to separate output from different threads.
    """
    def __init__(self, spec_id: str, thread_id: int, log_dir: Path, verbose: bool = False):
        self.spec_id = spec_id
        self.thread_id = thread_id
        self.verbose = verbose
        self.color = THREAD_COLORS[thread_id % len(THREAD_COLORS)]
        self.prefix = f"{self.color}[T{thread_id}:{spec_id[:20]}]{CLI_CLR}"
        self._closed = False

        # Create log file
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / f"{spec_id}.log"
        self.log_file = open(self.log_path, 'w', encoding='utf-8')

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
        if not self._closed:
            self.log_file.flush()

    def close(self):
        if not self._closed:
            self._closed = True
            self.log_file.close()


class ThreadLocalStdout:
    """
    A stdout replacement that routes output to thread-local log captures.

    When a thread registers a log_capture, all its print() calls go there.
    When no capture is registered (main thread), output goes to original stdout.
    """
    def __init__(self, original_stdout):
        self._original = original_stdout
        self._local = threading.local()

    def register(self, log_capture: ThreadLogCapture):
        """Register a log capture for the current thread."""
        self._local.capture = log_capture

    def unregister(self):
        """Unregister log capture for the current thread."""
        self._local.capture = None

    def write(self, text: str):
        capture = getattr(self._local, 'capture', None)
        if capture and not capture._closed:
            capture.write(text)
        else:
            self._original.write(text)

    def flush(self):
        capture = getattr(self._local, 'capture', None)
        if capture and not capture._closed:
            capture.flush()
        else:
            self._original.flush()

    # Forward other attributes to original stdout
    def __getattr__(self, name):
        return getattr(self._original, name)


# Global console lock for status messages
_console_lock = threading.Lock()
# Save original stdout/stderr BEFORE any redirection
_original_stdout = sys.stdout
_original_stderr = sys.stderr

# Create thread-local stdout/stderr dispatchers (for parallel mode)
_thread_stdout = ThreadLocalStdout(_original_stdout)
_thread_stderr = ThreadLocalStdout(_original_stderr)


def thread_status(thread_id: int, spec_id: str, message: str):
    """Print a thread status message to console (always to real stdout)."""
    color = THREAD_COLORS[thread_id % len(THREAD_COLORS)]
    prefix = f"{color}[T{thread_id}:{spec_id[:15]:15}]{CLI_CLR}"
    with _console_lock:
        _original_stdout.write(f"{prefix} {message}\n")
        _original_stdout.flush()


# ============================================================================
# Model configuration
# ============================================================================

if USE_OPENROUTER:
    MODEL_ID = os.environ.get("MODEL_ID_OPENROUTER", "openai/gpt-4o-mini")
    PRICING_MODEL_ID = os.environ.get("PRICING_MODEL_ID_OPENROUTER") or os.environ.get("PRICING_MODEL_ID") or MODEL_ID
    BACKEND = "openrouter"

    if not os.environ.get("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY not found in environment!")
        print("   Set it in .env: OPENAI_API_KEY=sk-or-...")
        print("   Also set: OPENAI_BASE_URL=https://openrouter.ai/api/v1")
        sys.exit(1)
else:
    MODEL_ID = os.environ.get("MODEL_ID_GONKA", "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8")
    PRICING_MODEL_ID = os.environ.get("PRICING_MODEL_ID", "qwen/qwen3-235b-a22b-2507")
    BACKEND = "gonka"

    if not os.environ.get("GONKA_PRIVATE_KEY"):
        print("⚠️ GONKA_PRIVATE_KEY not found! LLM calls might fail if not using a public node.")

ERC3_API_KEY = os.environ.get("ERC3_API_KEY")
if not ERC3_API_KEY:
    print("❌ ERC3_API_KEY not found in environment!")
    print("   Set it in .env: ERC3_API_KEY=key-...")
    sys.exit(1)


def verify_pricing_model(model_id: str) -> str:
    """Verify pricing model exists in OpenRouter or fallback"""
    try:
        test_cost = calculator.calculate_cost(model_id, 1000, 1000)
        if test_cost > 0:
            return model_id
    except:
        pass

    fallback_models = ["qwen/qwen3-235b-a22b", "qwen/qwen3-235b-a22b:free", "qwen/qwen-2.5-72b-instruct"]
    for fallback in fallback_models:
        try:
            cost = calculator.calculate_cost(fallback, 1000, 1000)
            if cost > 0:
                print(f"⚠ Primary model {model_id} not found in pricing, using fallback: {fallback}")
                return fallback
        except:
            continue

    print(f"⚠ No pricing model found, cost will be $0")
    return model_id


PRICING_MODEL_ID = verify_pricing_model(PRICING_MODEL_ID)


# ============================================================================
# Thread-local resources (for parallel mode)
# ============================================================================

thread_local = threading.local()

def get_thread_wiki_manager() -> WikiManager:
    """
    Get or create a WikiManager for the current thread.

    Each thread needs its own WikiManager because:
    - WikiManager has mutable state (current_sha1, pages, chunks, embeddings)
    - Two tasks running in parallel might have different wiki versions
    - If they share WikiManager, sync() calls would overwrite each other's state

    However, the DISK CACHE is shared and thread-safe:
    - WikiVersionStore saves each version to wiki_dump/{sha1}/
    - Multiple threads reading the same sha1 just read the same files
    - Multiple threads downloading different sha1s write to different dirs
    """
    if not hasattr(thread_local, 'wiki_manager'):
        thread_local.wiki_manager = WikiManager()
    return thread_local.wiki_manager


def get_thread_session() -> requests.Session:
    """Get or create a requests.Session for the current thread."""
    if not hasattr(thread_local, 'session'):
        thread_local.session = requests.Session()
    return thread_local.session


# ============================================================================
# Parallel task worker
# ============================================================================

def run_task_worker(
    task: TaskInfo,
    stats: SessionStats,
    base_url: str,
    model_id: str,
    pricing_model: str,
    backend: str,
    thread_id: int
) -> dict:
    """
    Worker function to run a single task in a thread pool.

    Thread-safety approach:
    - HTTP Session: thread-local (requests.Session is not thread-safe)
    - WikiManager: thread-local (has mutable in-memory state)
    - SessionStats: shared, thread-safe via threading.Lock
    - failure_logger: shared, thread-safe via threading.Lock
    - Disk cache (wiki_dump/): shared, safe for reads (immutable per sha1)
    """
    spec_id = task.spec_id
    result = {
        'task_id': task.task_id,
        'spec_id': spec_id,
        'score': None,
        'error': None,
        'thread_id': thread_id
    }

    # Setup log capture
    log_capture = ThreadLogCapture(spec_id, thread_id, PARALLEL_LOGS_DIR, verbose=VERBOSE_MODE)

    try:
        thread_status(thread_id, spec_id, "🚀 Starting...")

        # Create thread-local ERC3 client with its own session
        session = get_thread_session()
        core = ERC3(key=ERC3_API_KEY, base_url=base_url, session=session)

        # Get thread-local WikiManager
        wiki_manager = get_thread_wiki_manager()

        # Start tracking
        stats.start_task(task.task_id, spec_id)
        failure_logger.start_task(task.task_id, task.task_text, spec_id)

        # Start task on server
        core.start_task(task)

        thread_status(thread_id, spec_id, "⚙️  Running agent...")

        # Register log capture for this thread
        _thread_stdout.register(log_capture)
        _thread_stderr.register(log_capture)

        try:
            run_agent(
                model_name=model_id,
                api=core,
                task=task,
                stats=stats,
                pricing_model=pricing_model,
                failure_logger=failure_logger,
                wiki_manager=wiki_manager,
                backend=backend
            )
        finally:
            _thread_stdout.unregister()
            _thread_stderr.unregister()

        # Complete task and get score
        task_result = core.complete_task(task)

        if task_result.eval:
            result['score'] = task_result.eval.score
            failure_logger.save_failure(task.task_id, task_result.eval.score, task_result.eval.logs)

            # Write eval to log file
            log_capture.write(f"\n{'='*60}\n")
            log_capture.write(f"SCORE: {task_result.eval.score}\n")
            log_capture.write(f"{task_result.eval.logs}\n")

            # Status to console
            score_icon = "✅" if task_result.eval.score == 1.0 else "⚠️" if task_result.eval.score > 0 else "❌"
            thread_status(thread_id, spec_id, f"{score_icon} Done! Score: {task_result.eval.score}")
        else:
            thread_status(thread_id, spec_id, "✅ Done (no eval)")

        stats.finish_task(task.task_id, result['score'])

    except Exception as e:
        _thread_stdout.unregister()
        _thread_stderr.unregister()

        result['error'] = str(e)
        thread_status(thread_id, spec_id, f"❌ ERROR: {str(e)[:50]}")

        import traceback
        try:
            log_capture.write(f"\n{'='*60}\n")
            log_capture.write(f"ERROR: {e}\n")
            log_capture.write(traceback.format_exc())
        except (ValueError, AttributeError):
            pass

    finally:
        try:
            log_capture.close()
        except Exception:
            pass

    return result


# ============================================================================
# Sequential execution (single thread)
# ============================================================================

def run_sequential(core: ERC3, tasks_to_run: list, stats: SessionStats, wiki_manager: WikiManager):
    """Run tasks sequentially (original behavior)."""
    for task in tasks_to_run:
        print("=" * 40)
        print(f"Starting Task: {task.task_id} ({task.spec_id}): {task.task_text}")

        stats.start_task(task.task_id, task.spec_id)
        failure_logger.start_task(task.task_id, task.task_text, task.spec_id)

        core.start_task(task)
        try:
            run_agent(
                model_name=MODEL_ID,
                api=core,
                task=task,
                stats=stats,
                pricing_model=PRICING_MODEL_ID,
                failure_logger=failure_logger,
                wiki_manager=wiki_manager,
                backend=BACKEND
            )
        except Exception as e:
            print(f"Fatal error in agent: {e}")
            import traceback
            traceback.print_exc()

        result = core.complete_task(task)
        score = None
        if result.eval:
            score = result.eval.score
            explain = textwrap.indent(result.eval.logs, "  ")
            print(f"\nSCORE: {result.eval.score}\n{explain}\n")
            failure_logger.save_failure(task.task_id, result.eval.score, result.eval.logs)
        else:
            print(f"\nTask Completed (Status: {result.status})\n")

        stats.finish_task(task.task_id, score)


# ============================================================================
# Parallel execution (multiple threads)
# ============================================================================

def run_parallel(base_url: str, tasks_to_run: list, stats: SessionStats):
    """Run tasks in parallel using ThreadPoolExecutor."""
    # Install thread-local stdout/stderr dispatchers
    sys.stdout = _thread_stdout
    sys.stderr = _thread_stderr

    # Pre-initialize embedding model in main thread (avoids race condition on GPU)
    get_embedding_model()

    print(f"\n🔀 Running {len(tasks_to_run)} tasks with {NUM_THREADS} threads...\n")

    results = []
    with ThreadPoolExecutor(max_workers=NUM_THREADS, thread_name_prefix="Worker") as executor:
        futures = {
            executor.submit(
                run_task_worker,
                task,
                stats,
                base_url,
                MODEL_ID,
                PRICING_MODEL_ID,
                BACKEND,
                idx % NUM_THREADS
            ): task
            for idx, task in enumerate(tasks_to_run)
        }

        for future in as_completed(futures):
            task = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                print(f"Task {task.spec_id} raised exception: {e}")
                results.append({
                    'task_id': task.task_id,
                    'spec_id': task.spec_id,
                    'score': None,
                    'error': str(e)
                })

    # Print parallel execution summary
    print("\n📊 PARALLEL EXECUTION SUMMARY")
    print("-" * 40)
    successful = [r for r in results if r['error'] is None]
    failed = [r for r in results if r['error'] is not None]
    perfect = [r for r in successful if r['score'] == 1.0]

    print(f"  Total tasks:     {len(results)}")
    print(f"  Successful:      {len(successful)}")
    print(f"  Failed (error):  {len(failed)}")
    print(f"  Perfect score:   {len(perfect)}")
    print(f"  Max concurrency: {stats.max_concurrent_tasks}")

    if failed:
        print("\n  ❌ Failed tasks:")
        for r in failed:
            print(f"     - {r['spec_id']}: {r['error'][:50]}...")

    print(f"\n📁 Detailed logs: {PARALLEL_LOGS_DIR}/")
    print(f"   View specific task: cat {PARALLEL_LOGS_DIR}/<spec_id>.log")


# ============================================================================
# Local test runner
# ============================================================================

def run_local_tests():
    """Run local tests instead of benchmark tasks."""
    from tests.framework.test_runner import run_tests

    print(f"""
╔════════════════════════════════════════════════════════════════════╗
║  🧪 ERC3 LOCAL TEST MODE                                            ║
║  Model: {MODEL_ID:<52} ║
║  Pricing: {PRICING_MODEL_ID:<50} ║
╚════════════════════════════════════════════════════════════════════╝
""")

    run_tests(
        parallel=PARALLEL_MODE,
        num_threads=NUM_THREADS,
        task_filter=args.task,
        model_id=MODEL_ID,
        backend=BACKEND,
        pricing_model=PRICING_MODEL_ID,
        wiki_dump_dir="wiki_dump_tests",
        logs_dir="logs_tests",
        verbose=VERBOSE_MODE,
        max_turns=20,
    )


# ============================================================================
# Main entry point
# ============================================================================

def main():
    # Check if running local tests
    if args.tests_on:
        run_local_tests()
        return

    # Print banner
    backend_emoji = "🌐" if USE_OPENROUTER else "🚀"
    backend_name = "OpenRouter" if USE_OPENROUTER else "Gonka Network"
    mode_str = f"PARALLEL ({NUM_THREADS} threads)" if PARALLEL_MODE else "Sequential"

    print(f"""
╔════════════════════════════════════════════════════════════════════╗
║  {backend_emoji} ERC3-TEST Agent - {backend_name:<19} ({mode_str:<18}) ║
║  Model: {MODEL_ID:<52} ║
║  Pricing: {PRICING_MODEL_ID:<50} ║
╚════════════════════════════════════════════════════════════════════╝
""")

    base_url = "https://erc.timetoact-group.at"
    core = ERC3(key=ERC3_API_KEY, base_url=base_url)

    # Start session
    architecture_desc = f"SGR Agent {'Parallel ' if PARALLEL_MODE else ''}({backend_name} {MODEL_ID})"
    res = core.start_session(
        benchmark="erc3-test",
        workspace="test-workspace-1",
        name=f"@mishka ERC3-Test Agent{f' (Parallel x{NUM_THREADS})' if PARALLEL_MODE else ''}",
        architecture=architecture_desc,
        flags=["compete_accuracy", "compete_budget", "compete_speed", "compete_local"],
    )

    status = core.session_status(res.session_id)
    print(f"Session {res.session_id} has {len(status.tasks)} tasks")

    # Initialize stats
    stats = SessionStats()

    # Filter tasks if specified
    tasks_to_run = status.tasks
    if args.task:
        task_filters = [t.strip() for t in args.task.split(',')]
        tasks_to_run = [t for t in status.tasks if t.spec_id in task_filters]
        print(f"🎯 Filtered to {len(tasks_to_run)} task(s): {', '.join(task_filters)}")
        if PARALLEL_MODE:
            print(f"⚠️  WARNING: {len(status.tasks) - len(tasks_to_run)} tasks will NOT be executed!")

    if not tasks_to_run:
        print("No tasks to run!")
        return

    # Run tasks
    if PARALLEL_MODE:
        run_parallel(base_url, tasks_to_run, stats)
    else:
        wiki_manager = WikiManager()
        run_sequential(core, tasks_to_run, stats, wiki_manager)

    # Submit session
    print("\n" + "=" * 60)
    # Use force=True if tasks were filtered (some remain unfinished)
    force_submit = args.task is not None
    core.submit_session(res.session_id, force=force_submit)

    # Print reports
    stats.print_report()
    failure_logger.print_summary()


if __name__ == "__main__":
    main()
