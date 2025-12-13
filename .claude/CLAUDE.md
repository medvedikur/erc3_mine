# Project Rules for ERC3 Agent Development

This document is the **single source of truth** for any coding agent working in the ERC3 Agent codebase. It defines workflow, guard-rails, and conventions for consistent, high-quality contributions.

---

## 1. Core Principles

1. **Documentation-Driven Development** – Start by reading `solution_description.md` + this file to understand architecture before making changes.
2. **Adaptable Agent** – The ERC3 agent must adapt to ANY company situation by reading wiki policies, not hardcoded for specific cases.
3. **Smart Tooling Over Instructions** – Instead of bloating prompts with edge cases, build tools/handlers that guide the agent at execution time.
4. **Compact Prompts** – Keep system prompts minimal and focused; bloated instructions cause agents to lose focus.
5. **Greppable Inline Memory** – Use `AICODE-*:` anchors to leave breadcrumbs (§ 5).
6. **Small, Safe, Reversible Commits** – Prefer many focused commits over one massive diff.

---

## 2. Language & Communication

- **Primary language**: Russian for communication, English for code/comments

---

## 3. Task Execution Protocol

### 3.1. Before Starting Any Task

1. **Read Documentation First**:
   - `solution_description.md` — Full architecture overview
   - `.claude/CLAUDE.md` — Development rules (this file)
   - `prompts.py` — System prompt structure
   - `config.py` — Current configuration

2. **Analyse** the request: dependencies, affected modules, existing patterns.

3. **Determine Complexity** (§ 3.2):
   - **Complex** → Plan Mode (draft plan, await approval)
   - **Simple** → Implement directly following best practices

### 3.2. Determining Task Complexity

A task is **complex** if it involves:
- Multiple modules (handlers/, tools/, agent/)
- New middleware or enricher patterns
- Changes to the agent execution loop
- Integration with external systems (new API, new LLM backend)
- Performance optimization or security implications
- Cross-cutting architectural concerns

When uncertain — ask for clarification or default to Plan Mode.

### 3.3. After Implementation

For complex tasks or when explicitly requested:
1. Run tests: `./venv-erc3/bin/python main.py -tests_on`
2. Verify no regressions in specific areas: `-task spec_id1,spec_id2`
3. Update documentation if architecture changed

---

## 4. Architecture Philosophy

### 4.1. Chain of Thought
Agent uses structured reasoning: `thoughts` → `plan` → `action_queue`

### 4.2. Module Structure

**agent/** — Execution loop:
- `state.py` — AgentTurnState dataclass for turn state tracking
- `parsing.py` — LLM response parsing (extract_json, OpenAIUsage)
- `loop_detection.py` — LoopDetector for repetitive action detection
- `runner.py` — Main agent loop (`run_agent()`)

**handlers/** — Middleware, action handlers, managers:
- `core.py` — DefaultActionHandler, ActionExecutor for tool execution
- `intent.py` — IntentDetector, TaskIntent for task intent detection
- `action_handlers/` — Specialized handlers (Strategy pattern):
  - `base.py` — ActionHandler ABC, CompositeActionHandler
  - `wiki.py` — WikiSearchHandler, WikiLoadHandler
- `enrichers/` — API response enrichment (Composite pattern):
  - `project_search.py` — **ProjectSearchEnricher** (composite)
  - `project_ranking.py` — ProjectRankingEnricher
  - `project_overlap.py` — ProjectOverlapAnalyzer
  - `wiki_hints.py` — WikiHintEnricher
- `middleware/` — Guards and validation:
  - `base.py` — ResponseGuard base class
  - `membership.py` — ProjectMembershipMiddleware
  - `guards/` — Domain guards (outcome, project, time, security, response)
- `wiki.py` — WikiManager for knowledge base
- `security.py` — SecurityManager for authorization
- `base.py` — ToolContext, protocols

**tools/** — Tool parsing:
- `registry.py` — ToolParser, ParseContext, ParseError
- `parser.py` — parse_action() dispatcher
- `links.py` — LinkExtractor for auto-linking
- `patches.py` — SDK runtime patches
- `normalizers.py` — Argument normalization
- `parsers/` — Domain parsers (identity, employees, wiki, customers, projects, time, response)

---

## 5. Inline Memory — `AICODE-*:` Anchors

Use language-appropriate comment tokens (`#`, `//`, etc.):

- `AICODE-NOTE:` — Important rationale linking new to legacy code
- `AICODE-TODO:` — Known follow-ups not in current scope
- `AICODE-QUESTION:` — Uncertainty that needs human review

**Example**:
```python
# AICODE-NOTE: re-uses overlap logic from project_overlap.py:42
```

Anchors are **mandatory** when:
- Code is non-obvious
- Logic mirrors or patches hard-to-find parts
- A bug-prone area is touched

Discover anchors: `grep "AICODE-" -R agent-erc3-dev/`

---

## 6. Thread Safety & Parallelism

- **Embedding model**: Global singleton with thread-safe initialization (`get_embedding_model()`)
- **WikiManager**: Disk cache per SHA1 hash — immutable once downloaded. Use thread-local instances for in-memory state.
- **SessionStats / failure_logger**: Thread-safe via `threading.Lock`
- **requests.Session**: NOT thread-safe — use thread-local sessions
- **stdout redirection**: Use `ThreadLocalStdout` dispatcher pattern

**CRITICAL**: Always pass `task_id` explicitly to `stats.add_llm_usage()` and `stats.add_api_call()` — do NOT rely on `_current_task_id` class variable (race condition in parallel mode).

---

## 7. Testing & Development

### 7.1. Running Tests
```bash
# Local tests (mock API)
./venv-erc3/bin/python main.py -tests_on

# Parallel tests
./venv-erc3/bin/python main.py -tests_on -threads 4

# Filter specific tests
./venv-erc3/bin/python main.py -tests_on -task spec_id1,spec_id2

# Against real benchmark
./venv-erc3/bin/python main.py -benchmark erc3-test
./venv-erc3/bin/python main.py -benchmark erc3-dev
```

### 7.2. Test Structure
- **Location**: `tests/` with `framework/` (core) and `cases/` (test files)
- **Naming**: `tests/cases/test_XXX_name.py` with `SCENARIO` variable
- **Documentation**: `tests/TEST_MODEL.md` describes all tasks
- **Isolated data**: Uses `wiki_dump_tests/` and `logs_tests/`

### 7.3. Logs
- **Sequential**: Console output
- **Parallel**: Per-task logs in `logs/parallel_<timestamp>/<spec_id>.log`

### 7.4. Configuration
- Central config in `config.py` (benchmark type, workspace, models, threads)
- CLI overrides: `-benchmark`, `-threads`, `-task`, `-openrouter`

---

## 8. Code Quality & Self-Verification

### 8.1. Pre-Commit Checklist (Complex Tasks)

- [ ] Tests pass: `./venv-erc3/bin/python main.py -tests_on`
- [ ] No type errors in modified files
- [ ] No `AICODE-TODO:` left in scope unless explicitly out-of-scope
- [ ] Documentation updated if architecture changed
- [ ] No hardcoded policy patterns (see § 10)

### 8.2. Code Style
- Follow existing patterns in the codebase
- Use type hints for function signatures
- Prefer explicit over implicit (avoid magic strings where possible)

---

## 9. Middleware Pattern (handlers/middleware/)

### 9.1. Three Blocking Modes

1. **Hard block** — ONLY for logically impossible actions verified via API:
   - Employee not in project team
   - Project doesn't exist
   - Stop execution, return error

2. **Soft block** — For risky actions with `warning_key` check:
   - Block first time with warning
   - Allow through on repeat
   - Use sparingly

3. **Soft hint** (PREFERRED) — Non-blocking guidance:
   - Response goes through
   - Hint appended to results
   - Agent learns and adapts

### 9.2. ⚠️ DANGER: Regex-Based Blocking

NEVER use hard block based on regex word matching — too many false positives:
- `\bproject\b` matches "project" in ANY context
- `\bpause\b` matches "let me pause to think"

If using regex detection, ALWAYS use soft hint or soft block with warning_key.

### 9.3. Safe Blocking Criteria

- ✅ API-verified state (employee membership, project existence)
- ✅ Concrete format validation in specific field contexts
- ❌ Word presence in task text (matches unrelated contexts)
- ❌ Outcome + keyword combination (too many edge cases)

### 9.4. State Tracking

Use `ctx.shared` dict to track:
- Warnings shown
- Mutations performed
- `action_types_executed`
- Current user identity

---

## 10. ⚠️ ANTI-PATTERNS — DO NOT IMPLEMENT

These approaches seem logical but create brittle, non-adaptable code:

### 10.1. Hardcoded Policy File Names
```python
# ❌ BAD
if wiki.has_page("merger.md"):
    inject_merger_policy()

# ✅ GOOD
# Agent searches wiki for relevant terms, adapts to any policy structure
```

### 10.2. Hardcoded Format Patterns
```python
# ❌ BAD
CC_PATTERN = re.compile(r'CC-[A-Z]{2}-[A-Z]{2}-\d{3}')

# ✅ GOOD
# Agent reads format requirements from wiki, validates dynamically
```

### 10.3. Domain-Specific Guards
```python
# ❌ BAD
class JiraTicketRequirementGuard:
    def check(self, ctx):
        if "pause" in task_text and "project" in task_text:
            return "JIRA ticket required"

# ✅ GOOD
# Agent reads policies from wiki, applies them contextually
```

### 10.4. Keyword-Based Blocking
```python
# ❌ BAD
if 'pause' in task_text and 'project' in task_text:
    require_jira()

# ✅ GOOD
# Agent understands context through reasoning, not keyword matching
```

**Core Principle**: The agent should be **adaptable** — capable of handling ANY company situation by reading wiki policies. If you find yourself writing code that checks for specific file names, format patterns, or business rules — STOP and teach the agent to discover these from wiki instead.

---

## 11. Enricher Pattern (handlers/enrichers/)

Enrichers analyze API responses and inject context-aware hints.

### 11.1. Design Principles

- **Single responsibility** — one aspect per enricher
- **Non-blocking** — return hints, never block execution
- **Stateless per-turn** — clear caches between turns

### 11.2. Enricher Types

- **Simple**: `enrich(data, context) -> Optional[str]`
- **Composite**: `enrich(ctx, result, task_text) -> List[str]`

### 11.3. Adding New Hints

- **New aspect of existing domain** → add method to composite:
  ```python
  def _get_new_hint(self, ctx, projects, ...) -> Optional[str]:
      return "💡 Hint text" if condition else None
  ```

- **New domain** → create new composite enricher

---

## 12. ERC3 Benchmark Context

### 12.1. Benchmark Types
- `erc3-test` (24 tasks) — Testing/development
- `erc3-dev` — Development tasks
- `erc3` — Production benchmark

### 12.2. Session Lifecycle
```
start_session → session_status (get tasks) →
  for each task: start_task → agent loop → complete_task →
finally: submit_session
```

### 12.3. Response Outcomes
- `ok_answer` — Question answered successfully
- `ok_not_found` — Data not found in system
- `none_clarification_needed` — Ambiguous, need more info
- `denied_security` — Security restriction (guest access)
- `denied_authorization` — Insufficient permissions

### 12.4. Links in Responses
Always include relevant entity links (project, employee, customer) — benchmark checks for them.

### 12.5. Change Events
Benchmark tracks mutations (`time_log`, `employees_update`, etc.) — ensure agent doesn't perform unintended mutations.

---

## 13. Documentation Maintenance

### 13.1. When to Update Documentation

Update relevant files when changes affect:
- Public APIs or tool definitions
- Architecture patterns
- Testing patterns or fixtures
- Configuration options

### 13.2. Key Files

- `solution_description.md` — Full architecture overview
- `.claude/CLAUDE.md` — Development rules (this file)
- `tests/TEST_MODEL.md` — Test case documentation

---

## 14. Environment Setup

### 14.1. Virtual Environment
```bash
# Location
./venv-erc3/

# Activation
source venv-erc3/bin/activate

# Or run directly
./venv-erc3/bin/python main.py
```

### 14.2. SDK Version
- **Required**: `erc3>=1.2.0`
- **Install**: `pip install --extra-index-url https://erc.timetoact-group.at/ 'erc3>=1.2.0'`
- **Breaking change** (1.2.0): `log_llm()` requires `completion` parameter

---

## 15. Fallback Behaviour

If uncertain:
1. Add an `AICODE-QUESTION:` inline comment
2. Ask for clarification before making assumptions
3. Prefer minimal, reversible changes
