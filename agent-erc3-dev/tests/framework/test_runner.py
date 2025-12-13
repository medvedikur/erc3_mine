"""
Test runner for local testing.

Runs test scenarios against the agent with mock API.
Supports parallel execution and detailed logging.
"""

import os
import sys
import importlib
import threading
import traceback
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Dict, Any

from .task_builder import TestScenario
from .mock_api import MockErc3Client
from .evaluator import TestEvaluator, TestResult, TestSuiteResult


# Thread-local storage for test context
_test_local = threading.local()

from utils import CLI_RED, CLI_GREEN, CLI_YELLOW, CLI_BLUE, CLI_CYAN, CLI_CLR

# Thread colors for status messages
THREAD_COLORS = [
    CLI_CYAN,
    "\x1B[35m",  # Magenta
    CLI_YELLOW,
    CLI_GREEN,
    CLI_BLUE,
]


class ThreadLogCapture:
    """Captures output for a single thread and writes to a log file."""

    def __init__(self, spec_id: str, thread_id: int, logs_dir: Path):
        self.spec_id = spec_id
        self.thread_id = thread_id
        self.log_file = logs_dir / f"{spec_id}.log"
        self._file = open(self.log_file, 'w', buffering=1)
        self._closed = False

    def write(self, text: str):
        if not self._closed and self._file:
            self._file.write(text)

    def flush(self):
        if not self._closed and self._file:
            self._file.flush()

    def close(self):
        if not self._closed:
            self._closed = True
            if self._file:
                self._file.close()


class ThreadLocalStdout:
    """Routes output to thread-local log captures."""

    def __init__(self, original_stdout):
        self._original = original_stdout
        self._local = threading.local()

    def register(self, log_capture: ThreadLogCapture):
        self._local.capture = log_capture

    def unregister(self):
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

    def __getattr__(self, name):
        return getattr(self._original, name)


# Global stdout/stderr dispatchers for parallel mode
_original_stdout = sys.stdout
_original_stderr = sys.stderr
_thread_stdout = None
_thread_stderr = None


def _init_thread_output():
    """Initialize thread-local stdout/stderr dispatchers."""
    global _thread_stdout, _thread_stderr
    if _thread_stdout is None:
        _thread_stdout = ThreadLocalStdout(_original_stdout)
        _thread_stderr = ThreadLocalStdout(_original_stderr)


def thread_status(thread_id: int, spec_id: str, message: str):
    """Print a thread status message to console (always to real stdout)."""
    color = THREAD_COLORS[thread_id % len(THREAD_COLORS)]
    _original_stdout.write(f"{color}[T{thread_id}:{spec_id}]{CLI_CLR} {message}\n")
    _original_stdout.flush()


def discover_tests(tests_dir: Path) -> List[TestScenario]:
    """
    Discover all test scenarios in the cases/ directory.

    Each test file should have a SCENARIO variable containing a TestScenario.
    """
    cases_dir = tests_dir / "cases"
    if not cases_dir.exists():
        print(f"Warning: cases directory not found: {cases_dir}")
        return []

    scenarios = []

    # Add tests directory to path for imports
    sys.path.insert(0, str(tests_dir.parent))

    for test_file in sorted(cases_dir.glob("test_*.py")):
        try:
            # Import the test module
            module_name = test_file.stem
            spec = importlib.util.spec_from_file_location(module_name, test_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Get SCENARIO from module
            if hasattr(module, 'SCENARIO'):
                scenario = module.SCENARIO
                if isinstance(scenario, TestScenario):
                    scenarios.append(scenario)
                    print(f"  Found: {scenario.spec_id}")
                elif isinstance(scenario, list):
                    # Multiple scenarios in one file
                    for s in scenario:
                        if isinstance(s, TestScenario):
                            scenarios.append(s)
                            print(f"  Found: {s.spec_id}")
            else:
                print(f"  Warning: {test_file.name} has no SCENARIO")

        except Exception as e:
            print(f"  Error loading {test_file.name}: {e}")
            traceback.print_exc()

    return scenarios


def run_single_test(
    scenario: TestScenario,
    model_id: str,
    wiki_dump_dir: str,
    backend: str = "openrouter",
    pricing_model: str = None,
    max_turns: int = None,
    verbose: bool = False,
) -> TestResult:
    """
    Run a single test scenario.

    Args:
        scenario: Test scenario to run
        model_id: LLM model ID
        wiki_dump_dir: Path to wiki dump directory
        backend: LLM backend (gonka/openrouter)
        pricing_model: Pricing model ID
        max_turns: Maximum agent turns
        verbose: Print detailed output

    Returns:
        TestResult with evaluation
    """
    from agent.runner import run_agent
    from stats import SessionStats, FailureLogger
    from handlers.wiki import WikiManager, WikiVersionStore

    # Clear wiki cache to ensure fresh data from disk
    WikiVersionStore.clear_cache()

    # Create mock API client
    mock_client = MockErc3Client(scenario)

    # Create minimal task info
    class MockTaskInfo:
        def __init__(self, scenario: TestScenario):
            self.task_id = f"test_{scenario.spec_id}"
            self.spec_id = scenario.spec_id
            self.task_text = scenario.task_text

    task = MockTaskInfo(scenario)

    # Create stats (not used for scoring, just for tracking)
    stats = SessionStats()
    failure_logger = FailureLogger()
    failure_logger.start_task(task.task_id, task.task_text, task.spec_id)

    # Create WikiManager pointing to test wiki
    wiki_manager = WikiManager(base_dir=wiki_dump_dir)

    # Track turns
    turns_used = 0
    error = None

    try:
        if verbose:
            print(f"\n{CLI_BLUE}Running: {scenario.spec_id}{CLI_CLR}")
            print(f"  Task: {scenario.task_text[:80]}...")
            print(f"  User: {scenario.identity.user or 'GUEST'}")

        # Run agent with mock client
        run_agent(
            model_name=model_id,
            api=mock_client,
            task=task,
            stats=stats,
            pricing_model=pricing_model or model_id,
            failure_logger=failure_logger,
            wiki_manager=wiki_manager,
            backend=backend,
            max_turns=max_turns,
        )

        turns_used = len([c for c in mock_client.call_log])

    except Exception as e:
        error = str(e)
        if verbose:
            print(f"  {CLI_RED}Error: {error}{CLI_CLR}")
            traceback.print_exc()

    # Evaluate result
    evaluator = TestEvaluator()
    result = evaluator.evaluate(
        scenario=scenario,
        agent_response=mock_client.final_response,
        api_calls=mock_client.call_log,
        turns_used=turns_used,
        error=error,
    )

    # Attach context_results from failure_logger to result for debugging
    task_data = failure_logger.conversation_logs.get(task.task_id, {})
    result.context_results = task_data.get('context_results', [])

    if verbose:
        status_color = CLI_GREEN if result.passed else CLI_RED
        print(f"  {status_color}Score: {result.score:.1f}{CLI_CLR}")

    return result


def run_tests(
    parallel: bool = False,
    num_threads: int = 4,
    task_filter: Optional[str] = None,
    model_id: str = None,
    backend: str = "openrouter",
    pricing_model: str = None,
    wiki_dump_dir: str = "wiki_dump_tests",
    logs_dir: str = "logs_tests",
    verbose: bool = False,
    max_turns: int = None,
) -> TestSuiteResult:
    """
    Run all discovered tests.

    Args:
        parallel: Run tests in parallel
        num_threads: Number of parallel threads
        task_filter: Comma-separated list of spec_ids to run
        model_id: LLM model ID
        backend: LLM backend
        pricing_model: Pricing model ID
        wiki_dump_dir: Path to wiki dump
        logs_dir: Path for logs
        verbose: Verbose output
        max_turns: Max turns per test

    Returns:
        TestSuiteResult with all results
    """
    # Setup paths
    tests_dir = Path(__file__).parent.parent
    project_dir = tests_dir.parent
    wiki_path = project_dir / wiki_dump_dir
    logs_path = project_dir / logs_dir

    # Create logs directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_logs_dir = logs_path / f"test_run_{timestamp}"
    run_logs_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"ERC3 LOCAL TEST RUNNER")
    print(f"{'='*60}")
    print(f"  Model: {model_id}")
    print(f"  Backend: {backend}")
    print(f"  Wiki: {wiki_path}")
    print(f"  Logs: {run_logs_dir}")
    print(f"  Mode: {'Parallel' if parallel else 'Sequential'}")
    print(f"{'='*60}")

    # Discover tests
    print(f"\nDiscovering tests...")
    scenarios = discover_tests(tests_dir)

    if not scenarios:
        print("No tests found!")
        return TestSuiteResult()

    print(f"\nFound {len(scenarios)} test(s)")

    # Filter if specified
    if task_filter:
        filters = [f.strip() for f in task_filter.split(',')]
        scenarios = [s for s in scenarios if s.spec_id in filters]
        print(f"Filtered to {len(scenarios)} test(s): {', '.join(filters)}")

    if not scenarios:
        print("No tests match filter!")
        return TestSuiteResult()

    # Run tests
    suite_result = TestSuiteResult()

    if parallel:
        suite_result = _run_parallel(
            scenarios, num_threads, model_id, backend, pricing_model,
            str(wiki_path), run_logs_dir, verbose, max_turns
        )
    else:
        suite_result = _run_sequential(
            scenarios, model_id, backend, pricing_model,
            str(wiki_path), run_logs_dir, verbose, max_turns
        )

    # Print summary
    suite_result.print_summary()

    # Save summary to file
    summary_file = run_logs_dir / "summary.txt"
    with open(summary_file, 'w') as f:
        f.write(f"Test Run: {timestamp}\n")
        f.write(f"Model: {model_id}\n")
        f.write(f"Total: {suite_result.total}\n")
        f.write(f"Passed: {suite_result.passed}\n")
        f.write(f"Failed: {suite_result.failed}\n")
        f.write(f"Errors: {suite_result.errors}\n")
        f.write(f"Average Score: {suite_result.average_score:.2f}\n")
        f.write("\n" + "="*40 + "\n\n")
        for result in suite_result.results:
            f.write(str(result) + "\n\n")

    print(f"\nResults saved to: {summary_file}")

    return suite_result


def _run_sequential(
    scenarios: List[TestScenario],
    model_id: str,
    backend: str,
    pricing_model: str,
    wiki_dump_dir: str,
    logs_dir: Path,
    verbose: bool,
    max_turns: int,
) -> TestSuiteResult:
    """Run tests sequentially."""
    suite_result = TestSuiteResult()

    for i, scenario in enumerate(scenarios, 1):
        print(f"\n[{i}/{len(scenarios)}] {scenario.spec_id}")

        result = run_single_test(
            scenario=scenario,
            model_id=model_id,
            wiki_dump_dir=wiki_dump_dir,
            backend=backend,
            pricing_model=pricing_model,
            max_turns=max_turns,
            verbose=verbose,
        )

        suite_result.add_result(result)

        # Print result
        status_icon = "PASS" if result.passed else ("ERR" if result.error else "FAIL")
        color = CLI_GREEN if result.passed else CLI_RED
        print(f"  {color}[{status_icon}]{CLI_CLR} Score: {result.score:.1f}")

        # Save individual log
        log_file = logs_dir / f"{scenario.spec_id}.log"
        with open(log_file, 'w') as f:
            f.write(str(result))

    return suite_result


def _run_parallel(
    scenarios: List[TestScenario],
    num_threads: int,
    model_id: str,
    backend: str,
    pricing_model: str,
    wiki_dump_dir: str,
    logs_dir: Path,
    verbose: bool,
    max_turns: int,
) -> TestSuiteResult:
    """Run tests in parallel with thread-local log capture."""
    suite_result = TestSuiteResult()
    lock = threading.Lock()

    # Initialize thread-local output dispatchers
    _init_thread_output()

    # Replace sys.stdout/stderr with thread-local dispatchers
    sys.stdout = _thread_stdout
    sys.stderr = _thread_stderr

    print(f"\nRunning {len(scenarios)} tests with {num_threads} threads...\n")

    def run_test(scenario: TestScenario, idx: int) -> TestResult:
        """Worker function for parallel execution."""
        thread_id = idx % num_threads

        # Create log capture for this thread/test
        log_capture = ThreadLogCapture(scenario.spec_id, thread_id, logs_dir)

        try:
            # Register log capture for this thread
            _thread_stdout.register(log_capture)
            _thread_stderr.register(log_capture)

            # Status to console
            thread_status(thread_id, scenario.spec_id, "Starting test...")

            result = run_single_test(
                scenario=scenario,
                model_id=model_id,
                wiki_dump_dir=wiki_dump_dir,
                backend=backend,
                pricing_model=pricing_model,
                max_turns=max_turns,
                verbose=True,  # Verbose goes to log file now
            )

            # Unregister before console output
            _thread_stdout.unregister()
            _thread_stderr.unregister()

            # Print status to console
            status_icon = "PASS" if result.passed else ("ERR" if result.error else "FAIL")
            color = CLI_GREEN if result.passed else CLI_RED
            thread_status(thread_id, scenario.spec_id, f"{color}[{status_icon}]{CLI_CLR} Score: {result.score:.1f}")

            # Append evaluation result to log file
            with open(log_capture.log_file, 'a') as f:
                f.write("\n\n" + "=" * 40 + "\n")
                f.write("EVALUATION RESULT\n")
                f.write("=" * 40 + "\n")
                f.write(str(result))

            return result

        except Exception as e:
            # Unregister on error
            _thread_stdout.unregister()
            _thread_stderr.unregister()

            thread_status(thread_id, scenario.spec_id, f"{CLI_RED}[ERR]{CLI_CLR} {str(e)[:50]}")

            return TestResult(
                spec_id=scenario.spec_id,
                score=0.0,
                passed=False,
                error=str(e),
            )
        finally:
            log_capture.close()

    try:
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = {
                executor.submit(run_test, scenario, idx): scenario
                for idx, scenario in enumerate(scenarios)
            }

            for future in as_completed(futures):
                result = future.result()
                with lock:
                    suite_result.add_result(result)
    finally:
        # Restore original stdout/stderr
        sys.stdout = _original_stdout
        sys.stderr = _original_stderr

    return suite_result
