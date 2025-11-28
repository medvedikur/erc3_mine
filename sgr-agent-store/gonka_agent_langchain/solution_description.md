# Gonka Network SGR Agent (LangChain Edition)

This solution reimplements the SGR (Schema-Guided Reasoning) agent using **LangChain** and runs on the Gonka Network (Decentralized Inference).

## Structure
- `main.py`: Entry point handling session loop and configuration.
- `agent.py`: LangChain-based agent loop implementing the "Mental Protocol".
- `gonka_llm.py`: Custom `GonkaChatModel` for LangChain with built-in node failover and retry logic.
- `tools.py` & `models.py`: Tool definitions and Pydantic schemas for structured reasoning.
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

## Usage
Run `python sgr-agent-store/gonka_agent_langchain/main.py`.
Ensure `GONKA_PRIVATE_KEY` is set in `.env`.

