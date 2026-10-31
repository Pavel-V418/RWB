# Передача участнику 3: интеграция backend и инфраструктуры

Репозиторий уже объединяет платформу (участник 1), анализ (участник 2, `packages/specforge-analysis`)
и web (участник 4, `packages/specforge-web`). Здесь — что нужно знать, чтобы подключить свою часть,
не задев чужие.

## 1. Что изменилось в последней сборке

- `packages/specforge-analysis` — новая версия участника 2. Список изменений:
  `packages/specforge-analysis/CHANGES.md`. Интерфейс для платформы прежний
  (`stage_requirements.run`, `stage_system.run`, `stage_docs.run`).
- `agents/_team/bridge.py` — две строки: ошибка `E_LLM` (модель не ответила) теперь даёт этапу
  `failed`, а не `degraded`, чтобы следующие этапы не запускались без входных данных.

## 2. Как подключить свою часть

Тот же путь, что у участников 2 и 4 (`docs/AGENT_SDK.md`, §13):

1. Код — пакетом в `packages/<ваш-пакет>/` (свой `pyproject.toml`, свои тесты) или агентом прямо в
   `agents/backend_coder/` по шаблону `docs/AGENT_SDK.md` (пример `BackendCoder` там есть).
2. Тонкий адаптер `agents/<имя>/agent.py` с `AGENT = ...`. Пример для готовой части с контрактом
   `run(input_dir, output_dir, llm) -> GateResult` — `agents/system_architect/agent.py`.
3. `AgentSpec`: `stage="04_backend"` (имя в `config/repo_layout.yaml` помечено как предположение —
   подтвердите или поменяйте там), `depends_on=["system_architect"]`.
4. Добавьте себя в `depends_on` у `tech_writer` (`agents/tech_writer/agent.py`), чтобы документация
   собиралась после backend и видела ваши файлы.
5. Пакет из `packages/` uv-workspace подхватит сам (`members = ["packages/*"]`); добавьте его в
   `dependencies` и `[tool.uv.sources]` (`<пакет> = { workspace = true }`) корневого
   `pyproject.toml`, а тесты — в `testpaths`.

Модель — только через `ctx.llm` (`LLMClient` платформы), не своим клиентом.

## 3. Что backend получает от анализа (`artifacts/02_system/`)

| Файл | Что важно |
|---|---|
| `openapi.yaml` | OpenAPI 3.1. У каждой операции `x-requirements` (FR) и `x-roles`. Ошибки — `application/problem+json` (RFC 9457) с полем `code` (snake_case) и объектом `errors` для ошибок валидации. У каждого метода есть 401, 429, 500; ошибка внешнего поставщика — 502 `external_provider_error`; 503 не используется. Создание ресурса — заголовок `Idempotency-Key`. |
| `schema.sql` | PostgreSQL 17 + PostGIS: enum-типы, внешние ключи, GIST-индексы. **Новое:** если имя поля — зарезервированное слово SQL (`order`, `from`, `end`…), колонка в кавычках (`"order"`); в EF Core для неё нужен `HasColumnName("order")`, шлюз пишет предупреждение `W_DDL_RESERVED_NAME`. |
| `schema.platform.sql` | **Новое:** служебные таблицы `outbox_events` и `idempotency_keys` вынесены сюда — они считаются частью вашего каркаса backend и в `schema.sql` больше не входят. Сверьте со своими таблицами. |
| `asyncapi.yaml` | Есть, только если кейсу нужны события. Топики `<событие>.v<версия>`, DLQ `<топик>.dlq`, 3 повтора (1, 5, 25 с), потребитель идемпотентен по `eventId`, доставка at-least-once через transactional outbox. |
| `domain-model.json`, `api-design.json` | Источник истины, из которого построены контракты; генераторы могут читать их напрямую. |
| `api-methods/<operationId>.md` | Страница на метод по шаблону API: алгоритм, исключения, таблицы, примеры. |
| `traceability.csv` | Колонки `api` и `events` заполнены; `tests` дописывает QA по ID в имени или тегах тестов (`FR-5`, `AC-FR-5-02`). |

Пути API — `/api/v1/…`, kebab-case, только существительные; действие задаёт HTTP-метод.

## 4. Что документация берёт у вас

`tech_writer` читает эти пути (`packages/specforge-analysis/specforge_analysis/team_inputs.py`, `PATHS`):

| Путь в `artifacts/` | Что положить | Куда попадёт |
|---|---|---|
| `08_infra/README-run.md`, `docker-compose.yml`, `.env.example` | как есть | инструкция по запуску |
| `08_infra/services/<slug>.md` | паспорт сервиса по шаблону | заменяет сгенерированный паспорт |
| `08_infra/adr/*.md` | ADR по шаблону | добавляются в журнал решений |

Пока файлов нет, раздел помечается «Ожидается», шлюз даёт предупреждение, а не ошибку.

## 5. Порядок работы

- Работайте в своей ветке, вливайте через merge request; в `packages/specforge-analysis/` и
  `packages/specforge-web/` не правьте — замечания владельцам.
- Перед merge request:
  ```bash
  uv sync
  uv run pytest -q                       # вся платформа, включая tests/test_team_agents.py
  uv run ruff check . && uv run ruff format --check .
  uv run mypy specforge
  ```
- Проверка всей цепочки на эталоне без модели:
  `uv run pytest tests/test_team_agents.py -q` (граф `analyst → system_architect → … → tech_writer`).
- В `specforge/` и `agents/` нельзя слов предметной области кейса
  (`tests/domain_leak_words.txt`, проверка `tests/test_domain_leak.py`).

## 6. Открытые вопросы к участнику 3

1. Имя этапа backend: `04_backend` или другое?
2. Outbox и idempotency-таблицы — в вашем каркасе (как сейчас предполагает `schema.platform.sql`)?
3. Миграции EF Core строите по `schema.sql` или по `domain-model.json`?
4. Где лежат ваши паспорта сервисов и ADR — подходят ли пути из §4?
