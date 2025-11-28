import textwrap
import os
import sys
from dotenv import load_dotenv

# Ensure we can import erc3 and pricing from parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from erc3 import ERC3
from pricing import calculator
from gonka_agent_langchain.agent import run_agent
from gonka_agent_langchain.stats import SessionStats, failure_logger

# Gonka model ID
MODEL_ID = "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8"
PRICING_MODEL_ID = "qwen/qwen3-235b-a22b-2507"

def verify_pricing_model(model_id: str) -> str:
    """Verify pricing model exists in OpenRouter or fallback"""
    test_cost = calculator.calculate_cost(model_id, 1000, 1000)
    if test_cost > 0:
        print(f"✓ Pricing model: {model_id}")
        return model_id
    
    fallback_models = ["qwen/qwen3-235b-a22b", "qwen/qwen3-235b-a22b:free", "qwen/qwen-2.5-72b-instruct"]
    for fallback in fallback_models:
        cost = calculator.calculate_cost(fallback, 1000, 1000)
        if cost > 0:
            print(f"⚠ Primary model {model_id} not found, using fallback: {fallback}")
            return fallback
    
    print(f"⚠ No pricing model found, cost will be $0")
    return model_id

PRICING_MODEL_ID = verify_pricing_model(PRICING_MODEL_ID)

if not os.getenv("GONKA_PRIVATE_KEY"):
    raise ValueError("❌ GONKA_PRIVATE_KEY not found!")

print(f"""
╔════════════════════════════════════════════════════════════════════╗
║  🚀 ERC3 Agent - Gonka Network + SGR + LangChain                   ║
║  Model: {MODEL_ID:<52} ║
║  Pricing: OpenRouter Qwen3-235B rates                              ║
╚════════════════════════════════════════════════════════════════════╝
""")

core = ERC3()

# Start session
res = core.start_session(
    benchmark="store",
    workspace="my-workspace-1",
    name="@mishka SGR LangChain Agent (Gonka)",
    architecture=f"LangChain SGR Agent with Gonka Network ({MODEL_ID})"
)

status = core.session_status(res.session_id)
print(f"Session has {len(status.tasks)} tasks")

stats = SessionStats()

for task in status.tasks:
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

core.submit_session(res.session_id)
stats.print_report()
failure_logger.print_summary()

