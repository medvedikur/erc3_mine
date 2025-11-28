"""
ERC3-dev Agent - Main Entry Point

Usage:
    python main.py                    # Run all tasks in the benchmark
    
Environment variables required:
    OPENAI_API_KEY - OpenAI/OpenRouter API key
    OPENAI_BASE_URL - API base URL (optional, defaults to OpenAI)
    MODEL_ID - Model to use (optional, defaults to openai/gpt-4o)
    ERC3_API_KEY - ERC3 access key
"""

import textwrap
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from openai import OpenAI
from erc3_dev_agent import run_agent, SessionStats
from erc3 import ERC3

client = OpenAI()
core = ERC3()

# Configuration
MODEL_ID = os.getenv("MODEL_ID", "openai/gpt-4o")

# Start session with metadata
res = core.start_session(
    benchmark="erc3-dev",
    workspace="my-workspace-1",
    name="@mishka ERC3-dev Agent",
    architecture="Basic SGR Agent with OpenAI"
)

status = core.session_status(res.session_id)
print(f"Session has {len(status.tasks)} tasks")

# Initialize statistics
stats = SessionStats()

for task in status.tasks:
    print("="*40)
    print(f"Starting Task: {task.task_id} ({task.spec_id}): {task.task_text}")
    
    core.start_task(task)
    try:
        run_agent(MODEL_ID, core, task, stats=stats)
    except Exception as e:
        print(f"Fatal error in agent: {e}")
        import traceback
        traceback.print_exc()
        
    result = core.complete_task(task)
    if result.eval:
        explain = textwrap.indent(result.eval.logs, "  ")
        print(f"\nSCORE: {result.eval.score}\n{explain}\n")

core.submit_session(res.session_id)

# Print final statistics
stats.print_report()
