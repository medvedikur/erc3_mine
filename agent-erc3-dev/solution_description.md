# Gonka Network SGR Agent (LangChain Edition) - ERC3-Dev Port

This solution reimplements the SGR (Schema-Guided Reasoning) agent using **LangChain** and runs on the Gonka Network (Decentralized Inference), specifically adapted for the **ERC3-Dev Benchmark**.

## Structure
- `main.py`: Entry point handling session loop, environment configuration, and task orchestration.
- `agent.py`: LangChain-based agent loop implementing the "Mental Protocol" (Analyze -> Plan -> Act).
- `gonka_llm.py`: Custom `GonkaChatModel` for LangChain with built-in node failover and retry logic, using `gonka-openai`.
- `tools.py`: Tool definitions and Pydantic schemas mapping LLM actions to `erc3` SDK calls. Handles argument normalization.
- `prompts.py`: The SGR system prompt enforcing the thinking process, adapted for the Employee Assistant domain.
- `handlers/`:
  - `core.py`: Execution engine for tools with middleware support. Patches invalid API responses (e.g. `null` lists).
  - `wiki.py`: Middleware for automatic synchronization of the Company Wiki (RAG). Implements local chunking and semantic/keyword search.
  - `base.py`: Protocols for handlers and middleware.
- `logs/`: Directory containing detailed execution logs and failure reports.

## Key Features

### 1. Adaptive Knowledge Management (Local RAG)
The agent operates in a dynamic environment where company policies (`rulebook.md`) and data change.
- **Auto-Sync**: Middleware intercepts API responses containing `wiki_sha1` (e.g., from `/whoami`). If the hash differs from the local cache, it triggers an immediate re-fetch of all wiki pages.
- **Smart Indexing**: Pages are locally chunked and indexed. We use `sentence-transformers` (specifically `all-MiniLM-L6-v2`) to generate vector embeddings for all chunks locally, ensuring high-quality semantic understanding without external API costs.
- **Hybrid Search**: `wiki_search` executes a **Semantic Search** (Cosine Similarity) against the cached vectors. If the embedding model is not available, it gracefully degrades to token overlap ranking. This approach keeps inference **free** and **fast**.
- **Context Injection**: The system prompt is dynamically updated with a summary of available wiki pages.

### 2. Robust Tool Dispatch & Validation (Anti-Fragility)
To handle LLM output quirks and API inconsistencies, the tool layer is designed to be highly forgiving on input but strict on execution:
- **Argument Normalization**: "Smart Mapping" automatically fixes common hallucinations (e.g., mapping `query_semantic` to `query_regex`, `employee_id` to `employee`, `projects_update` to `update_project_status`).
- **Context Injection**: Automatically fills missing context-dependent fields (`logged_by`, `employee`) using the authenticated user's ID from `SecurityManager`.
- **Outcome Inference**: If the agent forgets the `outcome` field in `respond`, the system infers it from the message content (e.g., negative words -> `none_unsupported` or `denied_security`).
- **Auto-Linking**: Automatically detects entity IDs (`proj_...`) in response text and adds them to the `links` array if missing.
- **Strictness Fixes**: Handles API quirks (e.g. omitting `limit` when the server rejects explicit zeros) and patches invalid `null` list responses in `handlers/core.py`.
- **Validation**: Enforces required arguments to prevent runtime crashes.

### 3. Gonka Network Resilience
The `GonkaChatModel` (`gonka_llm.py`) ensures high availability:
- **Node Failover**: Automatically switches between available Gonka nodes if one fails or times out.
- **Retry Logic**: Implements exponential backoff for transient network errors.
- **Smart Connection**: Validates initial connection to a healthy node before starting the session.

### 4. Schema-Guided Reasoning (SGR)
The agent follows a strict "Mental Protocol" defined in `prompts.py`:
- **Outcome Selection**: Strictly enforces `outcome` codes (`denied_security`, `ok_answer`, etc.) based on the result.
- **Identity Check**: Always verifies current user role (`who_am_i`) to determine permissions.
- **Permissions**: Explicitly instructs NOT to deny based solely on job title, but to check entity ownership (e.g. Project Lead).
- **Data Source Separation**: Enforces using Database tools for entities (Projects, People) and Wiki for rules/policies.

## Handling Ambiguity & Data Conflicts (Corporate Realism)
The agent is engineered to operate in a realistic corporate environment where data may be incomplete, contradictory, or restricted.
- **Source of Truth Priority**:
    - **Database (Tools)**: Definitive source for Entity Existence and Status (Projects, Employees).
    - **Wiki (RAG)**: Definitive source for Policy, Rules, and unstructured context.
- **Handling Search Failures**: If a search returns empty, the agent is instructed to retry with broader terms or different attributes before concluding "Not Found".
- **Permission Granularity**: The agent distinguishes between "I don't know" (Data missing) and "I can't tell you" (Security restriction), forcing the use of `denied_security` even if partial information is known but sensitive details (IDs) are restricted.

## Evolution & Improvements

### 1. Identity-Aware Planning
Unlike the stateless Store agent, the ERC3 agent must handle **permissions**. The planning phase now explicitly includes "Context & Identity Check" as the first step for every task. This prevents unauthorized actions (e.g., "wipe my data" by a guest) which would negatively impact the score.

### 2. Middleware Architecture
We introduced a middleware pattern in `handlers/` to decouple cross-cutting concerns like Wiki Synchronization from the core agent logic. This allows the agent to "learn" about policy updates seamlessly without manual tool calls.

### 3. Local RAG & Caching
Instead of re-fetching wiki content or searching via the API repeatedly, the agent maintains a local vector/token index of the wiki. This allows for fast, free, and repeated searches within a task session without hitting API rate limits or costs. The system uses **Sentence Transformers** for high-quality semantic matching, running entirely on the local CPU.

### 4. Robust Error Handling
The agent is designed to be crash-resistant. It catches API validation errors (like the server returning `null` for a list) and patches them on the fly, allowing the conversation to continue instead of aborting the task.

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
