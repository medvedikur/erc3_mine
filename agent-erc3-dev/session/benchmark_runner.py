"""
Benchmark session runner.

Handles starting sessions, running tasks, and submitting results.
Supports both sequential and parallel execution modes.
"""

import textwrap
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from erc3 import ERC3
from erc3.core import TaskInfo

from agent.runner import run_agent
from stats import SessionStats, failure_logger
from handlers.wiki import WikiManager


class BenchmarkRunner:
    """
    Orchestrates benchmark session execution.

    Handles:
    - Session lifecycle (start, run tasks, submit)
    - Task filtering
    - Sequential or parallel execution
    - Result reporting
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model_id: str,
        pricing_model: str,
        backend: str,
        benchmark_type: str,
        workspace: str,
        session_name: str,
        competition_flags: List[str],
    ):
        """
        Initialize the benchmark runner.

        Args:
            api_key: ERC3 API key
            base_url: ERC3 API base URL
            model_id: LLM model identifier
            pricing_model: Model ID for pricing calculations
            backend: LLM backend ("gonka", "openrouter", etc.)
            benchmark_type: Benchmark type (erc3-test, erc3-dev, erc3)
            workspace: Workspace name
            session_name: Session name for reporting
            competition_flags: Competition flags
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model_id = model_id
        self.pricing_model = pricing_model
        self.backend = backend
        self.benchmark_type = benchmark_type
        self.workspace = workspace
        self.session_name = session_name
        self.competition_flags = competition_flags

        self.core: Optional[ERC3] = None
        self.session_id: Optional[str] = None
        self.stats = SessionStats()

    def start_session(self, num_threads: int = 1) -> List[TaskInfo]:
        """
        Start a benchmark session.

        Args:
            num_threads: Number of threads for parallel mode

        Returns:
            List of tasks in the session
        """
        self.core = ERC3(key=self.api_key, base_url=self.base_url)

        # Build session description
        parallel_suffix = f" (Parallel x{num_threads})" if num_threads > 1 else ""
        architecture_desc = f"SGR Agent {'Parallel ' if num_threads > 1 else ''}({self.backend} {self.model_id})"
        session_name = f"{self.session_name}{parallel_suffix}"

        print(f"Benchmark: {self.benchmark_type}")

        res = self.core.start_session(
            benchmark=self.benchmark_type,
            workspace=self.workspace,
            name=session_name,
            architecture=architecture_desc,
            flags=self.competition_flags,
        )

        self.session_id = res.session_id
        status = self.core.session_status(res.session_id)
        print(f"Session {res.session_id} has {len(status.tasks)} tasks")

        return status.tasks

    def filter_tasks(
        self,
        tasks: List[TaskInfo],
        task_filter: Optional[str],
        parallel_mode: bool = False
    ) -> List[TaskInfo]:
        """
        Filter tasks based on spec_id filter.

        Args:
            tasks: All tasks from session
            task_filter: Comma-separated spec_ids or None
            parallel_mode: Whether running in parallel mode

        Returns:
            Filtered list of tasks
        """
        if not task_filter:
            return tasks

        task_filters = [t.strip() for t in task_filter.split(',')]
        filtered = [t for t in tasks if t.spec_id in task_filters]
        print(f"Filtered to {len(filtered)} task(s): {', '.join(task_filters)}")

        if parallel_mode and len(filtered) < len(tasks):
            print(f" WARNING: {len(tasks) - len(filtered)} tasks will NOT be executed!")

        return filtered

    def run_sequential(self, tasks: List[TaskInfo]):
        """
        Run tasks sequentially (single thread).

        Args:
            tasks: Tasks to execute
        """
        wiki_manager = WikiManager()
        run_sequential(
            self.core,
            tasks,
            self.stats,
            wiki_manager,
            self.model_id,
            self.pricing_model,
            self.backend,
        )

    def run_parallel(
        self,
        tasks: List[TaskInfo],
        num_threads: int,
        verbose: bool = False
    ):
        """
        Run tasks in parallel.

        Args:
            tasks: Tasks to execute
            num_threads: Number of threads
            verbose: Whether to show real-time output
        """
        from parallel import run_parallel

        logs_dir = Path(__file__).parent.parent / "logs" / f"parallel_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        run_parallel(
            base_url=self.base_url,
            tasks_to_run=tasks,
            stats=self.stats,
            num_threads=num_threads,
            model_id=self.model_id,
            pricing_model=self.pricing_model,
            backend=self.backend,
            api_key=self.api_key,
            logs_dir=logs_dir,
            verbose=verbose,
        )

    def submit_session(self, force: bool = False):
        """
        Submit the session and print reports.

        Args:
            force: Force submit even if some tasks weren't completed
        """
        print("\n" + "=" * 60)
        self.core.submit_session(self.session_id, force=force)

        self.stats.print_report()
        failure_logger.print_summary()


def run_sequential(
    core: ERC3,
    tasks: List[TaskInfo],
    stats: SessionStats,
    wiki_manager: WikiManager,
    model_id: str,
    pricing_model: str,
    backend: str,
):
    """
    Run tasks sequentially (original behavior).

    Args:
        core: ERC3 API client
        tasks: Tasks to execute
        stats: Session statistics tracker
        wiki_manager: Wiki manager instance
        model_id: LLM model identifier
        pricing_model: Model ID for pricing
        backend: LLM backend name
    """
    for task in tasks:
        print("=" * 40)
        print(f"Starting Task: {task.task_id} ({task.spec_id}): {task.task_text}")

        stats.start_task(task.task_id, task.spec_id)
        failure_logger.start_task(task.task_id, task.task_text, task.spec_id)

        core.start_task(task)
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


def run_local_tests(
    model_id: str,
    pricing_model: str,
    backend: str,
    parallel: bool = False,
    num_threads: int = 1,
    task_filter: Optional[str] = None,
    verbose: bool = False,
    max_turns: int = 25,
):
    """
    Run local tests instead of benchmark tasks.

    Args:
        model_id: LLM model identifier
        pricing_model: Model ID for pricing
        backend: LLM backend name
        parallel: Whether to run in parallel
        num_threads: Number of threads for parallel mode
        task_filter: Task spec_id filter
        verbose: Whether to show real-time output
        max_turns: Maximum turns per task
    """
    from tests.framework.test_runner import run_tests

    print(f"""
=======================================================================
  ERC3 LOCAL TEST MODE
  Model: {model_id:<52}
  Pricing: {pricing_model:<50}
=======================================================================
""")

    run_tests(
        parallel=parallel,
        num_threads=num_threads,
        task_filter=task_filter,
        model_id=model_id,
        backend=backend,
        pricing_model=pricing_model,
        wiki_dump_dir="wiki_dump_tests",
        logs_dir="logs_tests",
        verbose=verbose,
        max_turns=max_turns,
    )
