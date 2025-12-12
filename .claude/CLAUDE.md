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
- **agent/ folder purpose**: Agent execution module (refactored):
  - `state.py` — AgentTurnState dataclass for turn state tracking
  - `parsing.py` — LLM response parsing (extract_json, OpenAIUsage)
  - `loop_detection.py` — LoopDetector for repetitive action detection
  - `runner.py` — Main agent loop (`run_agent()`)
- **handlers/ folder purpose**: Contains middleware, action handlers, and managers:
  - `core.py` — DefaultActionHandler, ActionExecutor for tool execution
  - `intent.py` — IntentDetector, TaskIntent for task intent detection
  - `action_handlers/` — Specialized handlers (Strategy pattern):
    - `base.py` — ActionHandler ABC, CompositeActionHandler
    - `wiki.py` — WikiSearchHandler, WikiLoadHandler
  - `enrichers/` — API response enrichment (Composite pattern):
    - `project_search.py` — **ProjectSearchEnricher** (composite) combines all project hints
    - `project_ranking.py` — ProjectRankingEnricher for search disambiguation
    - `project_overlap.py` — ProjectOverlapAnalyzer for authorization hints
    - `wiki_hints.py` — WikiHintEnricher for task-relevant wiki suggestions
  - `middleware/` — Middleware guards:
    - `base.py` — ResponseGuard base class, utility functions
    - `membership.py` — ProjectMembershipMiddleware
    - `guards/` — Domain-organized guard classes:
      - `outcome_guards.py` — AmbiguityGuard, OutcomeValidation, SubjectiveQueryGuard
      - `project_guards.py` — ProjectSearchReminder, ProjectModificationClarificationGuard
      - `time_guards.py` — TimeLoggingClarificationGuard
      - `security_guards.py` — BasicLookupDenialGuard, PublicUserSemanticGuard
      - `response_guards.py` — ResponseValidationMiddleware
  - `wiki.py` — WikiManager for knowledge base access
  - `security.py` — SecurityManager for authorization
  - `base.py` — ToolContext, ActionHandlerProtocol, Middleware protocols
- **tools/ folder purpose**: Tool parsing module:
  - `registry.py` — ToolParser, ParseContext, ParseError
  - `parser.py` — parse_action() dispatcher
  - `links.py` — LinkExtractor for auto-linking
  - `patches.py` — SDK runtime patches
  - `normalizers.py` — Argument normalization
  - `parsers/` — Domain-organized tool parsers:
    - `identity.py`, `employees.py`, `wiki.py`, `customers.py`, `projects.py`, `time.py`, `response.py`

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

## Middleware Pattern (handlers/middleware/)
- **Three blocking modes**:
  1. **Hard block**: ONLY for logically impossible actions verified via API (e.g., employee not in project team). Stop execution, return error.
  2. **Soft block**: For risky actions with `warning_key` check — block first time, allow through on repeat. Use sparingly.
  3. **Soft hint** (PREFERRED): Non-blocking hints that guide the agent. Response goes through, hint is just added to results.

- **⚠️ DANGER: Regex-based blocking**:
  - NEVER use hard block based on regex word matching — too many false positives
  - Example: `\bproject\b` matches "project" in ANY context, not just project modifications
  - Example: `\bpause\b` matches "let me pause to think" not just "pause the project"
  - If using regex detection, ALWAYS use soft hint (no blocking) or soft block with warning_key

- **Safe blocking criteria**:
  - ✅ API-verified state (employee membership, project existence)
  - ✅ Concrete format validation (CC-XXX-XXX-XXX pattern in specific field)
  - ❌ Word presence in task text (can match unrelated contexts)
  - ❌ Outcome + keyword combination (too many edge cases)

- **State tracking**: Use `ctx.shared` dict to track warnings shown, mutations performed, action_types_executed, etc.
- **Key middlewares**:
  - `AmbiguityGuardMiddleware`: Soft hint if `ok_not_found` without DB search
  - `ProjectSearchReminderMiddleware`: Soft block for project queries without projects_search
  - `BasicLookupDenialGuard`: Soft hint for denied_security on org-chart lookups
  - `ProjectModificationClarificationGuard`: Soft hint for clarification without project link

## Enricher Pattern (handlers/enrichers/)
Enrichers analyze API responses and inject context-aware hints. Use **Composite pattern** for domain grouping.

- **Simple enricher**: `enrich(data, context) -> Optional[str]` — single-aspect hint
- **Composite enricher**: `enrich(ctx, result, task_text) -> List[str]` — combines sub-enrichers
- **Design principles**:
  - Single responsibility — one aspect per enricher
  - Non-blocking — return hints, never block execution
  - Stateless per-turn — clear caches between turns
- **Adding new hints**:
  - New aspect of existing domain → add method to composite (e.g., `ProjectSearchEnricher._get_new_hint()`)
  - New domain → create new composite enricher (e.g., `CustomerSearchEnricher`)
- **Current composites**:
  - `ProjectSearchEnricher`: overlap, ranking, archived hints, auth reminder, membership confirmation

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
