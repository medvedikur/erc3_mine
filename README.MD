# ERC3 Agents

Schema-Guided Reasoning (SGR) agents for ERC3 (Enterprise RAG Challenge Part 3), with a production-focused implementation in `agent-erc3-dev`.

This repository contains:
- A main ERC3 agent with benchmark runner, guardrails, wiki-aware retrieval, and local test harness.
- Historical/prototype implementations in `sgr-agent-store`.
- Internal development guidelines in `.claude/` that define architecture and quality rules.

## Main Project

The primary codebase is:
- `agent-erc3-dev/` - current ERC3 agent implementation

The root-level `wiki_dump/` and `sgr-agent-store/` folders are legacy/support artifacts and can be treated as secondary.

## What The Agent Does

- Solves ERC3 tasks via an iterative loop: `thoughts -> plan -> action_queue`.
- Uses ERC3 API tools for identity, employees, projects, customers, time tracking, and wiki access.
- Applies middleware guards for security, ambiguity handling, pagination correctness, and policy compliance.
- Keeps a local versioned wiki cache and performs hybrid search (regex + keyword + semantic embeddings).
- Supports sequential and parallel benchmark execution.
- Supports local offline-style test scenarios with a mock API runner.

## Architecture (Current)

Execution flow:

```text
Task -> run_agent() -> LLM JSON -> parser/normalizers -> action pipeline
     -> middleware + enrichers + security -> ERC3 API call(s)
     -> context feedback -> next turn or final respond
```

Core modules in `agent-erc3-dev/`:
- `main.py` - CLI entry point, backend selection, benchmark/test mode switch.
- `agent/` - turn state, loop detection, parsing, message building, main loop.
- `handlers/` - action executor, middleware guards, enrichers, wiki manager, pipelines.
- `tools/` - tool registry/parsers, argument normalization, link extraction.
- `session/` - session lifecycle, sequential/parallel execution, submission.
- `tests/` - local framework and scenario-based test cases.
- `llm_provider.py` - Gonka/OpenRouter backend implementations.

## Supported Tool Domains

Registered tool families:
- Identity: `who_am_i`
- Employees: `employees_list`, `employees_search`, `employees_get`, `employees_update`
- Projects: `projects_list`, `projects_search`, `projects_get`, `projects_team_update`, `projects_status_update`
- Customers: `customers_list`, `customers_search`, `customers_get`
- Time: `time_log`, `time_get`, `time_search`, `time_update`, `time_void`, `time_summary_employee`, `time_summary_project`
- Wiki: `wiki_list`, `wiki_load`, `wiki_search`, `wiki_update`
- Finalization: `respond`

## Development Principles (From `.claude/CLAUDE.md`)

These are the key rules that shaped this implementation:
- Documentation-driven changes (`solution_description.md`, prompt/config files first).
- Adaptive behavior over hardcoded business policies.
- Smart tooling/handlers over oversized prompts.
- Clear reasoning memory anchors via `AICODE-*` comments:
  - `AICODE-NOTE`
  - `AICODE-TODO`
  - `AICODE-QUESTION`
- Middleware blocking strategy:
  - Hard block only for API-verified impossible states.
  - Prefer soft hints/soft blocks for ambiguity and risky actions.
- Thread safety requirements in parallel mode:
  - Explicit `task_id` tracking for usage stats.
  - No shared mutable request session misuse.

## Prerequisites

- Python 3.11+ (3.12 used in this repo).
- ERC3 API key.
- Gonka credentials (default backend for benchmark runs).
- Optional OpenRouter key for fallback/debug.
- Internet access for dependencies and initial embedding model download (`all-MiniLM-L6-v2`).

## Setup

```bash
cd agent-erc3-dev
python3 -m venv venv-erc3
source venv-erc3/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Create `agent-erc3-dev/.env` manually:

```bash
# Required
ERC3_API_KEY=key-...

# Default backend (Gonka)
GONKA_PRIVATE_KEY=...
MODEL_ID_GONKA=Qwen/Qwen3-235B-A22B-Instruct-2507-FP8
PRICING_MODEL_ID=qwen/qwen3-235b-a22b

# Optional OpenRouter backend
OPENAI_API_KEY=sk-or-v1-...
OPENAI_BASE_URL=https://openrouter.ai/api/v1
MODEL_ID_OPENROUTER=qwen/qwen3-235b-a22b-2507
PRICING_MODEL_ID_OPENROUTER=qwen/qwen3-235b-a22b-2507
```

## Running

From `agent-erc3-dev/`:

```bash
# Benchmark run (default backend: Gonka)
./venv-erc3/bin/python main.py

# Benchmark run with explicit benchmark id
./venv-erc3/bin/python main.py -benchmark erc3-prod
./venv-erc3/bin/python main.py -benchmark erc3-test
./venv-erc3/bin/python main.py -benchmark erc3-dev

# Parallel benchmark
./venv-erc3/bin/python main.py -threads 4

# Run selected benchmark tasks only
./venv-erc3/bin/python main.py -task spec_id_1,spec_id_2

# Optional OpenRouter fallback/debug
./venv-erc3/bin/python main.py -openrouter
```

Note:
- The default and preferred benchmark path in this project is Gonka.
- Primary benchmark used by this team is `erc3-prod`.
- `-openrouter` is intended for fallback/debug scenarios.

## Local Tests

The repository includes a local scenario framework (`tests/framework/`) and 37 scenario files in `tests/cases/`.

```bash
# Run full local suite
./venv-erc3/bin/python main.py -tests_on

# Run one or more local scenarios
./venv-erc3/bin/python main.py -tests_on -task ambiguous_project_search
./venv-erc3/bin/python main.py -tests_on -task spec1,spec2

# Parallel local tests
./venv-erc3/bin/python main.py -tests_on -threads 4
```

Reference:
- `agent-erc3-dev/tests/TEST_MODEL.md` - test catalog and expected behavior.

## Logs And Debugging

- Benchmark logs:
  - `agent-erc3-dev/logs/run_*`
  - `agent-erc3-dev/logs/parallel_*/`
- Local test logs:
  - `agent-erc3-dev/logs_tests/test_run_*/`

Useful debug workflow:
- Analyze failing task logs and verify final `outcome` + `links`.
- For deep log QA, use instructions in `.claude/agents/tester-digger.md`.
- For run summary format, use `.claude/templates/SUMMARY_TEMPLATE.md`.

## Configuration

Main config file:
- `agent-erc3-dev/config.py`

Key options:
- `BENCHMARK` (`erc3-prod`, `erc3-test`, `erc3-dev`)
- `WORKSPACE`
- `SESSION_NAME`
- `COMPETITION_FLAGS`
- `MAX_TURNS_PER_TASK`

CLI flags:
- `-openrouter`
- `-task <comma-separated spec_id>`
- `-threads <int>`
- `-verbose`
- `-tests_on`
- `-benchmark <name>`

## Pre-Publication Checklist

Before publishing this repository publicly:
- Add a `LICENSE` file (currently missing).
- Ensure no secrets are committed (`.env`, API keys, private keys).
- Remove local virtual environments (`venv*`) from tracked content.
- Remove or trim heavy local artifacts if not needed for users:
  - `logs/`, `logs_tests/`, `wiki_dump*/`
- Verify setup steps on a clean machine.
- Run at least:
  - `./venv-erc3/bin/python main.py -tests_on`
  - `./venv-erc3/bin/python main.py -tests_on -threads 4`

## Contributing

Recommended contribution workflow:
- Keep PRs small and focused.
- Preserve adaptive behavior (no hardcoded business policy shortcuts).
- Add/adjust scenario tests for behavior changes.
- Keep response `links` complete and consistent with entities mentioned in answers.
- Team convention from current internal workflow: communication in Russian, code/comments in English.

## License

License is not defined yet in this repository. Add a `LICENSE` file before public release.
