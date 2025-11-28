# Gonka Network SGR Agent (LangChain Edition)

This solution reimplements the SGR (Schema-Guided Reasoning) agent using **LangChain** and runs on the Gonka Network (Decentralized Inference).

## Structure
- `main.py`: Entry point handling session loop and configuration.
- `agent.py`: LangChain-based agent loop implementing the "Mental Protocol".
- `gonka_llm.py`: Custom `GonkaChatModel` for LangChain with built-in node failover and retry logic.
- `tools.py`: Tool definitions and Pydantic schemas for actions.
- `prompts.py`: The SGR system prompt enforcing the thinking process.

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

### 2. Robust Network Handling
The `GonkaChatModel` was enhanced to detect critical network errors (e.g., `RemoteDisconnected`, `Connection refused`) during the retry loop. Instead of retrying the same dead node, these errors now trigger an immediate **node switch** via `_switch_node()`, ensuring high availability even when individual Gonka nodes go offline.

### 3. Token Counting Fallback
We observed that some API nodes or the LangChain integration occasionally dropped usage metadata, leading to zero-token reports. We implemented a **multi-layered fallback strategy**:
1. Robust extraction of usage data (supporting both object and dict formats).
2. A "last resort" estimation in `agent.py` that calculates token usage based on character count if the API returns empty data.
This ensures that session statistics and cost estimates are always populated.

### 4. Object Identity & State Management
We refactored how the `SessionStats` object is passed through the application layers (`main.py` -> `agent.py` -> `stats.py`) to ensure that all usage data is aggregated into a single source of truth, fixing a bug where statistics were not being reported correctly at the end of the session.

## Usage
Run `python sgr-agent-store/gonka_agent_langchain/main.py`.
Ensure `GONKA_PRIVATE_KEY` is set in `.env`.
