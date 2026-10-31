# Детерминированные генераторы (без LLM)

Код, который однозначно выводится из контрактов, генерируется инструментами, а не LLM-агентом.
Одинаковый вход даёт байт-в-байт одинаковый выход (проверено `diff -r` двух прогонов).
Требования: Docker, .NET SDK 10, Python 3 + pyyaml (`.venv`), JDK ≥ 17, Node.js, Gradle.

## Всё сразу

```bash
generators/run-all.sh <artifacts_dir> [--verify]   # ~25 с на кейсе 1
```
Вход: `02_system/openapi.yaml` и `02_system/schema.sql`.
Выход: `04_backend` и `06_android/.../core/network/generated`. TS-клиент не генерируется:
у web-ветки свой генератор из того же OpenAPI (`CLIENTS=ts,kotlin` — включить обратно).

Шлюзы: `04_backend/gate.json` (генерация backend и `dotnet build`) и `06_android/gate.json` (Kotlin-клиент).
Формат для repair-агента: `{"gate", "passed", "errors": [{"file", "line", "message"}]}`, пути
относительно `artifacts`, не больше 50 ошибок. Разбор логов dotnet, kotlinc и Python — `gate_report.py`.
Код выхода ≠ 0 — шлюз не пройден, полный лог в stderr.

## Серверный контракт ASP.NET из OpenAPI

```bash
generators/gen-server.sh <openapi.yaml> <backend_dir> [--verify]
```
Если `<backend_dir>` пуст, туда копируется золотой каркас. Генератор — `server/gen_aspnet.py`, без шаблонизаторов, только pyyaml.

| Файл | Что внутри |
|---|---|
| `App.Application/Contracts/Generated/Models.cs` | DTO (`record ...Dto`) с валидацией из схемы, enum'ы с JSON-значениями из контракта |
| `App.Application/Contracts/Generated/Apis.cs` | `I<Tag>Api` — **их реализует backend-агент** |
| `App.Api/Controllers/Generated/<Tag>Controller.cs` | маршруты, роли, `[Idempotent]`, привязка параметров, коды ответов |

Что берётся из контракта:
- **Роли:** `x-roles` (соглашение команды) главнее `security`. `[admin]` даёт `[Authorize(Policy = "admin")]`, `[]` — `[AllowAnonymous]`, все роли — просто аутентификацию.
- **Идемпотентность:** `x-idempotent: true` или заголовок `Idempotency-Key` дают `[Idempotent]`; на GET ключ игнорируется (чтение и так безопасно).
- **Повторы параметров** (живой архитектор пишет `page` и inline, и через `$ref`): пара (name, in) уникальна, параметр операции перекрывает общий, из повторов берётся последний.
- **Ответы:** `200|204` превращается в `Task<T?>` (null означает 204), `201` — в `StatusCode(201, ...)`.
- **Ошибки** в сигнатуры не попадают: реализация бросает исключения каркаса, и они превращаются в RFC 9457.
- **Суффикс `Dto`.** Сущности EF называются по таблицам (`ParkingSession`), без суффикса они совпали бы с именами DTO.

## EF-модели из DDL

```bash
generators/gen-ef.sh <schema.sql> <backend_dir> [--verify]
```
Скрипт копирует DDL в `db/100_domain.sql`, поднимает временный PostGIS, применяет `db/*.sql` и запускает
`dotnet ef dbcontext scaffold` со своими шаблонами T4 из `ef/Scaffold/CodeTemplates`. Результат:
- `App.Domain/Entities/Generated` — partial-сущности без атрибутов EF;
- `AppDbContext.Generated.cs` — DbSet-ы и fluent-конфигурация в `OnModelCreatingGenerated`;
- `IAppDbContext.Generated.cs` — те же DbSet-ы для слоя Application.

Платформенные таблицы (`outbox*`, `idempotency*`) пропускаются: каркас реализует их сам.
Перед генерацией DDL нормализуется (`ef/normalize_ddl.py`): enum-типы PostgreSQL становятся `varchar` + `CHECK`,
иначе scaffold молча выбрасывает такие колонки. Если scaffold всё же пропустил колонку или таблицу, шлюз падает.
Литеральные `DEFAULT` переносятся в инициализаторы свойств (`ef/postprocess.py`), иначе EF не сохранит `false` или `0`.
`timestamptz` превращается в `DateTimeOffset`, как во всём каркасе.
Согласованность модели и схемы проверяет интеграционный тест `PersistenceModelTests`: каждая сущность читается реальным запросом.

## Клиенты API из OpenAPI

```bash
generators/gen-clients.sh <openapi.yaml> <artifacts_dir> [--verify]
```

| Выход | Для кого | Стек |
|---|---|---|
| `<artifacts>/05_web/api-client` | участник 4, web-админка | `typescript-angular`, Angular 20, standalone, `provideApi(...)` |
| `<artifacts>/06_android/app/src/main/kotlin/<appId>/core/network/generated` | участник 5, Android | Kotlin 2.2.21, Retrofit 3 + coroutines, kotlinx.serialization; пакет `<appId>.core.network.generated` |

`ANDROID_APP_ID` (по умолчанию `com.specforge.case1`) и `ANDROID_SRC_ROOT` (по умолчанию `app/src/main/kotlin`) задают пакет и путь.
В модуль Android копируются только исходники и перезаписывается только папка `generated/`; Kotlin компилируется во временном JVM-проекте.

`--verify` — шлюз качества: `ng-packagr` собирает TS-библиотеку, `gradle compileKotlin` компилирует Kotlin.
Любая ошибка → код выхода ≠ 0.

Генератор: openapi-generator, версия в `openapi-generator.version`.
JAR скачивается с Maven Central в `generators/.tools/`, контрольная сумма проверяется.

### Требования к окружению
- JDK ≥ 17 (`java` или `JAVA_HOME`; на macOS `/usr/bin/java` — заглушка, скрипт её пропускает).
- Node.js + npm — для `--verify` TS.
- Gradle, запускаемый на JDK 21 (`GRADLE_JAVA_HOME`, по умолчанию `/opt/homebrew/opt/openjdk@21`) — для `--verify` Kotlin.

### Решения, которые не видны из кода
- **`number` → `Double`, свободный объект → `JsonObject`, `{}` → `JsonElement`.** Иначе kotlinx.serialization не
  компилируется (`BigDecimal`) или падает с внутренней ошибкой компилятора (`Map<String, Any>`).
  Оба случая нашлись на живом выходе агента-архитектора, на эталоне их не было.
- **GeoJSON в Kotlin — `JsonElement`.** У `coordinates` переменная вложенность. Из-за неё генератор выдаёт `Any`,
  который kotlinx.serialization не разбирает во время выполнения. MapLibre Android принимает геометрию строкой:
  `GeoJsonSource(id, geometry.toString())`.
- **Сборочные файлы Kotlin свои** (`clients/kotlin-build.gradle.kts`): генератор ставит Kotlin 2.4,
  а стек кейса — 2.2.21. Тесты-заглушки генератора (kotlintest) удаляются.
- **TS: `useSingleRequestParameter`** — у каждого метода один объект параметров,
  поэтому добавление необязательного параметра в контракт не ломает вызовы.
- **`Idempotency-Key` — обязательный параметр методов**, клиент обязан его передать (UUID на операцию).

## CI продукта и MR на каждый этап

`gen-infra.sh` кладёт в корень артефактов `.gitlab-ci.yml` (шаблон `templates/ci/gitlab-ci.yml`) и `08_infra/deploy.sh`.
Репозиторий артефактов самодостаточен: моки (`08_infra/mocks`), init-скрипты БД (`08_infra/db-init`), e2e (`07_tests/e2e`).

| Стадия | Задача | Что делает |
|---|---|---|
| build | `backend:build` | `dotnet build -warnaserror` (из `04_backend`: там `global.json`) |
| test | `backend:test` | unit + интеграционные (Testcontainers), отчёт JUnit → вкладка Tests в MR |
| package | `backend:image` | образ `specforge/backend:<sha>` |
| security | из `ci/security.gitlab-ci.yml` | задачи QA (участник 5), подключаются, если файл есть |
| deploy | `stand:deploy` | только основная ветка: `08_infra/deploy.sh` → `docker compose up --wait` на VM |
| e2e | `stand:smoke` | `/health/ready`, Swagger, все `07_tests/e2e/*.py --base $STAND_URL` |

Раннер — shell на vm5 (`scripts/vm-runner.sh install|register|start`, бинарник из образа `gitlab/gitlab-runner`, без sudo).
Compose-проект у каждого кейса свой — `specforge-<каталог над artifacts>` (или `STAND_NAME`): иначе стенд нового кейса
на той же машине получил бы том БД прежнего (старая схема — `db-init` применяется только к пустому тому — и чужой пароль).
На VM живёт один стенд: `deploy.sh` и DevOps-агент перед подъёмом останавливают другие проекты `specforge*` (без `-v`).
Секреты стенда — `~/.specforge-stand.env` на VM (или переменные CI `STAND_POSTGRES_PASSWORD`, `STAND_JWT_SIGNING_KEY`).
Проверено локально: `gitlab-ci-local` (на macOS нужен bash ≥ 4: `PATH=/opt/homebrew/bin:$PATH`) — 5/5 задач.

MR на этап: `generators/stage-mr.sh <artifacts_repo> <stage_dir> "<заголовок>"` — коммит этапа собирается во временном
индексе поверх `origin/main` (рабочая копия не меняется) и уходит веткой `stage/<stage_dir>` с push-опциями GitLab
`merge_request.create`; в описании — результат шлюза из `<stage_dir>/gate.json`.

## Паспорт сервиса и ADR

`gen-infra.sh` в конце вызывает `docs/gen_docs.py <artifacts>`: `08_infra/services/backend-api.md` — описание сервиса по шаблону
организаторов (ресурсы, окружения, сборка, деплой, переменные, мониторинг `/metrics` и `/health`, логирование), собранное
из `openapi.yaml`, compose и `.gitlab-ci.yml`; `08_infra/adr/*.md` — ADR платформы из `templates/docs/adr`. Без LLM, детерминированно.

## Матрица трассируемости

`docs/traceability.py <artifacts>` (вызывается backend-агентом в конце прогона): требование → операции API → файл реализации
(заглушка 501 помечается) → интеграционные тесты. FR операции — `x-requirements` из OpenAPI (иначе страница `api-methods`),
тест → операция по имени `Operation_Condition_Result`, точнее — по `[Trait("AC", ...)]`. Выход: `04_backend/traceability.md`
и столбец `tests` в `02_system/traceability.csv` участника 2 (остальные столбцы не меняются).
