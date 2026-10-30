# Статус платформы SpecForge

Срез на коммит `6a7902b` (исправления по ревью 4.1–4.4). pytest 200 passed / 2 skipped, ruff, mypy — зелёные.

## Сделано (4.1–4.4)
- **4.1 Профили.** `AgentSpec.profile: ProfileName = CODE`, `thinking`/`temperature` — Optional (None = из профиля).
  `Profile` + `presence_penalty`, `timeout_s` (= `max_tokens / LLM_MIN_TOKENS_PER_S + 60`); ANALYSIS 16384, REVIEW/CODE 8192,
  REPAIR 4096 токенов; REVIEW t=0.6. ToolAgent/StructuredAgent/agent_loop/run_structured/ремонт передают `spec.profile`
  и перекрывают только не-None поля. thinking + t<0.5 → 0.6 с warning. `config/profiles.yaml` обновлён,
  `LLM_PROFILES_PATH` — от корня проекта.
- **4.2 События.** `PipelineState.events` удалён; добавлен `EventBus` (`core/events.py`), CLI подписывает на него логи.
- **4.3 Новые поля.** `GateResult.failed_command_index` (заполняется шлюзом), `GateResult.tests_executed`,
  `StageResult.stopped_reason` (заполняет ToolAgent).
- **4.4 Невалидные tool call.** В истории `arguments="{}"`; сырой текст — в лог `llm.tool_call.invalid` и событие `tool_called`.
- `docs/AGENT_SDK.md`, раздел «10. Изменения»: таблица «было/стало» и миграция для агентов команды.

## Оркестратор (LangGraph)
- `orchestrator/registry.py` — `Registry`: автозагрузка `agents/*/agent.py` (кроме `_*`), проверка depends_on,
  циклов, пересечения outputs, групп; детерминированный план шагов; `specforge list-agents`.
- `orchestrator/graph.py` — `build_graph`: profile → шаги (fan-out/fan-in по `parallel_group`) → finalize;
  PostgresSaver (`SPECFORGE_PG_DSN`), `specforge run`, `specforge resume <run_id> [--from <этап>]`.
- `orchestrator/nodes.py` — узлы: зависимости (degraded с артефактами — дальше, иначе skipped), enabled_if,
  бюджет, кэш, `execute_stage(max_repairs=)`; `pipeline.json`.
- `orchestrator/budget.py` — `BudgetController`: >70% — thinking off (`LLMClient.with_thinking`), >85% —
  max_repairs=1, 100% — skipped; события `budget_warning`.
- `orchestrator/cache.py` — кэш этапов в `runs/.cache/` по хэшу входов + spec + промптов + исходника агента.
- `core/artifacts.py` — `RunWorkspace`, `artifacts/<stage>/manifest.json` с sha256.

## CLI, события, API консоли
- `core/events.py` — `EventType`, поле `stage`; `EventBus` пишет в `PostgresEventStore` (таблица events),
  `SqliteEventStore` (`runs/events.sqlite` без Postgres) или память; ошибка хранилища его отключает.
- Подтверждение: узел `approve_<i>` после каждого шага, interrupt при `--approve-after`; `approve_run` с
  правками артефактов (пути проверяются до продолжения); `--only` — этапы + зависимости.
- `orchestrator/service.py` — `RunService` (фоновые запуски, статус, граф для консоли), общий для CLI и API.
- `cli.py` — run (md/txt/pdf/docx, `--autopilot | --approve-after`, `--only`, `--budget`, дерево rich), resume
  (подтверждает остановку), status, report; `telemetry/report.py` — отчёт в Markdown.
- `api/server.py` — FastAPI, SSE (`?after`, `Last-Event-ID`, `?follow=false`), CORS; контракт
  `docs/console-api.yaml`.

## Телеметрия и публикация
- `telemetry/` — метрики, `specforge report` (report.md, report.json, 2 PNG), Langfuse-хук (no-op без ключей).
- `orchestrator/publisher.py` — подписчик EventBus: ветка run/<id>, коммит на этап по `config/repo_layout.yaml`,
  отчёт + verdict.json, один MR в main. Без GITLAB_* или при ошибке GitLab — `publish_warning` и пропуск прогона.
  Вживую с GitLab не проверялся (тесты на моке python-gitlab); этапы без записи в раскладке не публикуются.

## Проверка стенда
- `specforge doctor` (`specforge/doctor.py`): vLLM (модель, tool calling, json_schema, thinking on/off), Postgres
  (+ pg_trgm, vector), Langfuse, GitLab, Docker; агенты (spec, prompt_dir, sandbox_image запускается, программы
  gate_commands в образе); граф — недостижимые этапы для профилей «всё включено / без mobile / без geo».
  Не настроенные Postgres / Langfuse / GitLab — SKIP. На машине разработки вживую: vLLM, Langfuse, Docker — FAIL
  (не запущены), реестр пуст.
- `scripts/e2e_case1.sh`: doctor → run examples/case1.md --autopilot → report. `examples/case1.md` ещё нет в репозитории.

## Осталось
- `GateResult.tests_executed` всегда `None`: парсеры TRX/JUnit пока не считают выполненные тесты.
- `StageResult.stopped_reason` не заполняется у StructuredAgent и ремонта (эскалация пишет причину только в summary).
- Не начаты: `rag/`.

## Не проверено вживую (только тесты на подделках)
- PostgresSaver: resume проверен только на InMemorySaver (тот же интерфейс); с реальным Postgres не запускался.
- Лимит времени этапа не прерывает этап — превышение фиксируется событием после него.
- `PostgresEventStore` и approve через CLI между процессами — только с Postgres, вживую не запускались.
- SSE опрашивает хранилище каждые 0.2 с (без LISTEN/NOTIFY).
- DockerSandbox — поддельный docker-клиент; Docker на машине разработки нет.
- `infra/docker-compose.platform.yml` — проверен разбором YAML, `docker compose up` не запускался.
- Память исправлений в Postgres (pg_trgm, SQL) — поддельное соединение.
- vLLM/Qwen3.8: smoke (`infra/smoke_llm.py`) не запускался; парсеры `hermes`/`qwen3`, top_p/top_k, длину контекста
  131072 нужно сверить с model card.
- Тест симлинков за пределы /work пропускается на Windows без прав администратора.

## Незакрытые проблемы
- Все запросы теперь несут `max_tokens` (CODE — 8192) — поведение старых агентов без явных настроек изменилось.
- `VLLM_IMAGE_TAG=latest` — нужно закрепить версию.
- `LocalSandbox` не изолирует команды: только для разработки и тестов.
- В `.env` пользователя нет `SPECFORGE_PG_DSN` — память исправлений у него выключена.
- `main.py` (шаблон PyCharm) в корне — исключён из ruff, в коммиты не попадает.

## Неочевидные решения
- `ProfileName` перенесён в `core.contracts`: иначе `AgentSpec` → `llm.profiles` → `contracts` — цикл импорта.
- Правило thinking/температуры проверяется ещё и в каждом вызове LLM: валидатор AgentSpec не видит thinking профиля.
- Переопределение `max_tokens` в YAML без `timeout_s` пересчитывает таймаут, а не берёт старый.
- Ремонт: служебный git в `/work/.specforge/git` с отключёнными подписью и хуками пользователя; метка `specforge-green`
  ставится после каждого зелёного шлюза, поэтому `degraded` откатывает всю работу неудачного этапа.
- Запреты ремонта (глушение ошибок, отключение тестов, защищённые файлы) проверяются по diff кодом, не только промптом.
- Postgres-память отключается после первой ошибки соединения, чтобы ремонт не ждал таймаут на каждой ошибке.
- `.env` ищется от текущего каталога (`find_dotenv(usecwd=True)`), иначе тесты подхватывали `.env` репозитория.
- Шлюз останавливается на первой упавшей команде; песочница этапа общая для агента, шлюза и ремонта.
