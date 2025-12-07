# ERC3 Agent - Enterprise RAG Challenge Part 3

An intelligent agent for the [ERC3: AI Agents in Action](https://www.timetoact-group.at/events/enterprise-rag-challenge-part-3) competition, designed to handle complex enterprise tasks with authorization-aware reasoning.

## Architecture

The agent uses **Schema-Guided Reasoning (SGR)** with structured Chain-of-Thought:

```
User Task → Agent Loop → [thoughts → plan → actions] → API Calls → Response
```

### Key Components

- **`main.py`** - Entry point, session management, benchmark integration
- **`agent.py`** - Core agent loop with SGR reasoning
- **`prompts.py`** - System prompts and tool definitions
- **`tools.py`** - Action parser, parameter normalization, validation
- **`handlers/`** - Modular middleware system:
  - `core.py` - ActionExecutor for tool execution
  - `wiki.py` - WikiManager for knowledge base access with RAG
  - `security.py` - Authorization checks and data redaction
  - `safety.py` - Ambiguity detection, guardrails, soft/hard blocks

### Design Philosophy

- **Smart tooling over instructions** - Instead of bloated prompts, the system guides the agent through API response enrichment (hints, disambiguation suggestions, authorization context)
- **Middleware pattern** - Hard blocks for impossible actions, soft blocks with warnings for risky actions
- **Adaptive agent** - Works with any company structure, not trained for specific cases

## Quick Start

### 1. Prerequisites

- Python 3.11+
- ERC3 API key (from https://erc.timetoact-group.at/)
- LLM API key (OpenAI, OpenRouter, or Anthropic)

### 2. Setup

```bash
cd agent-erc3-dev

# Create virtual environment
python3 -m venv venv-erc3
source venv-erc3/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys
```

### 3. Run

```bash
# Run with OpenAI
python main.py

# Run with OpenRouter (for other models)
python main.py -openrouter

# Run specific benchmark
python main.py -benchmark erc3-test
```

## Local Testing

The project includes a local test framework for developing and debugging without hitting the real benchmark.

```bash
# Run all local tests
python main.py -tests_on -openrouter

# Run specific test
python main.py -tests_on -openrouter -task ambiguous_project_search

# Run tests in parallel
python main.py -tests_on -openrouter -threads 4
```

Test cases are in `tests/cases/` - see `tests/TEST_MODEL.md` for documentation.

## Configuration

Key environment variables:

| Variable | Description |
|----------|-------------|
| `ERC3_API_KEY` | Competition API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `OPENROUTER_API_KEY` | OpenRouter API key (for alternative models) |

## Project Structure

```
agent-erc3-dev/
├── main.py              # Entry point
├── agent.py             # Agent loop
├── prompts.py           # System prompts
├── tools.py             # Action parsing
├── handlers/
│   ├── core.py          # Tool execution
│   ├── wiki.py          # Knowledge base RAG
│   ├── security.py      # Authorization
│   └── safety.py        # Guardrails
├── tests/
│   ├── framework/       # Test infrastructure
│   └── cases/           # Test scenarios
└── wiki_dump/           # Cached wiki content
```

## Resources

- [Competition Page](https://www.timetoact-group.at/events/enterprise-rag-challenge-part-3)
- [ERC3 Platform](https://erc.timetoact-group.at/)
- Discord channel (link in registration email)

## License

MIT
