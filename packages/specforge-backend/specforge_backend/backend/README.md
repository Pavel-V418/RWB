# Backend-агент

Реализует интерфейсы `I<Tag>Api`, сгенерированные из OpenAPI (`generators/run-all.sh`), по контракту,
схеме БД и требованиям аналитика, затем пишет к ним интеграционные тесты. Шлюз — `dotnet build`
с предупреждениями как ошибками + `dotnet test` тестов фичи.

```bash
python -m specforge_backend.backend.agent <artifacts> [--tags Zones,Sessions] [--force] [--max-attempts 3] [--dry-run]
                                           [--no-tests | --tests-only] [--feedback файл]
```

| Шаг | Что происходит |
|---|---|
| контекст | `context.py`: интерфейс тега, замыкание его DTO, сущности EF, DbSet-ы, CHECK-ограничения и SQL-функции схемы, алгоритм и таблицы из `02_system/api-methods/<operationId>.md`, тексты FR (из `x-requirements` операции, иначе со страницы метода) — ~5–7 тыс. токенов на тег |
| генерация | один файл `src/App.Application/Features/<Tag>/<Tag>Api.cs`; правила каркаса и эталонный пример — в `prompts.py` |
| шлюз | `dotnet build` проекта Application; ошибки (файл, строка, код) уходят в ремонт |
| ремонт | до `--max-attempts` попыток всего; затем детерминированная заглушка (HTTP 501), сборка остаётся зелёной |
| DI | регистрации всех реализаций `I*Api` пишутся детерминированно в `DependencyInjection.Features.cs` |
| тесты | `testgen.py`: `tests/App.IntegrationTests/Features/<Tag>ApiTests.cs` по критериям приёмки (Given/When/Then из `01_requirements`), ролям операций и коду реализации; HTTP через `WebApplicationFactory`, настоящий PostGIS (Testcontainers) |
| шлюз тестов | `dotnet test --filter-class` класса фичи, отчёт JUnit; ремонт чинит сами тесты, а не подгоняет ожидания под ошибку кода |
| итог тестов | непрошедший за все попытки тест получает `[Fact(Skip = "причина")]`: CI зелёный, шлюз агента красный, причина — готовый текст для `--feedback` |
| трассируемость | имена тестов `Operation_Condition_Result` и `[Trait("AC", "AC-FR-n-mm")]` → `generators/docs/traceability.py` в конце прогона: `04_backend/traceability.md` и столбец `tests` в `02_system/traceability.csv` |
| ремонт по поведению | `--feedback файл --tags X`: ошибка работающего кода (e2e, Schemathesis, DevOps-агент) → правка от текущего файла |

Итог — `04_backend/backend-agent.json` (`{passed, errors, tokens, attempts, tags, tests}`), ответы модели — `04_backend/.agent/`.
Уже реализованные теги (в том числе написанные вручную) пропускаются, `--force` — перегенерировать.

LLM: `llm.py`, только стандартная библиотека. Настройки — `~/.specforge.env`
(`SPECFORGE_LLM_URL`, `SPECFORGE_LLM_MODEL`, `SPECFORGE_LLM_API_KEY`, `SPECFORGE_LLM_TPM=30000`).
Клиент сам держит окно 60 с под лимит токенов, выключает режим рассуждений Qwen3 и повторяет запрос при 429/5xx.
`DOTNET` — команда сборки (по умолчанию `dotnet`).

Офлайн-тест цикла (фейковая LLM, настоящий `dotnet build`, ключ не тратится):
```bash
.venv/bin/python -m unittest tests.test_backend_agent tests.test_testgen
```
