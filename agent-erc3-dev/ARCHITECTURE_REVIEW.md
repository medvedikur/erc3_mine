# Архитектурный обзор и план рефакторинга

> **Статус**: Завершено
> **Последнее обновление**: 2025-12-11

## Текущее состояние

### Метрики кода
| Файл | Строк | Главная проблема | Статус |
|------|-------|------------------|--------|
| `handlers/core.py` | 816 | Декомпозиция завершена | ✅ Рефакторинг завершён |
| `handlers/action_handlers/` | 241 | Новый модуль | ✅ Создан |
| `handlers/enrichers/` | ~470 | Новый модуль | ✅ Создан |
| `agent/` | ~550 | Новый модуль | ✅ Создан |
| `tools/` | ~1170 | Новый модуль (из tools.py) | ✅ Создан |
| `handlers/middleware/` | ~550 | Новый модуль (из safety.py) | ✅ Создан |
| `handlers/safety.py` | 41 | Re-exports (wrapper) | ✅ Рефакторинг завершён |
| `main.py` | 631 | CLI и бизнес-логика | 🟢 OK |

### Хорошие паттерны (оставить как есть)
- [x] **Registry Pattern** в `ToolParser` - отлично работает
- [x] **Middleware Chain** в `safety.py` - правильный подход
- [x] **State Dataclass** `AgentTurnState` - типизированное состояние
- [x] **Fetch-Merge-Dispatch** для partial updates
- [x] **CompositeActionHandler** - Strategy pattern для handlers
- [x] **LoopDetector** - выделенный класс для обнаружения циклов
- [x] **LinkExtractor** - выделенный класс для извлечения ссылок

---

## Текущая структура проекта

```
agent-erc3-dev/
├── agent/                    # NEW - Agent execution module
│   ├── __init__.py           # Exports
│   ├── state.py              # AgentTurnState dataclass
│   ├── parsing.py            # extract_json, OpenAIUsage
│   ├── loop_detection.py     # LoopDetector class
│   └── runner.py             # run_agent() main loop
├── handlers/
│   ├── base.py               # ToolContext, Middleware protocols
│   ├── core.py               # DefaultActionHandler, ActionExecutor (972 строки)
│   ├── action_handlers/      # Strategy pattern для действий
│   │   ├── __init__.py
│   │   ├── base.py           # ActionHandler ABC, CompositeActionHandler
│   │   └── wiki.py           # WikiSearchHandler, WikiLoadHandler
│   ├── enrichers/            # Обогащение API ответов
│   │   ├── __init__.py
│   │   ├── project_ranking.py    # ProjectRankingEnricher
│   │   ├── project_overlap.py    # ProjectOverlapAnalyzer
│   │   └── wiki_hints.py         # WikiHintEnricher
│   ├── middleware/           # Middleware guards (refactored)
│   │   ├── __init__.py       # Exports
│   │   ├── base.py           # ResponseGuard, utility functions
│   │   ├── response_guards.py # 10 response guard classes
│   │   └── membership.py     # ProjectMembershipMiddleware
│   ├── wiki.py               # WikiManager
│   ├── security.py           # SecurityManager
│   └── safety.py             # Re-exports from middleware/ (wrapper)
├── tools/                    # NEW - Tool parsing module
│   ├── __init__.py           # Exports
│   ├── registry.py           # ToolParser, ParseContext, ParseError
│   ├── links.py              # LinkExtractor class
│   ├── patches.py            # SafeReq_UpdateEmployeeInfo, SDK patches
│   ├── normalizers.py        # Argument normalization utilities
│   └── parser.py             # parse_action() and tool parsers
├── prompts.py                # System prompts
├── main.py                   # CLI entry point
└── config.py                 # Configuration
```

---

## Завершённые фазы

### Фаза 1: Декомпозиция `handle()` ✅ ЗАВЕРШЕНО

**Результат**: Wiki handlers выделены

**Выполнено**:
1. [x] Создать `handlers/action_handlers/` структуру
2. [x] Написать `ActionHandler` protocol
3. [x] Выделить `WikiSearchHandler`
4. [x] Выделить `WikiLoadHandler`
5. [x] Создать `CompositeActionHandler`
6. [x] Обновить `ActionExecutor`

**Дополнительно выполнено**:
- [x] Выделить `ProjectSearchHandler`
- [x] Выделить `EmployeeSearchHandler`

### Фаза 2: Enrichers ✅ ЗАВЕРШЕНО

**Результат**: core.py 1402→972 строк (-430)

**Выполнено**:
1. [x] Создать `handlers/enrichers/`
2. [x] Выделить `ProjectRankingEnricher`
3. [x] Выделить `ProjectOverlapAnalyzer`
4. [x] Выделить `WikiHintEnricher`

### Фаза 3: Рефакторинг `run_agent()` ✅ ЗАВЕРШЕНО

**Результат**: agent.py 711→21 строк, логика в `agent/` модуле

**Выполнено**:
1. [x] Создать `agent/` модуль
2. [x] Выделить `AgentTurnState` в `agent/state.py`
3. [x] Выделить `extract_json`, `OpenAIUsage` в `agent/parsing.py`
4. [x] Выделить `LoopDetector` в `agent/loop_detection.py`
5. [x] Создать `agent/runner.py` с чистым `run_agent()`
6. [x] Сохранить обратную совместимость через `agent.py` wrapper

---

### Фаза 4: Организация tools.py ✅ ЗАВЕРШЕНО

**Результат**: tools.py 930→23 строк, логика в `tools/` модуле

**Выполнено**:
1. [x] Создать `tools/` модуль
2. [x] Выделить `LinkExtractor` в `tools/links.py`
3. [x] Выделить SDK patches в `tools/patches.py`
4. [x] Выделить normalizers в `tools/normalizers.py`
5. [x] Перенести парсеры в `tools/parser.py`
6. [x] Создать `tools/registry.py` для ToolParser

---

### Фаза 5: Группировка middleware ✅ ЗАВЕРШЕНО

**Результат**: safety.py 879→41 строк, логика в `handlers/middleware/` модуле

**Выполнено**:
1. [x] Создать `handlers/middleware/` структуру
2. [x] Выделить `ResponseGuard` и утилиты в `base.py`
3. [x] Выделить 10 Response Guards в `response_guards.py`
4. [x] Выделить `ProjectMembershipMiddleware` в `membership.py`
5. [x] Оставить `safety.py` как re-export wrapper

---

## Оставшиеся задачи (низкий приоритет)

- [x] Выделить `ProjectSearchHandler` из core.py ✅
- [x] Выделить `EmployeeSearchHandler` из core.py ✅

---

## Приоритеты

| Приоритет | Задача | Влияние | Статус |
|-----------|--------|---------|--------|
| P0 | Разбить `handle()` на handlers | Высокое | ✅ Завершено |
| P1 | Выделить enrichers | Среднее | ✅ Завершено |
| P2 | Рефакторинг `run_agent()` | Среднее | ✅ Завершено |
| P3 | Организация tools.py | Низкое | ✅ Завершено |
| P3 | Группировка middleware | Низкое | ✅ Завершено |

---

## Метрики успеха

- [x] Ни один метод > 100 строк (частично — handle() разбит на handlers)
- [ ] Ни один файл > 500 строк (core.py: 816 — приемлемо, но можно улучшить)
- [x] Каждый класс имеет single responsibility
- [x] Все тесты проходят (37/37)
- [x] Основной код покрыт docstrings

---

## История изменений

| Дата | Изменение |
|------|-----------|
| 2025-12-11 | Создан первоначальный документ |
| 2025-12-11 | Фаза 1 частично: WikiSearchHandler, WikiLoadHandler, CompositeActionHandler |
| 2025-12-11 | Фаза 2 завершена: ProjectRankingEnricher, WikiHintEnricher, ProjectOverlapAnalyzer. core.py: 1402→972 (-430) |
| 2025-12-11 | Фаза 3 завершена: agent/ модуль создан. agent.py: 711→21 строк. Тесты: 37/37 ✅ |
| 2025-12-11 | Фаза 4 завершена: tools/ модуль создан. tools.py: 930→23 строк. LinkExtractor выделен. Тесты: 37/37 ✅ |
| 2025-12-11 | Фаза 5 завершена: middleware/ модуль создан. safety.py: 879→41 строк. 11 middleware классов разнесены. Тесты: 37/37 ✅ |
| 2025-12-11 | Фаза 1 полностью завершена: ProjectSearchHandler, EmployeeSearchHandler выделены. core.py: 972→816 строк. Все handlers интегрированы. |
