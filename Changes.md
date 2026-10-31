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

---

# Changes — интеграция части участника 5 (Android, тесты, безопасность), 31.10

Подключено по `docs/INTEGRATION-participant5.pdf` тем же путём, что части участников 2, 3 и 4: пакет в
`packages/` и тонкие адаптеры в `agents/`. Код участников 2, 3 и 4 не менялся, кроме одной строки CI
участника 3 (ниже).

## Новое

| Путь | Что |
|---|---|
| `packages/specforge-mobile/` | Часть участника 5 целиком: Android-агент, шлюз Gradle и генератор скриншот-тестов, контрактные тесты, e2e, генератор маршрута, security-контур, каркас Android (`android/`), эталон кейса 1 (`reference/`), образ сборки (`infra/android/`), документы участника (`docs/`). Python-пакет переименован `agents.android_agent` → `specforge_mobile.android_agent`, `tools.*` → `specforge_mobile.tools.*` (конфликт с `agents/` и `tools` платформы). Тесты — 42, без Gradle, Docker и стенда. |
| `packages/specforge-mobile/specforge_mobile/android_agent/toolchain.py` | Где собирать: образ `specforge/android:jdk21-api36` (без сети, лимиты, не root; пути `/work` в логе → пути хоста), локальный Gradle или нечем — тогда код генерируется без ремонтов вслепую и этап честно `degraded`. |
| `packages/specforge-mobile/specforge_mobile/qa.py` | Этап `07_tests` целиком: security-контур по дереву артефактов → стенд из `08_infra` (если есть Docker) → контракт из OpenAPI и e2e по сценарию → стенд гасится → файлы CI продукта. |
| `packages/specforge-mobile/ci/` | CI продукта под раскладку публикатора: `tests.gitlab-ci.yml`, `security.gitlab-ci.yml` (пути `reports/tests`, `analysis/requirements`, `analysis/system`, код — `PYTHONPATH=reports/tests`), `e2e/route_e2e.py`. Исходные файлы участника 5 — `docs/original-ci/`. |
| `agents/android_coder/` | Адаптер, этап `06_android`, `depends_on=["system_architect", "ux_designer"]`. Приложение — только если `profile.json → needs_mobile_app` (проверка при запуске, не `enabled_if`: иначе `tech_writer` был бы заблокирован выключенным этапом). Kotlin-клиент — генератором участника 3 (`gen-clients.sh`, `CLIENTS=kotlin`); не вышло — клиент каркаса и предупреждение. |
| `agents/qa/` | Адаптер, этап `07_tests`, `depends_on=["backend_coder", "devops"]`. |
| `agents/_team/mobile.py` | Мост: `MobileLLM.chat(messages, **opts) -> LLMReply` поверх `ctx.llm` (профиль CODE, без thinking) — для Android-агента и LLM-триажа находок. |

## Изменено в общих файлах

| Файл | Изменение | Почему |
|---|---|---|
| `pyproject.toml` | `specforge-mobile` в `dependencies`, `[tool.uv.sources]`, `testpaths`, mypy-override | как у остальных пакетов |
| `uv.lock` | добавлен workspace-пакет `specforge-mobile` (зависимость — только `pyyaml`, уже в lock); `uv lock --check` проходит | при `uv sync` пересоберётся сам, если что |
| `config/repo_layout.yaml` | `06_android` → `android/` с `exclude`: служебные файлы агента (`gate_report.json`, `agent_log.json`, `plan.json`, сырые ответы `llm/*`) и результаты Gradle (`project/build`, `project/app/build`, `.gradle`, `.kotlin`, `local.properties`) | в продуктовый репозиторий — исходники и снимки, не сотни МБ сборки |
| `agents/tech_writer/agent.py` | `depends_on` += `android_coder`, `qa` | документация собирается после них и видит `06_android/screenshots`, `07_tests/**` (§6 п.1 инструкции участника 3) |
| `tests/test_team_agents.py` | в сквозной прогон добавлены `NoGradleAndroid`, `NoStandQa`; тесты моста `MobileLLM`, пропуска `android_coder` без мобильного приложения, отказа без UX-спеки, `qa` без стенда и раскладки `07_tests` | по образцу `NoDotnetBackend` / `NoDockerDevOps` |
| `packages/specforge-backend/templates/ci/gitlab-ci.yml` | `stand:smoke` запускает `reports/tests/e2e/*.py` и `07_tests/e2e/*.py` | так требует инструкция участника 3 (§5): в опубликованном репозитории e2e лежит в `reports/tests/e2e` |
| `docs/AGENT_SDK.md` §13 | строки участников 3 и 5 в таблице частей, план графа, мосты, `tool` диагностик | таблица была только про участников 2 и 4 |

## Настройки хоста конвейера

`SPECFORGE_ANDROID_GATE` (`auto` / `docker` / `local` / `none`), `SPECFORGE_ANDROID_IMAGE`, `ANDROID_APP_ID`,
`SPECFORGE_QA_STAND` (`auto` / `on` / `off`), `STAND_URL`, `SPECFORGE_QA_USERS` (`роль=логин,…`, пароли —
`CONTRACT_PASSWORD_<РОЛЬ>`), `E2E_SCENARIO`. Подробно — `packages/specforge-mobile/README.md`. Образ сборки:
`docker build -f packages/specforge-mobile/infra/android/Dockerfile -t specforge/android:jdk21-api36 packages/specforge-mobile`
(на vm5 он уже есть). Для кейса 1: `SPECFORGE_QA_USERS=driver=driver.demo,admin=admin`,
`E2E_SCENARIO=packages/specforge-mobile/reference/case1/mock_route/scenario.json`.

## Что проверено и что нет

Проверено в среде без доступа к PyPI: тесты частей — `specforge-mobile` 42, `specforge-analysis` 67, `specforge-backend`
3 (ещё 5 требуют `dotnet` и падают так же на исходной версии); совместимость `security.json` с модулем участника 2;
скопированный в `07_tests` код запускается из раскладки опубликованного репозитория; `ruff check .` и
`ruff format --check` по `agents/` и `tests/` — чисто; `mypy --strict` по новым адаптерам — ошибок нет (13 ошибок —
только отсутствующие в этой среде зависимости платформы, как и у адаптеров участника 3); `uv lock --check`.

**Не проверено (нужен `uv sync`):** `uv run pytest -q` всей платформы, включая `tests/test_team_agents.py`,
`uv run mypy specforge agents`, `uv run specforge doctor`. Запустить на vm5 перед отправкой в `main`.
