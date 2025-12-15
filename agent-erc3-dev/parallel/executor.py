"""
Parallel task executor using ThreadPoolExecutor.

Handles the execution of benchmark tasks in parallel threads
with proper resource isolation and progress reporting.
"""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Any

from erc3 import ERC3
from erc3.core import TaskInfo

from agent.runner import run_agent
from stats import SessionStats, failure_logger
from handlers.wiki import get_embedding_model

from .output import (
    ThreadLogCapture,
    thread_status,
    get_thread_stdout,
    get_thread_stderr,
)
from .resources import get_thread_wiki_manager, get_thread_session


def _write_context_results(log_capture: ThreadLogCapture, task_id: str, failure_logger):
    """
    Write context results (hints, guards, enrichments) to the log file.

    Similar to the failure report format but included in all parallel logs
    for easier debugging and analysis.
    """
    if task_id not in failure_logger.conversation_logs:
        return

    task_data = failure_logger.conversation_logs[task_id]
    context_results = task_data.get('context_results', [])
    api_responses = task_data.get('api_responses', [])

    # Write API calls section
    if api_responses:
        log_capture.write(f"\n{'═'*60}\n")
        log_capture.write(f"API CALLS ({len(api_responses)} calls)\n")
        log_capture.write(f"{'═'*60}\n")
        for call in api_responses:
            log_capture.write(f"\n[{call['action']}]\n")
            if call.get('error'):
                log_capture.write(f"  ERROR: {call['error']}\n")
            else:
                # For ProvideAgentResponse, show request with links
                if call['action'] == 'Req_ProvideAgentResponse':
                    request_data = call.get('request', {})
                    links = request_data.get('links', [])
                    log_capture.write(f"  Links sent: {links}\n")
                response_str = str(call.get('response', {}))
                # Truncate long responses
                if len(response_str) > 500:
                    response_str = response_str[:500] + "..."
                log_capture.write(f"  Response: {response_str}\n")

    # Write context results section (hints, guards, enrichments)
    if context_results:
        log_capture.write(f"\n{'═'*60}\n")
        log_capture.write("CONTEXT RESULTS (hints/guards/enrichments)\n")
        log_capture.write(f"{'═'*60}\n")
        for ctx_result in context_results:
            log_capture.write(f"\n[{ctx_result['action']}]\n")
            for result in ctx_result.get('results', []):
                log_capture.write(f"  {result}\n")


def run_task_worker(
    task: TaskInfo,
    stats: SessionStats,
    base_url: str,
    model_id: str,
    pricing_model: str,
    backend: str,
    thread_id: int,
    api_key: str,
    logs_dir: Path,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Worker function to run a single task in a thread pool.

    Thread-safety approach:
    - HTTP Session: thread-local (requests.Session is not thread-safe)
    - WikiManager: thread-local (has mutable in-memory state)
    - SessionStats: shared, thread-safe via threading.Lock
    - failure_logger: shared, thread-safe via threading.Lock
    - Disk cache (wiki_dump/): shared, safe for reads (immutable per sha1)

    Args:
        task: Task to execute
        stats: Shared session statistics
        base_url: ERC3 API base URL
        model_id: LLM model identifier
        pricing_model: Model ID for pricing
        backend: LLM backend name
        thread_id: Thread index for color coding
        api_key: ERC3 API key
        logs_dir: Directory for task log files
        verbose: Whether to show output in console

    Returns:
        Dict with task_id, spec_id, score, error, thread_id
    """
    spec_id = task.spec_id
    result = {
        'task_id': task.task_id,
        'spec_id': spec_id,
        'score': None,
        'error': None,
        'thread_id': thread_id
    }

    # Setup log capture with task context
    log_capture = ThreadLogCapture(
        spec_id=spec_id,
        thread_id=thread_id,
        log_dir=logs_dir,
        verbose=verbose,
        task_id=task.task_id,
        task_text=task.task_text,
    )
    _thread_stdout = get_thread_stdout()
    _thread_stderr = get_thread_stderr()

    try:
        thread_status(thread_id, spec_id, "Starting...")

        # Create thread-local ERC3 client with its own session
        session = get_thread_session()
        core = ERC3(key=api_key, base_url=base_url, session=session)

        # Get thread-local WikiManager
        wiki_manager = get_thread_wiki_manager()

        # Start tracking
        stats.start_task(task.task_id, spec_id)
        failure_logger.start_task(task.task_id, task.task_text, spec_id)

        # Start task on server
        core.start_task(task)

        thread_status(thread_id, spec_id, "Running agent...")

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
            score_icon = "" if task_result.eval.score == 1.0 else "" if task_result.eval.score > 0 else ""
            thread_status(thread_id, spec_id, f"{score_icon} Done! Score: {task_result.eval.score}")
        else:
            thread_status(thread_id, spec_id, "Done (no eval)")

        # Write context results (hints/guards/enrichments) to log
        _write_context_results(log_capture, task.task_id, failure_logger)

        stats.finish_task(task.task_id, result['score'])

    except Exception as e:
        _thread_stdout.unregister()
        _thread_stderr.unregister()

        result['error'] = str(e)
        thread_status(thread_id, spec_id, f"ERROR: {str(e)[:50]}")

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


def run_parallel(
    base_url: str,
    tasks_to_run: List[TaskInfo],
    stats: SessionStats,
    num_threads: int,
    model_id: str,
    pricing_model: str,
    backend: str,
    api_key: str,
    logs_dir: Path,
    verbose: bool = False,
) -> List[Dict[str, Any]]:
    """
    Run tasks in parallel using ThreadPoolExecutor.

    Args:
        base_url: ERC3 API base URL
        tasks_to_run: List of tasks to execute
        stats: Session statistics tracker
        num_threads: Number of parallel threads
        model_id: LLM model identifier
        pricing_model: Model ID for pricing
        backend: LLM backend name
        api_key: ERC3 API key
        logs_dir: Directory for task log files
        verbose: Whether to show real-time output

    Returns:
        List of result dicts with task_id, spec_id, score, error
    """
    # Install thread-local stdout/stderr dispatchers
    _thread_stdout = get_thread_stdout()
    _thread_stderr = get_thread_stderr()
    sys.stdout = _thread_stdout
    sys.stderr = _thread_stderr

    # Pre-initialize embedding model in main thread (avoids race condition on GPU)
    get_embedding_model()

    print(f"\n Running {len(tasks_to_run)} tasks with {num_threads} threads...\n")

    results = []
    with ThreadPoolExecutor(max_workers=num_threads, thread_name_prefix="Worker") as executor:
        futures = {
            executor.submit(
                run_task_worker,
                task,
                stats,
                base_url,
                model_id,
                pricing_model,
                backend,
                idx % num_threads,
                api_key,
                logs_dir,
                verbose,
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
    _print_parallel_summary(results, stats, logs_dir)

    return results


def _print_parallel_summary(
    results: List[Dict[str, Any]],
    stats: SessionStats,
    logs_dir: Path
):
    """Print summary of parallel execution."""
    print("\n PARALLEL EXECUTION SUMMARY")
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
        print("\n  Failed tasks:")
        for r in failed:
            print(f"     - {r['spec_id']}: {r['error'][:50]}...")

    print(f"\n Detailed logs: {logs_dir}/")
    print(f"   View specific task: cat {logs_dir}/<spec_id>.log")
