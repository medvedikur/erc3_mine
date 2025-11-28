import textwrap
import os
import sys
import logging
from dotenv import load_dotenv

# Configure logging to suppress noisy httpx/httpcore logs from OpenAI client
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Ensure we can import erc3 and pricing from parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables
# Try loading from sgr-agent-store directory as per user instruction
env_path = os.path.join(os.path.dirname(__file__), '..', 'sgr-agent-store', '.env')
loaded = load_dotenv(env_path)

# Fallback to parent dir if not found
if not loaded:
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    loaded = load_dotenv(env_path)

# Debug: Check if key is loaded (print once)
if not os.environ.get("GONKA_PRIVATE_KEY"):
    print("⚠️ GONKA_PRIVATE_KEY not found! LLM calls might fail if not using a public node.")

from erc3 import ERC3
from pricing import calculator
from agent import run_agent
from stats import SessionStats, failure_logger

# Gonka model ID
MODEL_ID = "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8" 
# Use a cheaper model for dev if needed, but sticking to reference
PRICING_MODEL_ID = "qwen/qwen3-235b-a22b-2507"

def verify_pricing_model(model_id: str) -> str:
    """Verify pricing model exists in OpenRouter or fallback"""
    try:
        test_cost = calculator.calculate_cost(model_id, 1000, 1000)
        if test_cost > 0:
            # print(f"✓ Pricing model: {model_id}") # Reduce startup noise
            return model_id
    except:
        pass
    
    fallback_models = ["qwen/qwen3-235b-a22b", "qwen/qwen3-235b-a22b:free", "qwen/qwen-2.5-72b-instruct"]
    for fallback in fallback_models:
        try:
            cost = calculator.calculate_cost(fallback, 1000, 1000)
            if cost > 0:
                print(f"⚠ Primary model {model_id} not found, using fallback: {fallback}")
                return fallback
        except:
            continue
    
    print(f"⚠ No pricing model found, cost will be $0")
    return model_id

PRICING_MODEL_ID = verify_pricing_model(PRICING_MODEL_ID)

print(f"""
╔════════════════════════════════════════════════════════════════════╗
║  🚀 ERC3-DEV Agent - Gonka Network + SGR + LangChain               ║
║  Model: {MODEL_ID:<52} ║
╚════════════════════════════════════════════════════════════════════╝
""")

core = ERC3(
    key=os.environ.get("ERC3_API_KEY", "key-7ePHVSyN3b2ntJ5fRxYbKHU61r7MWF"),
    base_url="https://erc.timetoact-group.at"
)

# Start session
res = core.start_session(
    benchmark="erc3-dev",
    workspace="dev-workspace-1",
    name="@mishka ERC3-Dev Agent",
    architecture=f"SGR Agent (Gonka {MODEL_ID})"
)

status = core.session_status(res.session_id)
print(f"Session {res.session_id} has {len(status.tasks)} tasks")

stats = SessionStats()

for task in status.tasks:
    # Optional: Filter tasks for testing
    # if task.spec_id != "wipe_my_data": continue

    print("=" * 40)
    print(f"Starting Task: {task.task_id} ({task.spec_id}): {task.task_text}")
    
    failure_logger.start_task(task.task_id, task.task_text, task.spec_id)
    
    core.start_task(task)
    try:
        run_agent(
            model_name=MODEL_ID,
            api=core,
            task=task,
            stats=stats,
            pricing_model=PRICING_MODEL_ID,
            failure_logger=failure_logger
        )
    except Exception as e:
        print(f"Fatal error in agent: {e}")
        import traceback
        traceback.print_exc()
        
    result = core.complete_task(task)
    if result.eval:
        explain = textwrap.indent(result.eval.logs, "  ")
        print(f"\nSCORE: {result.eval.score}\n{explain}\n")
        failure_logger.save_failure(task.task_id, result.eval.score, result.eval.logs)
    else:
        print(f"\nTask Completed (Status: {result.status})\n")

core.submit_session(res.session_id)
stats.print_report()
failure_logger.print_summary()
