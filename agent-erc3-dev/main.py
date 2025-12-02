import textwrap
import os
import sys
import logging
import argparse
from dotenv import load_dotenv

# Configure logging to suppress noisy httpx/httpcore logs from OpenAI client
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Ensure we can import erc3 and pricing from parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Parse command line arguments FIRST (before loading env)
parser = argparse.ArgumentParser(description='ERC3-TEST Agent')
parser.add_argument('-openrouter', '--openrouter', action='store_true', 
                    help='Use OpenRouter API instead of Gonka Network')
parser.add_argument('-task', '--task', type=str, default=None,
                    help='Filter to run only specific task spec_id')
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
from pricing import calculator
from agent import run_agent
from stats import SessionStats, failure_logger
from handlers.wiki import WikiManager

# Determine backend and model
USE_OPENROUTER = args.openrouter

if USE_OPENROUTER:
    # OpenRouter configuration (uses standard OpenAI env vars)
    MODEL_ID = os.environ.get("MODEL_ID_OPENROUTER", "openai/gpt-4o-mini")
    # Use separate pricing model ID or fall back to MODEL_ID
    PRICING_MODEL_ID = os.environ.get("PRICING_MODEL_ID_OPENROUTER") or os.environ.get("PRICING_MODEL_ID") or MODEL_ID
    BACKEND = "openrouter"
    
    # Check for OpenAI API key (OpenRouter uses same env var)
    if not os.environ.get("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY not found in environment!")
        print("   Set it in .env: OPENAI_API_KEY=sk-or-...")
        print("   Also set: OPENAI_BASE_URL=https://openrouter.ai/api/v1")
        sys.exit(1)
else:
    # Gonka Network configuration
    MODEL_ID = os.environ.get("MODEL_ID_GONKA", "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8")
    PRICING_MODEL_ID = os.environ.get("PRICING_MODEL_ID", "qwen/qwen3-235b-a22b-2507")
    BACKEND = "gonka"
    
    # Check for Gonka key
    if not os.environ.get("GONKA_PRIVATE_KEY"):
        print("⚠️ GONKA_PRIVATE_KEY not found! LLM calls might fail if not using a public node.")

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

# Print banner
backend_emoji = "🌐" if USE_OPENROUTER else "🚀"
backend_name = "OpenRouter" if USE_OPENROUTER else "Gonka Network"

print(f"""
╔════════════════════════════════════════════════════════════════════╗
║  {backend_emoji} ERC3-TEST Agent - {backend_name:<19} + SGR + LangChain      ║
║  Model: {MODEL_ID:<52} ║
║  Pricing: {PRICING_MODEL_ID:<50} ║
╚════════════════════════════════════════════════════════════════════╝
""")

# Validate required environment variable
ERC3_API_KEY = os.environ.get("ERC3_API_KEY")
if not ERC3_API_KEY:
    print("❌ ERC3_API_KEY not found in environment!")
    print("   Set it in .env: ERC3_API_KEY=key-...")
    sys.exit(1)

core = ERC3(
    key=ERC3_API_KEY,
    base_url="https://erc.timetoact-group.at"
)

# Start session with appropriate architecture description
architecture_desc = f"SGR Agent ({backend_name} {MODEL_ID})"
res = core.start_session(
    benchmark="erc3-test",
    workspace="test-workspace-1",
    name="@mishka ERC3-Test Agent",
    architecture=architecture_desc
)

status = core.session_status(res.session_id)
print(f"Session {res.session_id} has {len(status.tasks)} tasks")

stats = SessionStats()
wiki_manager = WikiManager()

for task in status.tasks:
    # Optional: Filter tasks for testing
    if args.task and task.spec_id != args.task:
        continue

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
            failure_logger=failure_logger,
            wiki_manager=wiki_manager,
            backend=BACKEND  # Pass backend type
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
