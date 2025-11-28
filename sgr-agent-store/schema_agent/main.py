import textwrap
import os
from dotenv import load_dotenv

# Load environment variables from .env file in parent directory
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from openai import OpenAI
from agent import run_agent, SessionStats
from erc3 import ERC3

client = OpenAI()
core = ERC3()
MODEL_ID = os.getenv("MODEL_ID", "openai/gpt-5.1") # Убедись, что ID соответствует OpenRouter для точного подсчета

# Start session with metadata
res = core.start_session(
    benchmark="store",
    workspace="my-workspace-1",
    name="@mishka Simple SGR Agent 2",
    architecture="NextStep SGR Agent 2 with OpenAI")

status = core.session_status(res.session_id)
print(f"Session has {len(status.tasks)} tasks")

# 1. Инициализируем статистику
stats = SessionStats()

for task in status.tasks:
    print("="*40)
    print(f"Starting Task: {task.task_id} ({task.spec_id}): {task.task_text}")
    
    core.start_task(task)
    try:
        # 2. Передаем объект stats
        run_agent(MODEL_ID, core, task, stats=stats)
    except Exception as e:
        print(f"Fatal error in agent: {e}")
        
    result = core.complete_task(task)
    if result.eval:
        explain = textwrap.indent(result.eval.logs, "  ")
        print(f"\nSCORE: {result.eval.score}\n{explain}\n")

core.submit_session(res.session_id)

# 3. Печатаем отчет по деньгам и токенам
stats.print_report()