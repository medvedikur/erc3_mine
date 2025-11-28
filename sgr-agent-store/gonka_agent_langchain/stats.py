import os
import json
import time
from datetime import datetime
from pathlib import Path
from pricing import calculator

# === Statistics & Billing ===
class SessionStats:
    def __init__(self):
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.llm_requests = 0
        self.store_api_requests = 0
        self.total_cost_usd = 0.0

    def add_llm_usage(self, model: str, usage):
        if usage:
            # Usage is passed as a Pydantic-like object or dict, handle both
            p_tokens = getattr(usage, 'prompt_tokens', 0)
            c_tokens = getattr(usage, 'completion_tokens', 0)
            
            # If attributes are missing or 0, try dictionary access or __dict__
            if p_tokens == 0 and c_tokens == 0:
                if isinstance(usage, dict):
                    p_tokens = usage.get('prompt_tokens', 0)
                    c_tokens = usage.get('completion_tokens', 0)
                elif hasattr(usage, '__dict__'):
                    p_tokens = usage.__dict__.get('prompt_tokens', 0)
                    c_tokens = usage.__dict__.get('completion_tokens', 0)

            self.total_prompt_tokens += p_tokens
            self.total_completion_tokens += c_tokens
            self.llm_requests += 1
            
            # DEBUG: Cumulative check
            # print(f"DEBUG: Total Prompts so far: {self.total_prompt_tokens}")

            cost = calculator.calculate_cost(model, p_tokens, c_tokens)
            self.total_cost_usd += cost

    def add_api_call(self):
        self.store_api_requests += 1

    def print_report(self):
        total_tokens = self.total_prompt_tokens + self.total_completion_tokens
        print("\n" + "="*50)
        print(f"📊 SESSION STATISTICS REPORT (GONKA + SGR + LangChain)")
        print("="*50)
        print(f"🧠 LLM Requests:      {self.llm_requests}")
        print(f"🛒 Store API Calls:   {self.store_api_requests}")
        print("-" * 30)
        print(f"📥 Input Tokens:      {self.total_prompt_tokens}")
        print(f"📤 Output Tokens:     {self.total_completion_tokens}")
        print(f"∑  Total Tokens:      {total_tokens}")
        print("-" * 30)
        print(f"💰 TOTAL COST:        ${self.total_cost_usd:.6f}")
        print("="*50 + "\n")


# === Failure Logging ===
class FailureLogger:
    """Logs failed tasks (score=0) to files for analysis"""
    
    def __init__(self):
        self.run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Logs relative to this file's parent (the package)
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

# Global logger
failure_logger = FailureLogger()

