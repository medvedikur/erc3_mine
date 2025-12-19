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
"""

import os
import sys
import logging
import argparse

from dotenv import load_dotenv

# Configure logging to suppress noisy httpx/httpcore logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Ensure we can import from parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_args():
    """Parse command line arguments."""
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
    parser.add_argument('-benchmark', '--benchmark', type=str, default=None,
                        help='Benchmark type: erc3-test, erc3-dev, erc3 (overrides config.py)')
    return parser.parse_args()


def load_environment():
    """Load environment variables from .env files."""
    env_path = os.path.join(os.path.dirname(__file__), '..', 'sgr-agent-store', '.env')
    loaded = load_dotenv(env_path)

    if not loaded:
        env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
        load_dotenv(env_path)

    local_env = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(local_env):
        load_dotenv(local_env, override=True)


def get_model_config(use_openrouter: bool) -> tuple:
    """
    Get model configuration based on backend.

    Returns:
        Tuple of (model_id, pricing_model_id, backend)
    """
    from pricing import calculator
    import config

    if use_openrouter:
        # Use config.py defaults, can be overridden by env vars
        model_id = os.environ.get("MODEL_ID_OPENROUTER", config.DEFAULT_MODEL_OPENROUTER)
        pricing_model = os.environ.get("PRICING_MODEL_ID_OPENROUTER") or \
                        os.environ.get("PRICING_MODEL_ID") or \
                        config.DEFAULT_PRICING_MODEL or model_id
        backend = "openrouter"

        if not os.environ.get("OPENAI_API_KEY"):
            print("OPENAI_API_KEY not found in environment!")
            print("   Set it in .env: OPENAI_API_KEY=sk-or-...")
            sys.exit(1)
    else:
        # Use config.py defaults, can be overridden by env vars
        model_id = os.environ.get("MODEL_ID_GONKA", config.DEFAULT_MODEL_GONKA)
        pricing_model = os.environ.get("PRICING_MODEL_ID") or \
                        config.DEFAULT_PRICING_MODEL or "qwen/qwen3-235b-a22b-2507"
        backend = "gonka"

        if not os.environ.get("GONKA_PRIVATE_KEY"):
            print("GONKA_PRIVATE_KEY not found! LLM calls might fail if not using a public node.")

    # Verify pricing model
    pricing_model = _verify_pricing_model(calculator, pricing_model)

    return model_id, pricing_model, backend


def _verify_pricing_model(calculator, model_id: str) -> str:
    """Verify pricing model exists or fallback."""
    try:
        if calculator.calculate_cost(model_id, 1000, 1000) > 0:
            return model_id
    except:
        pass

    for fallback in ["qwen/qwen3-235b-a22b", "qwen/qwen3-235b-a22b:free", "qwen/qwen-2.5-72b-instruct"]:
        try:
            if calculator.calculate_cost(fallback, 1000, 1000) > 0:
                print(f"Primary model {model_id} not found in pricing, using fallback: {fallback}")
                return fallback
        except:
            continue

    print("No pricing model found, cost will be $0")
    return model_id


def print_banner(use_openrouter: bool, num_threads: int, model_id: str, pricing_model: str):
    """Print startup banner."""
    backend_emoji = "" if use_openrouter else ""
    backend_name = "OpenRouter" if use_openrouter else "Gonka Network"
    mode_str = f"PARALLEL ({num_threads} threads)" if num_threads > 1 else "Sequential"

    print(f"""
=======================================================================
  {backend_emoji} ERC3-TEST Agent - {backend_name:<19} ({mode_str:<18})
  Model: {model_id:<52}
  Pricing: {pricing_model:<50}
=======================================================================
""")


def main():
    """Main entry point."""
    args = parse_args()
    load_environment()

    # Verify API key
    api_key = os.environ.get("ERC3_API_KEY")
    if not api_key:
        print("ERC3_API_KEY not found in environment!")
        sys.exit(1)

    # Get model config
    model_id, pricing_model, backend = get_model_config(args.openrouter)

    # Import config after env is loaded
    import config

    # Run local tests if requested
    if args.tests_on:
        from session import run_local_tests
        print_banner(args.openrouter, args.threads, model_id, pricing_model)
        run_local_tests(
            model_id=model_id,
            pricing_model=pricing_model,
            backend=backend,
            parallel=args.threads > 1,
            num_threads=args.threads,
            task_filter=args.task,
            verbose=args.verbose,
            max_turns=config.MAX_TURNS_PER_TASK,
        )
        return

    # Print banner
    print_banner(args.openrouter, args.threads, model_id, pricing_model)

    # Run benchmark
    from session import BenchmarkRunner

    benchmark_type = args.benchmark or config.BENCHMARK

    runner = BenchmarkRunner(
        api_key=api_key,
        base_url=config.API_BASE_URL,
        model_id=model_id,
        pricing_model=pricing_model,
        backend=backend,
        benchmark_type=benchmark_type,
        workspace=config.WORKSPACE,
        session_name=config.SESSION_NAME,
        competition_flags=config.COMPETITION_FLAGS,
    )

    # Start session and get tasks
    tasks = runner.start_session(num_threads=args.threads)
    tasks = runner.filter_tasks(tasks, args.task, parallel_mode=args.threads > 1)

    if not tasks:
        print("No tasks to run!")
        return

    # Run tasks
    if args.threads > 1:
        runner.run_parallel(tasks, args.threads, verbose=args.verbose)
    else:
        runner.run_sequential(tasks)

    # Submit
    runner.submit_session(force=args.task is not None)


if __name__ == "__main__":
    main()
