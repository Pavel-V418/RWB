# Changes — интеграция части участника 3 (backend + DevOps), 31.10

Подключено тем же путём, что части участников 2 и 4 (`docs/AGENT_SDK.md` §13): пакет в `packages/`
и тонкие адаптеры в `agents/`. Код участников 2 и 4 (`packages/specforge-analysis`, `packages/specforge-web`)
не менялся. Проверено: `uv run pytest -q` — 501 passed, 18 skipped (пропуски только в чужих частях:
нет tsc, PostgreSQL или LLM); `ruff check .`, `ruff format --check .`, `mypy specforge agents` — чисто;
`specforge doctor` видит `backend_coder` и `devops` без ошибок.

Повторная проверка перед отправкой: чистая копия папки без `.venv` → `uv sync` → `uv run pytest -q`:
501 passed, 18 skipped. Одно наблюдение не про интеграцию: `tests/test_efficiency.py` чувствителен к длине
пути к папке (путь попадает в ответы инструментов и уменьшает общий префикс промптов). При очень длинном
пути получилось 79% при пороге 80%, при обычном пути тест проходит. Живой запуск адаптеров на артефактах
кейса 3 с подменной моделью: `backend_coder` — генераторы, `dotnet build`, тег без кода → заглушка 501,
статус `degraded` (ожидаемо); `devops` — стенд поднят, health/swagger/metrics, `passed`, стенд погашен.

## Новое

| Путь | Что |
|---|---|
| `packages/specforge-backend/` | Моя часть целиком: каркас .NET 10 (`templates/backend-dotnet`), генераторы без LLM (`generators/`: DTO и контроллеры из OpenAPI, EF-модели из DDL, Kotlin-клиент, стенд, CI, паспорт сервиса и ADR, матрица трассируемости), backend-агент и DevOps-агент (`specforge_backend/backend`, `specforge_backend/devops`), эталоны кейсов 1 и 3 (`reference/`), офлайн-тесты (`tests/`, 8 шт.). Python-пакет переименован `agents.*` → `specforge_backend.*` (иначе конфликт с `agents/` платформы). |
| `agents/backend_coder/` | Адаптер, этап `04_backend`, `depends_on=["system_architect"]`: генераторы `gen-server.sh` + `gen-ef.sh`, затем backend-агент (реализация `I<Tag>Api` по тегам + интеграционные тесты). Шлюзы `dotnet build` / `dotnet test` — внутри части. |
| `agents/devops/` | Адаптер, этап `08_infra`, `depends_on=["backend_coder"]`: compose, db-init, deploy.sh, `.gitlab-ci.yml`, паспорт сервиса, ADR; шлюз `compose up --wait` + health/swagger/metrics, после шлюза стенд гасится. |
| `agents/_team/backend.py` | Мост: `BackendLLM` реализует протокол моих агентов (`chat(system, user) -> str`, `usage.total`) поверх `ctx.llm` (профиль CODE, thinking выключен). Собственный клиент части в конвейере не используется. |

## Изменено в общих файлах

| Файл | Изменение | Почему |
|---|---|---|
| `pyproject.toml` | `specforge-backend` в `dependencies`, `[tool.uv.sources]`, `testpaths`, mypy-override | как у пакетов участников 2 и 4 |
| `config/repo_layout.yaml` | `04_backend` публикуется в `04_backend/` (было `backend/`, помечено как предположение); `08_infra` — в `08_infra/`, в корень только `.gitlab-ci.yml` (было `infra/` + compose и `.env.example` в корень) | `.gitlab-ci.yml` и compose ссылаются на `04_backend/`, `08_infra/deploy.sh`, `../04_backend`, `./db-init`. Если compose уехал бы в корень, а backend — в `backend/`, сборка и деплой в GitLab сломались бы |
| `agents/tech_writer/agent.py` | `depends_on` += `backend_coder`, `devops`; вход += `04_backend/traceability.md` | документация собирается после backend и видит паспорт, ADR, README-run, матрицу (§2 п.4 передачи) |
| `uv.lock` | обновлён `uv sync` (добавился `specforge-backend`) | автоматически |
| `tests/test_team_agents.py` | в сквозной прогон добавлены `NoDotnetBackend`, `NoDockerDevOps` (по образцу `NoBuildFrontend`) | у `tech_writer` новые зависимости; в тестах нет dotnet и Docker |

## Изменено внутри моей части под платформу

- Генераторы берут Python из `PYTHON` (адаптер ставит интерпретатор платформы), по умолчанию `python3`, а не `.venv` моего репозитория.
- `SPECFORGE_PLATFORM=1` (ставит адаптер): `gen-infra.sh` кладёт `.gitlab-ci.yml` в `08_infra/` (публикатор поднимает его в корень), compose собирает web из `../web/admin` (там его публикует платформа).
- `gen-infra.sh` пишет `08_infra/README-run.md` и `08_infra/.env.example` — их читает техписатель (§4 передачи).
- CI `web:image` находит Dockerfile web-части в `web/admin`, `05_web/admin` или `05_web`.
- CI подключает файлы участника 5 (`tests.gitlab-ci.yml`, `security.gitlab-ci.yml`) и из `ci/`, и из `reports/tests/ci/`
  (туда платформа публикует этап `07_tests`). Как встраивать участника 5 — `docs/INTEGRATION-participant5.pdf`.

## Ответы на открытые вопросы (§6 передачи)

1. Имя этапа — `04_backend` (подтверждено, в репозитории тоже `04_backend/`).
2. Outbox и идемпотентность — в каркасе: `04_backend/db/000_platform.sql` (`outbox_messages`, `idempotency_records`). `schema.platform.sql` архитектора не применяется; его `outbox%`/`idempotency%` из `schema.sql` генератор EF тоже отбрасывает.
3. EF Core — DB-first: `dotnet ef dbcontext scaffold` по `schema.sql` (enum-типы → varchar + CHECK), без миграций.
4. Паспорт — `08_infra/services/backend-api.md`, ADR — `08_infra/adr/*.md`: пути из §4 подходят.

## Что нужно на хосте конвейера

`backend_coder` и `devops` вызывают `dotnet` (SDK 10) и `docker compose` напрямую на хосте, а не в песочнице платформы. Без них этап завершится `failed`/`degraded` с понятной диагностикой, конвейер пойдёт дальше. На vm5 .NET лежит в `~/.dotnet` (нужны `DOTNET_ROOT` и `PATH`).
