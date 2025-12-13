import json
import time
import threading
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from pricing import calculator

# === Task Statistics (for parallel-safe per-task tracking) ===
@dataclass
class TaskStats:
    """Statistics for a single task execution"""
    task_id: str
    spec_id: str
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_sec: float = 0.0
    llm_requests: int = 0
    api_requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    score: Optional[float] = None
    turns: int = 0

    def start(self):
        self.started_at = time.time()

    def finish(self, score: Optional[float] = None):
        self.finished_at = time.time()
        self.duration_sec = self.finished_at - self.started_at
        self.score = score


# === Statistics & Billing ===
class SessionStats:
    """
    Thread-safe session statistics tracker.
    Supports both sequential and parallel task execution.
    """
    def __init__(self):
        self._lock = threading.Lock()

        # Global counters (aggregated from all tasks)
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.llm_requests = 0
        self.api_requests = 0
        self.total_cost_usd = 0.0

        # Timing
        self.session_started_at: float = time.time()
        self.session_finished_at: float = 0.0

        # Per-task tracking (thread-safe dict)
        self.tasks: Dict[str, TaskStats] = {}
        self._current_task_id: Optional[str] = None  # For sequential mode

        # Parallel execution tracking
        self.max_concurrent_tasks: int = 0
        self._active_tasks: int = 0

    def start_task(self, task_id: str, spec_id: str) -> TaskStats:
        """Start tracking a new task. Thread-safe."""
        with self._lock:
            task_stats = TaskStats(task_id=task_id, spec_id=spec_id)
            task_stats.start()
            self.tasks[task_id] = task_stats
            self._current_task_id = task_id

            # Track concurrency
            self._active_tasks += 1
            self.max_concurrent_tasks = max(self.max_concurrent_tasks, self._active_tasks)

            return task_stats

    def finish_task(self, task_id: str, score: Optional[float] = None):
        """Finish tracking a task. Thread-safe."""
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id].finish(score)
            self._active_tasks = max(0, self._active_tasks - 1)

    def add_llm_usage(self, model: str, usage, task_id: Optional[str] = None):
        """Add LLM usage. Thread-safe. Optionally associate with a specific task."""
        if not usage:
            return

        # Extract tokens from usage object
        p_tokens = getattr(usage, 'prompt_tokens', 0)
        c_tokens = getattr(usage, 'completion_tokens', 0)

        if p_tokens == 0 and c_tokens == 0:
            if isinstance(usage, dict):
                p_tokens = usage.get('prompt_tokens', 0)
                c_tokens = usage.get('completion_tokens', 0)
            elif hasattr(usage, '__dict__'):
                p_tokens = usage.__dict__.get('prompt_tokens', 0)
                c_tokens = usage.__dict__.get('completion_tokens', 0)

        cost = calculator.calculate_cost(model, p_tokens, c_tokens)

        with self._lock:
            # Update global counters
            self.total_prompt_tokens += p_tokens
            self.total_completion_tokens += c_tokens
            self.llm_requests += 1
            self.total_cost_usd += cost

            # Update task-specific counters
            tid = task_id or self._current_task_id
            if tid and tid in self.tasks:
                self.tasks[tid].prompt_tokens += p_tokens
                self.tasks[tid].completion_tokens += c_tokens
                self.tasks[tid].llm_requests += 1
                self.tasks[tid].cost_usd += cost
                self.tasks[tid].turns += 1

    def add_api_call(self, task_id: Optional[str] = None):
        """Add API call. Thread-safe."""
        with self._lock:
            self.api_requests += 1

            tid = task_id or self._current_task_id
            if tid and tid in self.tasks:
                self.tasks[tid].api_requests += 1

    def finish_session(self):
        """Mark session as finished."""
        self.session_finished_at = time.time()

    def get_session_duration(self) -> float:
        """Get total session duration in seconds."""
        end = self.session_finished_at or time.time()
        return end - self.session_started_at

    def get_total_task_time(self) -> float:
        """
        Get sum of all task durations.
        In parallel execution, this can exceed wall-clock time.
        """
        return sum(t.duration_sec for t in self.tasks.values() if t.duration_sec > 0)

    def print_report(self):
        """Print comprehensive statistics report."""
        self.finish_session()

        total_tokens = self.total_prompt_tokens + self.total_completion_tokens
        session_duration = self.get_session_duration()
        total_task_time = self.get_total_task_time()

        # Calculate averages
        num_tasks = len(self.tasks)
        avg_task_duration = total_task_time / num_tasks if num_tasks > 0 else 0
        avg_tokens_per_task = total_tokens / num_tasks if num_tasks > 0 else 0
        avg_cost_per_task = self.total_cost_usd / num_tasks if num_tasks > 0 else 0

        # Score statistics
        scored_tasks = [t for t in self.tasks.values() if t.score is not None]
        avg_score = sum(t.score for t in scored_tasks) / len(scored_tasks) if scored_tasks else 0
        perfect_tasks = sum(1 for t in scored_tasks if t.score == 1.0)
        failed_tasks = sum(1 for t in scored_tasks if t.score == 0.0)

        print("\n" + "=" * 60)
        print(f"📊 SESSION STATISTICS REPORT")
        print("=" * 60)

        # Tasks summary
        print(f"\n📋 TASKS")
        print("-" * 40)
        print(f"  Total Tasks:        {num_tasks}")
        if scored_tasks:
            print(f"  ✅ Perfect (1.0):    {perfect_tasks}")
            print(f"  ❌ Failed (0.0):     {failed_tasks}")
            print(f"  📈 Average Score:    {avg_score:.2%}")

        # Timing
        print(f"\n⏱️  TIMING")
        print("-" * 40)
        print(f"  Session Duration:   {self._format_duration(session_duration)}")
        print(f"  Total Task Time:    {self._format_duration(total_task_time)}")
        print(f"  Avg Task Duration:  {self._format_duration(avg_task_duration)}")
        if self.max_concurrent_tasks > 1:
            print(f"  Max Concurrency:    {self.max_concurrent_tasks} tasks")
            parallelism = total_task_time / session_duration if session_duration > 0 else 1
            print(f"  Parallelism Factor: {parallelism:.2f}x")

        # LLM & API
        print(f"\n🧠 LLM & API")
        print("-" * 40)
        print(f"  LLM Requests:       {self.llm_requests}")
        print(f"  API Calls:          {self.api_requests}")
        print(f"  Avg Turns/Task:     {self.llm_requests / num_tasks:.1f}" if num_tasks > 0 else "")

        # Tokens
        print(f"\n📊 TOKENS")
        print("-" * 40)
        print(f"  Input Tokens:       {self.total_prompt_tokens:,}")
        print(f"  Output Tokens:      {self.total_completion_tokens:,}")
        print(f"  Total Tokens:       {total_tokens:,}")
        print(f"  Avg Tokens/Task:    {avg_tokens_per_task:,.0f}")

        # Cost
        print(f"\n💰 COST")
        print("-" * 40)
        print(f"  Total Cost:         ${self.total_cost_usd:.6f}")
        print(f"  Avg Cost/Task:      ${avg_cost_per_task:.6f}")
        print(f"  Cost/1K Tokens:     ${(self.total_cost_usd / total_tokens * 1000):.6f}" if total_tokens > 0 else "")

        print("=" * 60)

        # Per-task breakdown (sorted by duration desc)
        if num_tasks > 0 and num_tasks <= 50:  # Only show if reasonable number
            print(f"\n📋 PER-TASK BREAKDOWN (sorted by duration)")
            print("-" * 60)
            sorted_tasks = sorted(self.tasks.values(), key=lambda t: t.duration_sec, reverse=True)
            for t in sorted_tasks:
                score_str = f"{t.score:.2f}" if t.score is not None else "N/A"
                status = "✅" if t.score == 1.0 else "❌" if t.score == 0.0 else "⚠️"
                print(f"  {status} {t.spec_id:<30} {self._format_duration(t.duration_sec):>8} | "
                      f"{t.turns:>2} turns | {t.prompt_tokens + t.completion_tokens:>6} tok | "
                      f"${t.cost_usd:.4f} | {score_str}")
            print("-" * 60)

        print("\n")

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable format."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            mins = int(seconds // 60)
            secs = seconds % 60
            return f"{mins}m {secs:.0f}s"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours}h {mins}m"

    def to_dict(self) -> dict:
        """Export statistics as dictionary (for JSON serialization)."""
        return {
            "session": {
                "started_at": datetime.fromtimestamp(self.session_started_at).isoformat(),
                "finished_at": datetime.fromtimestamp(self.session_finished_at).isoformat() if self.session_finished_at else None,
                "duration_sec": self.get_session_duration(),
                "total_task_time_sec": self.get_total_task_time(),
                "max_concurrent_tasks": self.max_concurrent_tasks,
            },
            "totals": {
                "tasks": len(self.tasks),
                "llm_requests": self.llm_requests,
                "api_requests": self.api_requests,
                "prompt_tokens": self.total_prompt_tokens,
                "completion_tokens": self.total_completion_tokens,
                "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
                "cost_usd": self.total_cost_usd,
            },
            "tasks": {
                tid: {
                    "spec_id": t.spec_id,
                    "duration_sec": t.duration_sec,
                    "turns": t.turns,
                    "llm_requests": t.llm_requests,
                    "api_requests": t.api_requests,
                    "tokens": t.prompt_tokens + t.completion_tokens,
                    "cost_usd": t.cost_usd,
                    "score": t.score,
                }
                for tid, t in self.tasks.items()
            }
        }


# === Failure Logging ===
class FailureLogger:
    """Logs failed tasks (score=0 or errors) to files for analysis"""

    def __init__(self):
        self.run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Logs relative to this file's parent (the package)
        self.logs_dir = Path(__file__).parent / "logs" / f"run_{self.run_timestamp}"
        self.failure_count = 0
        self.conversation_logs = {}  # task_id -> list of messages
        self._lock = threading.Lock()  # Thread-safe for parallel execution

    def start_task(self, task_id: str, task_text: str, spec_id: str):
        """Initialize logging for a new task"""
        with self._lock:
            self.conversation_logs[task_id] = {
                "task_id": task_id,
                "spec_id": spec_id,
                "task_text": task_text,
                "messages": [],
                "actions": [],
                "api_responses": [],
                "started_at": datetime.now().isoformat()
            }

    def log_llm_turn(self, task_id: str, turn: int, raw_response: str, parsed_actions: list):
        """Log an LLM turn"""
        with self._lock:
            if task_id in self.conversation_logs:
                self.conversation_logs[task_id]["messages"].append({
                    "turn": turn,
                    "llm_response": raw_response,
                    "parsed_actions": [str(a) for a in parsed_actions] if parsed_actions else []
                })

    def log_api_call(self, task_id: str, action_type: str, request: dict, response: dict, error: str = None):
        """Log an API call and response"""
        with self._lock:
            if task_id in self.conversation_logs:
                self.conversation_logs[task_id]["api_responses"].append({
                    "action": action_type,
                    "request": request,
                    "response": response,
                    "error": error
                })

    def log_context_results(self, task_id: str, action_type: str, results: list):
        """Log context results (hints, guards, enrichments) for an action"""
        with self._lock:
            if task_id in self.conversation_logs:
                # Initialize context_results list if not exists
                if "context_results" not in self.conversation_logs[task_id]:
                    self.conversation_logs[task_id]["context_results"] = []
                self.conversation_logs[task_id]["context_results"].append({
                    "action": action_type,
                    "results": list(results)  # Copy the list
                })

    def save_failure(self, task_id: str, score: float, eval_logs: str):
        """Save failure log"""
        with self._lock:
            if score > 0 or task_id not in self.conversation_logs:
                # Only log failures (score <= 0 means failure or error)
                return

            self.failure_count += 1

            # Create logs directory if needed
            self.logs_dir.mkdir(parents=True, exist_ok=True)

            # Build failure report
            task_data = self.conversation_logs[task_id]
            task_data["finished_at"] = datetime.now().isoformat()
            task_data["score"] = score
            task_data["eval_logs"] = eval_logs

            # Save to file
            filename = f"failure_{self.failure_count:02d}_{task_data['spec_id']}.json"
            filepath = self.logs_dir / filename

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(task_data, f, indent=2, ensure_ascii=False, default=str)

            print(f"📝 Failure logged: {filepath}")

            # Also save human-readable summary
            summary_file = self.logs_dir / f"failure_{self.failure_count:02d}_{task_data['spec_id']}_summary.txt"
            with open(summary_file, 'w', encoding='utf-8') as f:
                f.write(f"═══ FAILURE REPORT ═══\n")
                f.write(f"Task ID: {task_id}\n")
                f.write(f"Spec ID: {task_data['spec_id']}\n")
                f.write(f"Task: {task_data['task_text']}\n")
                f.write(f"Score: {score}\n")
                f.write(f"\n═══ EVALUATION ═══\n{eval_logs}\n")
                f.write(f"\n═══ CONVERSATION ({len(task_data['messages'])} turns) ═══\n")
                for msg in task_data['messages']:
                    f.write(f"\n--- Turn {msg['turn']} ---\n")
                    f.write(f"Actions: {msg['parsed_actions']}\n")
                    f.write(f"LLM Response:\n{msg['llm_response'][:1000]}...\n" if len(msg['llm_response']) > 1000 else f"LLM Response:\n{msg['llm_response']}\n")
                f.write(f"\n═══ API CALLS ({len(task_data['api_responses'])} calls) ═══\n")
                for call in task_data['api_responses']:
                    f.write(f"\n[{call['action']}]\n")
                    if call.get('error'):
                        f.write(f"  ERROR: {call['error']}\n")
                    else:
                        f.write(f"  Response: {str(call['response'])[:500]}\n")

                # Log context results (hints, guards, enrichments)
                context_results = task_data.get('context_results', [])
                if context_results:
                    f.write(f"\n═══ CONTEXT RESULTS (hints/guards/enrichments) ═══\n")
                    for ctx_result in context_results:
                        f.write(f"\n[{ctx_result['action']}]\n")
                        for result in ctx_result.get('results', []):
                            f.write(f"  {result}\n")

    def print_summary(self):
        """Print summary of failures"""
        if self.failure_count > 0:
            print(f"\n⚠️  {self.failure_count} failures logged to: {self.logs_dir}")

# Global logger
failure_logger = FailureLogger()
