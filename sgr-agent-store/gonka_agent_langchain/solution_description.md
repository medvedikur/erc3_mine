# Gonka Network SGR Agent (LangChain Edition)

This solution reimplements the SGR (Schema-Guided Reasoning) agent using **LangChain** and runs on the Gonka Network (Decentralized Inference).

## Structure
- `main.py`: Entry point handling session loop and configuration.
- `agent.py`: LangChain-based agent loop implementing the "Mental Protocol".
- `gonka_llm.py`: Custom `GonkaChatModel` for LangChain with built-in node failover and retry logic.
- `tools.py`: Tool definitions and Pydantic schemas for actions.
- `prompts.py`: The SGR system prompt enforcing the thinking process.
- `logs/`: Directory containing detailed execution logs and failure reports.

## Key Features
- **LangChain Integration**: Uses `LangChain` primitives (ChatModels, Messages) for robust interaction.
- **Custom Gonka LLM**: A specialized LangChain Chat Model wrapper that handles Gonka network specifics (node selection, failover).
- **Reasoning First**: Preserves the strict "Mental Protocol" (Analyze -> Plan -> Verify) via the SGR system prompt.
- **Robustness**: 
  - Automatic node switching on failure.
  - JSON repair strategies.
  - Detailed failure logging (`logs/`).
- **Telemetry**: Full integration with ERC3 benchmarking and cost tracking.

## Evolution & Improvements

### 1. Planning & Checklist System
To address issues where the agent would "forget" complex multi-step plans (e.g., testing mixed bundle combinations), we introduced a mandatory `plan` field in the LLM response schema. The agent must now maintain a list of steps with statuses (`pending`, `in_progress`, `completed`). This state persistence across turns significantly improved performance on optimization tasks.

### 2. Robust Network Strategy (Smart Retry & Failover)
The `GonkaChatModel` implements a sophisticated resilience layer:
- **Automatic Retry**: Standard errors trigger immediate retries on the same node with exponential backoff.
- **Critical Error Detection**: The system identifies fatal network errors (e.g., `RemoteDisconnected`, `Connection refused`, `Connection aborted`).
- **Node Failover**: Instead of retrying a dead node, critical errors trigger an immediate **node switch** via `_switch_node()`. The agent maintains a list of healthy nodes and cycles through them, ensuring high availability even during network instability.

### 3. Token Counting Fallback
We observed that some API nodes or the LangChain integration occasionally dropped usage metadata, leading to zero-token reports. We implemented a **multi-layered fallback strategy**:
1. Robust extraction of usage data (supporting both object and dict formats).
2. A "last resort" estimation in `agent.py` that calculates token usage based on character count if the API returns empty data.
This ensures that session statistics and cost estimates are always populated.

### 4. Object Identity & State Management
We refactored how the `SessionStats` object is passed through the application layers (`main.py` -> `agent.py` -> `stats.py`) to ensure that all usage data is aggregated into a single source of truth, fixing a bug where statistics were not being reported correctly at the end of the session.

### 5. Tool-Level Verification (The "Blind Check" Pattern)
To prevent "hallucinated actions" (e.g., checking out with the wrong price), we implemented strict verification logic directly inside the tools. 

**Example: `checkout_basket`**
Instead of a simple trigger, the tool requires the LLM to provide its expectations:
```python
class Req_CheckoutBasket(ToolModel):
    expected_total: float  # <--- Mandatory expectation
    expected_coupon: Optional[str] = None
```
Before executing the API call, the handler:
1. Fetches the *actual* basket state.
2. Compares `actual_total` vs `expected_total`.
3. If they mismatch, the tool **fails locally** and returns an error to the agent ("Expected 50, but basket is 100").

This forces the agent to be self-consistent and catches synchronization errors (e.g., forgotten coupons or inventory changes) *before* irreversible actions are taken. This pattern serves as the primary reliability layer for porting the agent to new domains.

## Developer Guide

### Logging & Debugging
The agent provides a dual-layer logging system:
1. **Console Output**: Real-time colored logs showing reasoning, plans, actions, and API results. Designed for monitoring active sessions.
2. **File Logs (`logs/run_<timestamp>/`)**:
   - **Failures**: If a task fails (score 0), a full JSON dump (`failure_XX.json`) and a human-readable summary (`failure_XX_summary.txt`) are automatically generated.
   - These files contain the full conversation history, exact API payloads, and internal state, making post-mortem analysis easy.

### Extending the Agent (Rules & Best Practices)

When adding new capabilities or porting to new benchmarks, follow these rules:

1. **Strict Tool Validation**: 
   - Never trust the LLM's memory blindly.
   - Critical actions (buy, delete, publish) MUST require "expected state" parameters.
   - Verify these expectations against the API *before* execution.

2. **Schema-Driven Actions**:
   - Define all new tools in `tools.py` using Pydantic `BaseModel`.
   - Ensure every tool has a clear description and typed arguments.

3. **Plan Persistence**:
   - If the task requires >3 steps, ensure the system prompt enforces the usage of the `plan` field.
   - The agent relies on this field to "remember" its progress across context window shifts.

4. **Error Handling as Feedback**:
   - Do not catch API errors silently. Return them as text observations to the agent.
   - Let the agent reason about the error (e.g., "Out of stock" -> "I should look for a substitute").

## Usage
Run `python sgr-agent-store/gonka_agent_langchain/main.py`.
Ensure `GONKA_PRIVATE_KEY` is set in `.env`.
