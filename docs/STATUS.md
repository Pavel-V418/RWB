# Статус платформы SpecForge

Срез на `0b80436`. pytest 286 passed / 2 skipped (симлинки на Windows, LLM_BASE_URL), ruff, mypy —
зелёные. `main` запушен в `origin`; этап 13 — 292 passed.

## Сделано по этапам

| Этап | Коммиты | Кратко |
|---|---|---|
| 0 каркас | `b377151` | Контракты (`core/contracts.py`, `core/agent_api.py`), реестр `agents/<name>`, `specforge agent run`, AGENT_SDK.md |
| 1 LLM | `d575415` | LLMClient: профили, ретраи, учёт токенов, FakeLLM; compose vLLM/Postgres/Langfuse |
| 2 исполнитель | `d7bed94` | agent_loop (ReAct), инструменты, Local/DockerSandbox, компакция |
| 3 шлюзы | `8ccb140` | GateRunner, парсеры (tsc, eslint, MSBuild, TRX, JUnit, psql, Spectral, AsyncAPI, generic) |
| 4 ремонт | `b7e4018`, `6a7902b`, `304a123` | repair_loop, git-снимки, память исправлений; правки ревью 4.1–4.4 (BREAKING профили/события) |
| 5 оркестратор | `06bb7b0`, `56da211` | LangGraph: реестр, шаги/параллельные группы, бюджет, кэш этапов, PostgresSaver, resume, manifest |
| — | `d550069`, `a65769a` | Скоростной режим в CLAUDE.md, pytest-timeout |
| 6 CLI/API | `bf605f1`, `fb3137b` | EventType + хранилища событий; CLI run/resume/status (rich), pdf/docx, --only, approve (interrupt); FastAPI + SSE, `docs/console-api.yaml` |
| 7 телеметрия | `02e216a`, `1122311` | Метрики, `specforge report` (md, json, 2 PNG), Langfuse-хук |
| 8 публикация | `8d8342e` | GitLab publisher: ветка run/<id>, коммит на этап, отчёт + verdict, один MR |
| 9 doctor | `eccabb3` | `specforge doctor`, `scripts/e2e_case1.sh` |
| 10 RAG | `0fca4cd`, `6b7c150` | FTS5 + векторы (remote / fastembed / FTS-only), RRF, `search_docs`, `ask_docs`, `infra/fetch_docs.sh` |
| 11 эффективность | `b524278` | Стабильные префиксы, thinking по агентам (profiles.yaml), dedup_hits, селекторы входов, bench_concurrency, «до/после» |
| 12 приёмка | `3fbd867`, `0b80436` | agents/acceptance (verdict.json), таймаут этапа, инфра-повторы, ожидание LLM; словарь доменной утечки |
| 13 репетиция | — | `scripts/rehearse.py`: все ТЗ из examples/ → rehearsal.md (этап × ТЗ, топ-10 ошибок шлюзов с исправлениями по `config/rehearse_rules.yaml`); diagnostics в событиях gate_failed/repair_attempt |
| — публикация | — | `repo_layout.yaml`: корень (`.`) и файлы в корень (`root`: .gitlab-ci.yml, docker-compose, README), этапы ux/db/mocks/tests/e2e/security/devops/ci/docs/acceptance; вердикт приёмщика в docs/report/ и MR; `doctor` — этапы без раскладки; парсер `diagnostics-json` |
| — приёмка по ТЗ | — | `doc_templates.yaml` по ТЗ (14 разделов СА, 11 документов, ID ФТ); проверки CI, секретов, README/.env.example/миграций/тестовых данных, статического анализа; `test-report.md`, `security-report.md`; `GateResult.tests_executed` из TRX/JUnit |
| — вопросы, seed, логи | — | Протокол уточняющих вопросов (`questions.json` → `--ask` / допущения → `input/clarifications.*`), ручное вмешательство в отчёте; `LLM_SEED`; логи шлюзов в `docs/report/logs/` |
| — образы песочниц | — | `infra/images/{dotnet,node,postgis,devops,android}` + `build.sh`, `pg-up`; кэш Trivy; не собирались (Docker на машине разработки не запущен) |
| — кейс 1 и данные | — | `examples/case1/` (ТЗ + `data/spb.json`), `run --data` / API zip → `artifacts/input/data/`; e2e и репетиция берут `data/` |
| — части команды | — | `packages/specforge-analysis` (уч. 2) и `packages/specforge-web` (уч. 4) в uv-workspace; агенты-адаптеры `analyst`, `system_architect`, `ux_designer`, `frontend_coder`, `tech_writer`; вызовы модели — через `LLMClient`; раскладка номерных этапов; шаблоны документов и матрица трассируемости — под регламент участника 2 |

## Начато и не доделано

Незакоммиченных правок нет. Не сделано из промптов:
- **Baseline (этап 11) и `bench/concurrency.md`** — не сняты: нет vLLM, Docker и агентов команды.
- **Репетиция (этап 13) вживую не запускалась** — `examples/` пуст; проверена на синтетических прогонах.
  Ранние прогоны без поля `diagnostics` в событиях дают в топ только итоговые красные шлюзы.
- Кейс 1 в репозитории: `examples/case1/tz.md` (текст Приложения 1), `examples/case1/data/spb.json` и описание
  данных; `run --data`, `e2e_case1.sh` и `rehearse.py` передают `data/`. Живой прогон не выполнялся.
  Зоны в данных — линии (LINESTRING), не полигоны: неоднозначность для аналитика.
- Из STATUS 4.x: `StageResult.stopped_reason` не заполняют StructuredAgent и ремонт.

## Отклонения от промптов и TODO

- **Интеграция частей команды (уч. 2, 4).** Сделано: сквозной граф на FakeLLM (эталоны анализа) — все 5 этапов
  `passed`. Исправлено в частях: шаблон ID требований в схеме экранов веба (`FR-\d{2,3}` → `FR-\d{1,4}`,
  анализ нумерует `FR-1` по регламенту), путь в навигации MkDocs и кодировка в тесте анализа (ломались на Windows),
  убраны `sys.path.insert` веба, импорты веба — `specforge_web.*`. Не сделано:
  - ответы на вопросы (`--ask`) не возвращаются в анализ — `analyst` берёт допущения; нужен перезапуск с
    `answers.yaml` (формат части) или чтение `input/clarifications.json` частью;
  - профиль кейса (`CaseProfile`) не берётся из `01_requirements/profile.json` — флаги CLI;
  - UI-ревьюер веба (`vlm_reviewer`, Playwright + VLM) и консоль участника 4 не подключены; API консоли
    участника 4 (`/console-api/v1`, свои форматы) расходится с `docs/console-api.yaml` — согласовать;
  - имя этапа backend `04_backend` в `repo_layout.yaml` — предположение;
  - `tech_writer` зависит от `ux_designer` и `frontend_coder`; когда появятся агенты backend / тестов / DevOps —
    добавить их в `depends_on`, иначе документация соберётся раньше их результатов.

- Этапы 7, 10, 11 просили «в отдельном worktree» — его не было, работа шла в основном checkout на `main`;
  запрет на правку `cli.py`/`api/server.py`/`graph.py` соблюдён, кроме случаев, когда пользователь разрешил явно.
- Этап 12, таймаут: Python-поток не убить — зависший агент дорабатывает в брошенном потоке; agent_loop и ремонт
  останавливаются по `deadline` между шагами.
- Этап 11, дедупликация: в ключ кэша добавлен `thinking` (ответы разные). Профили по умолчанию с temperature > 0 —
  кэш срабатывает только у агентов с temperature=0.
- Этап 11, селекторы: платформа агентов не сужает сама — только событие `input_too_large`; селекторы ставит команда.
- Этап 11, тест префикса: совпадение байт-в-байт — только первого запроса; дальше расходятся id вызовов инструментов
  (ответ модели).
- Этап 5: пересечение `outputs` проверяется приближённо (совпадение, вложенность, префикс с `**`).
- Этап 8: этапы из кэша публикуются (`stage_cached`), этапы без записи в `repo_layout.yaml` — нет (`publish_warning`,
  `doctor` — FAIL). Имена этапов в `repo_layout.yaml` (ux, db, tests, e2e, security, devops, docs…) — предложение, сверить с командой.
- Шаблоны `config/doc_templates.yaml` — по тексту ТЗ (не шаблоны организаторов); пути документов — соглашение, сверить с тех. писателем;
  frontend-сервисы в healthcheck отсекаются по имени
  (web|front|ui|nginx|console|admin).
- Этап 6: поведение по умолчанию — автопилот (без `--approve-after` остановок нет); `--only` добавляет зависимости.

## Договорённости сессии (нет в CLAUDE.md / AGENT_SDK.md)

- Формат ошибок шлюза — существующий `Diagnostic` (`column`, не `col`) + `code`, `category`, `code_window`.
- KVM на машине разработки нет; скриншоты Android — Roborazzi, эмулятор — опция (WSL2 nestedVirtualization при нужде).
- Имена агентов в `config/profiles.yaml` (`analyst`, `backend_coder`, …) — предположение, сверить с командой;
  несовпадения покажет `specforge doctor`.
- Приёмщик ждёт от команды: `e2e/run.sh`, `docs/traceability.md` (FR | Код | Тест), compose в корне/`infra/`/`deploy/`,
  `reports/trivy*.json`, у backend — `/health` (Healthy/Degraded/Unhealthy) и `/metrics` (Prometheus).
- Данные кейса и ТЗ — входы прогона (`artifacts/input/`), не RAG; RAG — документация стека и `org-standards`.
- `.gitignore`: игнорируется только корневой `/runs/` (фикстуры `tests/fixtures/runs/` в git), `/data/`, `/infra/docs-corpus/`.
- Пользовательские события (кроме EventType): `stage_cached`, `stage_retry`, `stage_timeout`, `llm_unavailable`,
  `llm_available`, `artifacts_edited`, `input_too_large`, `input_selector_failed`, `publish_warning`, `published`,
  `profile_built`, `profile_fallback`, `escalation`, `budget_warning`.
- Изменения публичных контрактов после 4.x — только необязательные поля (Usage.dedup_hits, AgentSpec.timeout_s,
  GateResult.infra_error, StopReason `timeout`, AgentContext.deadline); `docs/console-api.yaml` перегенерирован.

## Известные проблемы

- Падающих тестов нет. 2 предупреждения pytest — штатное повышение температуры при thinking (CODE t=0.2).
- Вживую не проверено (только подделки): vLLM/Qwen, Docker/DockerSandbox, Postgres (чекпойнты, события, память
  исправлений), Langfuse, GitLab, docker compose/Trivy в приёмщике, fastembed с реальными весами,
  `infra/fetch_docs.sh` (пути документации в репозиториях — по памяти), uvicorn.
- `doctor` на машине разработки: vLLM, Langfuse, Docker — FAIL; в `.env` ключи Langfuse-заглушки.
- SSE опрашивает хранилище каждые 0.2 с (без LISTEN/NOTIFY). `LocalSandbox` не изолирует команды.
- `VLLM_IMAGE_TAG=latest` — закрепить версию.

## Следующий шаг

1. На VM: `infra/images/build.sh`, `specforge doctor` → `infra/fetch_docs.sh` → `uv run python infra/bench_concurrency.py` →
   `E2E_SAVE_BASELINE=1 scripts/e2e_case1.sh` → `uv run python scripts/rehearse.py`.
2. Сверить имена агентов с командой (`config/profiles.yaml`), шаблоны документации организаторов
   (`config/doc_templates.yaml`, `infra/docs-corpus/org-standards/`).
