# Project Rules for ERC3 Agent Development

## Language & Communication
- **Primary language**: Russian for communication, English for code/comments

## Architecture Philosophy
- **Adaptable agent**: The agent must adapt to ANY company situation, not be trained for specific cases
- **Compact prompts**: Keep system prompts minimal and focused — bloated instructions cause the agent to lose focus and miss important details
- **Smart tooling over instructions**: Instead of adding prompt rules for each edge case, build tools and handlers that:
  - Guide the agent in the right direction
  - Prevent obvious mistakes at execution time
  - Provide helpful hints in API responses (e.g., authorization info, disambiguation suggestions)
- **Chain of Thought**: Agent uses structured reasoning (thoughts → plan → actions) to work through complex tasks
- **handlers/ folder purpose**: Contains not just middleware but also:
  - `core.py` — ActionExecutor for tool execution
  - `wiki.py` — WikiManager for knowledge base access
  - `security.py` — SecurityManager for authorization
  - `safety.py` — Middleware guards (ambiguity detection, validation, etc.)

## Thread Safety & Parallelism
- **Embedding model**: Global singleton with thread-safe initialization (`get_embedding_model()`)
- **WikiManager**: Uses disk cache per SHA1 hash — each wiki version is immutable once downloaded to `wiki_dump/{sha1}/`. Multiple threads can safely read same version. Use thread-local WikiManager instances for in-memory state.
- **SessionStats / failure_logger**: Thread-safe via `threading.Lock`
- **requests.Session**: NOT thread-safe — use thread-local sessions
- **stdout redirection**: Use `ThreadLocalStdout` dispatcher pattern, not direct `sys.stdout` assignment (breaks other threads)
- **CRITICAL**: Always pass `task_id` explicitly to `stats.add_llm_usage()` and `stats.add_api_call()` — do NOT rely on `_current_task_id` class variable (race condition in parallel mode)

## Testing & Development
- **Single entry point**: `main.py` — default 1 thread (sequential), use `-threads N` for parallel execution
- **Benchmark selection**: Use `-benchmark erc3-test|erc3-dev|erc3` or edit `config.py` (default: erc3-test)
- **Task filtering**: Use `-task spec_id1,spec_id2` for testing specific tasks (use `force=True` on submit since unfiltered tasks remain unfinished)
- **Logs**: Parallel mode writes per-task logs to `logs/parallel_<timestamp>/<spec_id>.log`
- **Don't duplicate solutions**: After validating experimental code, merge it into the main entry point
- **Configuration**: Central config in `config.py` (benchmark type, workspace, models, threads, etc.)

## Middleware Pattern (handlers/safety.py)
- **Two blocking modes**:
  1. **Hard block**: For logically impossible actions (e.g., salary raise without amount) — stop execution, return error
  2. **Soft block**: For risky but possible actions (high error probability) — return warning, ask agent to reconsider or provide additional data. If agent confirms same action again, allow through.
- **Lightweight hints over blocking**: Prefer non-blocking hints that guide the agent (e.g., "you didn't search DB before ok_not_found") rather than fragile regex-based blocking
- **State tracking**: Use `ctx.shared` dict to track warnings shown, mutations performed, action_types_executed, etc. across middleware calls
- **Key middlewares**:
  - `AmbiguityGuardMiddleware`: Adds hint if agent responds `ok_not_found` without searching database
  - `ProjectSearchReminderMiddleware`: Reminds to search projects_search for project-related queries

## API Response Enrichment
- **Hints in responses**: API handlers can inject helpful hints into responses (not just raw data), e.g.:
  - Project overlap analysis: "felix_baum works on 3 'cv' projects, you are Lead of 1 of them"
  - Authorization context: "jonas_weiss is Lead of proj_acme_line3_cv_poc"
  - Disambiguation suggestions when multiple matches found
- **Purpose**: Guide agent without bloating system prompt — context-specific help at runtime

## ERC3 Benchmark Context
- **Benchmark types**: `erc3-test` (24 tasks, testing), `erc3-dev` (development tasks), `erc3` (production)
- **Session lifecycle**: `start_session` → `session_status` (get tasks) → for each task: `start_task` → agent loop → `complete_task` → finally `submit_session`
- **Response outcomes**: `ok_answer`, `ok_not_found`, `none_clarification_needed`, `denied_security`, `denied_authorization`
- **Links in responses**: Always include relevant entity links (project, employee, customer) — benchmark checks for them
- **Change events**: Benchmark tracks mutations (time_log, employees_update, etc.) — ensure agent doesn't perform unintended mutations

## Virtual Environment
- **venv location**: `venv-erc3/` in current directory (`./venv-erc3/`)
- **Activation**: `source venv-erc3/bin/activate` or run directly with `./venv-erc3/bin/python`

## SDK Version
- **Required**: `erc3>=1.2.0` (breaking change from 1.1.x as of Dec 7, 2025)
- **Install**: `pip install --extra-index-url https://erc.timetoact-group.at/ 'erc3>=1.2.0'`
- **Breaking change**: `log_llm()` now requires `completion` parameter (raw LLM response)

## Local Test Framework
- **Purpose**: Test agent behavior locally with mock API before running against real benchmark
- **Location**: `tests/` directory with `framework/` (core components) and `cases/` (test files)
- **Run tests**: `python main.py -tests_on` (instead of benchmark tasks)
- **Parallel tests**: `python main.py -tests_on -threads 4`
- **Filter tests**: `python main.py -tests_on -task spec_id1,spec_id2`
- **Test documentation**: `tests/TEST_MODEL.md` describes all 24 benchmark tasks + custom tests
- **Isolated data**: Tests use `wiki_dump_tests/` and `logs_tests/` to avoid polluting production data
- **Test structure**: One file per test in `tests/cases/test_XXX_name.py` with `SCENARIO` variable
- **Mock API**: `MockErc3Client` intercepts API calls and returns mock data
- **Evaluation**: Same as benchmark - checks outcome + links match expected values
