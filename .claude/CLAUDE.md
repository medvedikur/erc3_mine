# Project Rules for ERC3 Agent Development

Single source of truth for coding agents. Defines workflow, guard-rails, and conventions.

---

## 1. Core Principles

1. **Documentation-Driven** – Read `solution_description.md` + this file before changes
2. **Adaptable Agent** – Agent reads wiki policies, no hardcoded business rules
3. **Smart Tooling** – Build tools/handlers instead of bloating prompts
4. **Compact Prompts** – Minimal and focused; bloat causes agents to lose focus
5. **Greppable Memory** – Use `AICODE-*:` anchors (§5)
6. **Small Commits** – Many focused commits over one massive diff

---

## 2. Language

- **Communication**: Russian
- **Code/comments**: English

---

## 3. Task Protocol

### Before Starting
1. Read: `solution_description.md`, `.claude/CLAUDE.md`, `prompts.py`, `config.py`
2. Analyze dependencies, affected modules, existing patterns
3. **Complex task?** → Plan Mode. **Simple?** → Implement directly

**Complex = multiple modules, new patterns, loop changes, external integrations, security implications**

### After Implementation
```bash
./venv-erc3/bin/python main.py -tests_on           # Run tests
./venv-erc3/bin/python main.py -tests_on -task X   # Specific tests
```

---

## 4. Architecture

### Chain of Thought
`thoughts` → `plan` → `action_queue`

### Key Modules

| Directory | Purpose |
|-----------|---------|
| `agent/` | Execution loop: `runner.py`, `state.py`, `parsing.py`, `loop_detection.py` |
| `handlers/` | Middleware, action handlers, managers |
| `handlers/enrichers/` | API response hints (Composite pattern) |
| `handlers/middleware/` | Guards and validation |
| `handlers/action_handlers/` | Specialized handlers (Strategy pattern) |
| `tools/` | Tool parsing: `registry.py`, `parser.py`, `links.py` |
| `tools/parsers/` | Domain parsers (identity, employees, wiki, etc.) |

---

## 5. Inline Memory — `AICODE-*:` Anchors

```python
# AICODE-NOTE: rationale linking to legacy code
# AICODE-TODO: known follow-up, out of current scope
# AICODE-QUESTION: needs human review
```

**Mandatory when**: code is non-obvious, mirrors hidden logic, or touches bug-prone areas.

Discover: `grep "AICODE-" -R agent-erc3-dev/`

---

## 6. Thread Safety

| Component | Safety |
|-----------|--------|
| Embedding model | Global singleton, thread-safe init |
| WikiManager | Disk cache immutable; use thread-local for in-memory |
| SessionStats | `threading.Lock` |
| requests.Session | NOT thread-safe — use thread-local |

**CRITICAL**: Always pass `task_id` explicitly to `stats.add_llm_usage()` — never rely on class variable (race condition).

---

## 7. Testing

### LLM Backend: Gonka Network (ОБЯЗАТЕЛЬНО!)

**⚠️ КРИТИЧЕСКИ ВАЖНО: Используем ТОЛЬКО Gonka Network!**

```bash
# ✅ ПРАВИЛЬНО — Gonka (бесплатно!)
./venv-erc3/bin/python main.py -benchmark erc3-prod -threads 10

# ❌ НЕПРАВИЛЬНО — НЕ использовать OpenRouter!
./venv-erc3/bin/python main.py -openrouter -benchmark erc3-prod  # ЗАПРЕЩЕНО!
```

**Почему Gonka:**
- Бесплатная LLM сеть
- Та же модель (Qwen3-235B) что и OpenRouter
- Флаг `-openrouter` — только для экстренной отладки с явного разрешения

### Local Tests

```bash
./venv-erc3/bin/python main.py -tests_on              # Local tests
./venv-erc3/bin/python main.py -tests_on -threads 4   # Parallel
./venv-erc3/bin/python main.py -benchmark erc3-test   # Real benchmark
```

- **Tests**: `tests/cases/test_XXX_name.py` with `SCENARIO` variable
- **Logs**: `logs/parallel_<timestamp>/<spec_id>.log`
- **Config**: `config.py`, CLI overrides: `-benchmark`, `-threads`, `-task`

### Benchmark Strategy (IMPORTANT)

**Все команды БЕЗ флага `-openrouter`!** Gonka бесплатна.

1. **Отладка конкретных тестов**: Изолированные запуски
   ```bash
   ./venv-erc3/bin/python main.py -benchmark erc3-prod -task t081   # Single test
   ./venv-erc3/bin/python main.py -benchmark erc3-prod -task t081,t097,t056  # Few tests
   ```

2. **Проверка регрессий**: Батч из 3-5 тестов
   ```bash
   ./venv-erc3/bin/python main.py -benchmark erc3-prod -threads 3 -task t009,t010,t012
   ```

3. **Полный benchmark**: Когда ожидаем 103/103
   ```bash
   ./venv-erc3/bin/python main.py -benchmark erc3-prod -threads 10
   ```
   - Перед полным запуском — проверить фиксы изолированно
   - Запустить проблемные тесты 3-5 раз для подтверждения стабильности

---

## 8. Code Quality

### Pre-Commit (Complex Tasks)
- [ ] Tests pass
- [ ] No type errors in modified files
- [ ] No open `AICODE-TODO:` in scope
- [ ] No hardcoded policy patterns (§10)

### Style
- Follow existing patterns
- Type hints for signatures
- Explicit over implicit

---

## 9. Middleware Pattern

### Three Blocking Modes

| Mode | When | Behavior |
|------|------|----------|
| **Hard block** | API-verified impossible (employee not in team) | Stop, return error |
| **Soft block** | Risky action with `warning_key` | Block first, allow on repeat |
| **Soft hint** (preferred) | Guidance needed | Append hint, don't block |

### Danger: Regex-Based Blocking
NEVER hard block on regex word matching — false positives everywhere.
Use soft hint or soft block with `warning_key`.

### Safe Blocking
- ✅ API-verified state
- ✅ Format validation in specific fields
- ❌ Word presence in task text
- ❌ Outcome + keyword combinations

---

## 10. Anti-Patterns

**DO NOT hardcode business rules.** Agent must discover from wiki.

```python
# BAD: Hardcoded policy
if wiki.has_page("merger.md"): inject_merger_policy()
if 'pause' in task_text: require_jira()

# GOOD: Agent searches wiki, adapts to any structure
```

---

## 11. Enricher Pattern

Enrichers analyze API responses, inject context-aware hints.

- **Single responsibility** — one aspect per enricher
- **Non-blocking** — return hints, never block
- **Stateless** — clear caches between turns

**Types**: `enrich(data, ctx) -> Optional[str]` or composite `enrich(ctx, result, task) -> List[str]`

---

## 12. ERC3 Benchmark

### Lifecycle
```
start_session → session_status → [start_task → agent loop → complete_task]* → submit_session
```

### Outcomes
| Code | Meaning |
|------|---------|
| `ok_answer` | Success |
| `ok_not_found` | Data not in system |
| `none_clarification_needed` | Ambiguous request |
| `none_unsupported` | Action not supported by system |
| `denied_security` | Access denied (guest or insufficient permissions) |

**Always include entity links** — benchmark checks them.

---

## 13. Environment

```bash
./venv-erc3/bin/python main.py  # Run directly
```

**SDK**: `erc3>=1.2.0` (breaking: `log_llm()` requires `completion`)

---

## 14. Test Log Analysis

Use `tester-digger` sub-agent for:
- Analyzing `logs/parallel_*/` results
- Verifying agent correctness
- Deep test case verification

```python
Task(
  subagent_type="general-purpose",
  prompt="Analyze test log using .claude/agents/tester-digger.md. Log: {path}"
)
```

**After analysis**: Create `SUMMARY.md` using template from `.claude/templates/SUMMARY_TEMPLATE.md`

---

## 15. Fallback

If uncertain:
1. Add `AICODE-QUESTION:` comment
2. Ask for clarification
3. Prefer minimal, reversible changes
