# Gonka Network SGR Agent (LangChain Edition) - ERC3-Dev Port

This solution reimplements the SGR (Schema-Guided Reasoning) agent using **LangChain** and runs on the Gonka Network (Decentralized Inference), specifically adapted for the **ERC3-Dev Benchmark**.

## Structure
- `main.py`: Entry point handling session loop, environment configuration, and task orchestration.
- `agent.py`: LangChain-based agent loop implementing the "Mental Protocol" (Analyze -> Plan -> Act).
- `gonka_llm.py`: Custom `GonkaChatModel` for LangChain with built-in node failover and retry logic, using `gonka-openai`.
- `tools.py`: Tool definitions and Pydantic schemas mapping LLM actions to `erc3` SDK calls. Handles argument normalization.
- `prompts.py`: The SGR system prompt enforcing the thinking process, adapted for the Employee Assistant domain.
- `handlers/`:
  - `core.py`: Execution engine for tools with middleware support.
  - `wiki.py`: Middleware for automatic synchronization of the Company Wiki (RAG) when `wiki_sha1` changes.
  - `base.py`: Protocols for handlers and middleware.
- `logs/`: Directory containing detailed execution logs and failure reports.

## Key Features

### 1. Adaptive Knowledge Management (Wiki Sync)
The agent operates in a dynamic environment where company policies (`rulebook.md`) and data change.
- **Auto-Sync**: Middleware intercepts API responses containing `wiki_sha1` (e.g., from `/whoami`). If the hash differs from the local cache, it triggers an immediate re-fetch of all wiki pages.
- **Context Injection**: The system prompt is dynamically updated with a summary of available wiki pages, ensuring the agent knows what information is accessible.

### 2. Robust Tool Dispatch & Validation
To handle LLM output quirks (e.g., using `text` instead of `message`, or strings instead of lists), `tools.py` implements a robust normalization layer:
- **Alias Handling**: Maps diverse LLM terms (e.g., "answer", "reply") to the correct API tool (`respond`).
- **Type Coercion**: Automatically wraps single strings into lists for array fields (e.g., `status` in `projects_search`) and handles fallback values.
- **Error Recovery**: Catches API validation errors (e.g., `null` lists from backend) and provides safe defaults (empty lists) to prevent crashes.

### 3. Gonka Network Resilience
The `GonkaChatModel` (`gonka_llm.py`) ensures high availability:
- **Node Failover**: Automatically switches between available Gonka nodes if one fails or times out.
- **Retry Logic**: Implements exponential backoff for transient network errors.
- **Smart Connection**: Validates initial connection to a healthy node before starting the session.

### 4. Schema-Guided Reasoning (SGR)
The agent follows a strict "Mental Protocol" defined in `prompts.py`:
1.  **Identity Check**: Always verifies current user role (`who_am_i`) to determine permissions (Guest vs. Employee vs. Lead).
2.  **Policy Review**: Consults the Wiki (`rulebook.md`) before taking privileged actions (e.g., archiving projects, revealing data).
3.  **Plan & Execute**: Maintains a structured plan in the JSON response to track progress across multi-turn tasks.

## Evolution & Improvements (vs Store Agent)

### 1. Identity-Aware Planning
Unlike the stateless Store agent, the ERC3 agent must handle **permissions**. The planning phase now explicitly includes "Context & Identity Check" as the first step for every task. This prevents unauthorized actions (e.g., "wipe my data" by a guest) which would negatively impact the score.

### 2. Middleware Architecture
We introduced a middleware pattern in `handlers/` to decouple cross-cutting concerns like Wiki Synchronization from the core agent logic. This allows the agent to "learn" about policy updates seamlessly without manual tool calls.

### 3. Strict vs. Lenient Validation
While the Store agent used strict "Blind Check" validation for checkout, the ERC3 agent adopts a **Lenient Input / Strict Output** approach. It forgives minor LLM formatting errors (via `tools.py` normalization) but enforces strict adherence to API schemas before making calls, ensuring the underlying `erc3` SDK never receives invalid data.

## Developer Guide

### Logging & Debugging
- **Console Output**: Real-time colored logs show the agent's Thought Process, Plan updates, and Action execution.
- **Failure Logs**: Located in `logs/run_<timestamp>/`. Each failed task generates a JSON dump and a human-readable summary text file containing the full conversation history and error details.

### Adding New Capabilities
1.  **Define Tool**: Add mapping in `tools.py`. Ensure robust argument extraction.
2.  **Update Prompt**: If the tool requires specific reasoning (e.g., time logging rules), add a hint to `SGR_SYSTEM_PROMPT` in `prompts.py`.
3.  **Middleware**: If the tool affects global state (like wiki hash), update `handlers/wiki.py` or create a new middleware.

## Usage
Run `python agent-erc3-dev/main.py`.
Ensure `.env` contains `ERC3_API_KEY` and `GONKA_PRIVATE_KEY`.

