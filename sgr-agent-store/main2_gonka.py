"""
ERC3 Agent running on Gonka Network
Uses Qwen/Qwen3-235B-A22B-Instruct-2507-FP8 model via decentralized inference

Required ENV variables:
  - GONKA_PRIVATE_KEY: Your Gonka network private key
  - GONKA_NODE_URL (optional): Fixed node URL. If not set, selects random node from active participants.
"""
import textwrap
import os
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from store_agent_2_gonka import run_agent, SessionStats, create_gonka_client_with_retry
from erc3 import ERC3
from pricing import calculator

# Gonka model ID (used for inference)
MODEL_ID = "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8"

# OpenRouter model ID for pricing lookup
# Maps Gonka model to equivalent OpenRouter model for cost estimation
PRICING_MODEL_ID = "qwen/qwen3-235b-a22b-2507"

# Verify at startup that pricing model exists in OpenRouter
def verify_pricing_model(model_id: str) -> str:
    """Проверяет что модель найдена в OpenRouter"""
    test_cost = calculator.calculate_cost(model_id, 1000, 1000)
    if test_cost > 0:
        print(f"✓ Pricing model: {model_id}")
        print(f"  Input: ${float(calculator.prices[model_id]['prompt'])*1_000_000:.2f}/M tokens")
        print(f"  Output: ${float(calculator.prices[model_id]['completion'])*1_000_000:.2f}/M tokens")
        return model_id
    
    # Try fallback models
    fallback_models = ["qwen/qwen3-235b-a22b", "qwen/qwen3-235b-a22b:free", "qwen/qwen-2.5-72b-instruct"]
    for fallback in fallback_models:
        cost = calculator.calculate_cost(fallback, 1000, 1000)
        if cost > 0:
            print(f"⚠ Primary model {model_id} not found, using fallback: {fallback}")
            return fallback
    
    print(f"⚠ No pricing model found, cost will be $0")
    return model_id

PRICING_MODEL_ID = verify_pricing_model(PRICING_MODEL_ID)


# ═══════════════════════════════════════════════════════════════════════════
# FAILURE LOGGING SYSTEM
# ═══════════════════════════════════════════════════════════════════════════
class FailureLogger:
    """Logs failed tasks (score=0) to files for analysis"""
    
    def __init__(self):
        self.run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.logs_dir = Path(__file__).parent / "logs" / f"run_{self.run_timestamp}"
        self.failure_count = 0
        self.conversation_logs = {}  # task_id -> list of messages
    
    def start_task(self, task_id: str, task_text: str, spec_id: str):
        """Initialize logging for a new task"""
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
        if task_id in self.conversation_logs:
            self.conversation_logs[task_id]["messages"].append({
                "turn": turn,
                "llm_response": raw_response,
                "parsed_actions": [str(a) for a in parsed_actions] if parsed_actions else []
            })
    
    def log_api_call(self, task_id: str, action_type: str, request: dict, response: dict, error: str = None):
        """Log an API call and response"""
        if task_id in self.conversation_logs:
            self.conversation_logs[task_id]["api_responses"].append({
                "action": action_type,
                "request": request,
                "response": response,
                "error": error
            })
    
    def save_failure(self, task_id: str, score: float, eval_logs: str):
        """Save failure log if score is 0"""
        if score > 0 or task_id not in self.conversation_logs:
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
                    f.write(f"  Response: {json.dumps(call['response'], indent=2)[:500]}\n")
    
    def print_summary(self):
        """Print summary of failures"""
        if self.failure_count > 0:
            print(f"\n⚠️  {self.failure_count} failures logged to: {self.logs_dir}")


# Global failure logger instance
failure_logger = FailureLogger()


# Verify GONKA_PRIVATE_KEY is set
if not os.getenv("GONKA_PRIVATE_KEY"):
    raise ValueError(
        "❌ GONKA_PRIVATE_KEY not found!\n"
        "Please set it in your .env file or environment variables.\n"
        "Get your key at: https://gonka.ai/developer/quickstart/"
    )

print(f"""
╔════════════════════════════════════════════════════════════════════╗
║  🚀 ERC3 Agent - Gonka Network + SGR (Schema-Guided Reasoning)     ║
║  Model: {MODEL_ID:<52} ║
║  Pricing: OpenRouter Qwen3-235B rates                              ║
║  Docs: https://abdullin.com/schema-guided-reasoning/               ║
╚════════════════════════════════════════════════════════════════════╝
""")

# Initialize ERC3 core
core = ERC3()

# Create Gonka client with automatic node selection and failover
gonka_client, selected_node = create_gonka_client_with_retry()
print(f"✓ Connected to Gonka via: {selected_node}")

# Start session with metadata
res = core.start_session(
    benchmark="store",
    workspace="my-workspace-1",
    name="@mishka SGR Agent 2 (Gonka)",
    architecture=f"NextStep SGR Agent 2 with Gonka Network ({MODEL_ID})"
)

status = core.session_status(res.session_id)
print(f"Session has {len(status.tasks)} tasks")

# Initialize statistics
stats = SessionStats()

for task in status.tasks:
    print("=" * 40)
    print(f"Starting Task: {task.task_id} ({task.spec_id}): {task.task_text}")
    
    # Initialize failure logging for this task
    failure_logger.start_task(task.task_id, task.task_text, task.spec_id)
    
    core.start_task(task)
    try:
        # Pass the Gonka client, stats, pricing model ID, and failure logger
        run_agent(
            MODEL_ID, core, task, 
            stats=stats, 
            client=gonka_client, 
            pricing_model=PRICING_MODEL_ID,
            failure_logger=failure_logger
        )
    except Exception as e:
        print(f"Fatal error in agent: {e}")
        
    result = core.complete_task(task)
    if result.eval:
        explain = textwrap.indent(result.eval.logs, "  ")
        print(f"\nSCORE: {result.eval.score}\n{explain}\n")
        
        # Log failure if score is 0
        failure_logger.save_failure(task.task_id, result.eval.score, result.eval.logs)

core.submit_session(res.session_id)

# Print final report with cost estimation
stats.print_report()
failure_logger.print_summary()

