# SpecForge — агентный конвейер разработки (платформа)

## Что это
SpecForge превращает бизнес-требования в работающее приложение
(Angular 20 + ASP.NET 10 + PostgreSQL 17/PostGIS + Kafka + Kotlin/Android + Docker + GitLab CI)
через последовательность LLM-агентов, каждый из которых закрыт шлюзом проверки.
LLM: Qwen3.8-27B через vLLM (OpenAI-совместимый API).

Этот репозиторий — **платформа**: ядро, исполнитель агентов, шлюзы, ремонт, оркестратор,
телеметрия. Агентов этапов пишут другие участники команды и подключают через реестр
(`agents/<name>/agent.py`). От публичных контрактов платформы зависят 4 человека.

## Принципы (не нарушать)
1. Оркестратор детерминирован. LLM работает только внутри агентов.
2. Этап = агент + шлюз. Этап пройден, только если шлюз зелёный (или статус `degraded` после исчерпания ремонтов).
3. В `specforge/` и `agents/` нет кода предметной области конкретного кейса
   (слова из `tests/domain_leak_words.txt` запрещены вне `examples/` и `tests/fixtures/`).
4. Все вызовы LLM — только через `specforge.llm.LLMClient` (учёт токенов, профили, ретраи, телеметрия).
5. Экономия токенов: детерминированные шаги не идут через LLM; правки через `str_replace`, а не переписывание
   файлов; логи в контекст попадают только усечёнными; неизменный префикс промптов (prefix caching).
6. Всё тестируется без GPU и без сети: `FakeLLM` со скриптованными ответами, `LocalSandbox`.

## Стек платформы
Python 3.12, uv, pydantic v2, langgraph + langgraph-checkpoint-postgres, openai (клиент к vLLM),
docker (docker-py), typer, fastapi + sse-starlette, python-gitlab, langfuse, psycopg 3 + pgvector,
structlog, matplotlib, pytest + pytest-asyncio, ruff, mypy.

## Структура
```
specforge/
  core/          contracts.py, agent_api.py, events.py, artifacts.py
  llm/           client.py, profiles.py, fake.py, tool_parsing.py
  sandbox/       base.py, docker_sandbox.py, local_sandbox.py
  tools/         fs.py, shell.py, docs.py
  executor/      react.py (agent_loop), structured.py, compaction.py
  gates/         runner.py, parsers/
  repair/        loop.py, context.py, fix_memory.py, prompts/
  rag/           ingest.py, store.py, lookup_agent.py
  orchestrator/  registry.py, graph.py, nodes.py, budget.py, publisher.py
  telemetry/     langfuse.py, metrics.py, report.py
  api/           server.py
  cli.py
agents/          агенты команды: agents/<name>/{agent.py, prompts/, schemas/}
config/          profiles.yaml и прочие настройки без кода
infra/           docker-compose.platform.yml, images/, smoke/bench скрипты
examples/        входные ТЗ (кейс №1 и синтетические)
tests/
docs/AGENT_SDK.md   — контракт для команды
```

## Команды
```
uv sync
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy specforge
docker compose -f infra/docker-compose.platform.yml up -d
uv run specforge agent run <name> --in <dir> --out <dir>
uv run specforge run examples/case1.md
uv run specforge report <run_id>
```

## Конвенции
- Типы везде; все данные между модулями — pydantic-модели, а не dict.
- Публичные контракты (`core/contracts.py`, `core/agent_api.py`) меняются только вместе с `docs/AGENT_SDK.md`
  и с пометкой BREAKING в коммите.
- У каждого модуля есть unit-тесты на FakeLLM / LocalSandbox. Задача не готова, пока pytest, ruff и mypy не зелёные.
- Логи — structlog (JSON). Полные логи команд — в `runs/<id>/logs/`, в контекст LLM — только усечённые.
- Конфигурация — через env (`.env.example` держать актуальным) и `config/*.yaml`.
- Маленькие коммиты после каждого зелёного шага: `feat(executor): ...`, `fix(gates): ...`.
