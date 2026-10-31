# Паспорт сервиса: Parking Assistant API (backend)

> Сгенерирован `generators/docs/gen_docs.py` из артефактов продукта (OpenAPI, docker-compose, .gitlab-ci.yml). Не редактировать вручную — изменения вносятся в источники.

## Описание
Backend мобильного помощника водителя и административного web-интерфейса.

Соглашения:
- Версия API в пути: `/api/v1`.
- JSON в camelCase, время в ISO 8601 (UTC), деньги в копейках (integer).
- Координаты WGS84; в GeoJSON порядок `[lon, lat]`.
- Ошибки в формате RFC 9457 (`application/problem+json`).
- Аутентификация: JWT Bearer, роли `driver` и `admin`. Допустимые роли операции — `x-roles`
  (дублируется стандартным `security: [{bearerAuth: [admin]}]`); `x-roles: []` — анонимный доступ.
- `x-idempotent: true` — операция требует заголовок `Idempotency-Key`.
- Критичные операции принимают заголовок `Idempotency-Key`.
- Ограничение частоты запросов: при превышении ответ `429` и заголовок `Retry-After`.
- Транспорт: HTTP/1.1, снаружи только HTTPS (TLS на reverse proxy). Обмен синхронный request-response;
  изменения состояния дополнительно публикуются асинхронно: Kafka (между сервисами) и SignalR `/hubs/events` (в UI).
- Нахождение в зоне: расстояние от точки до геометрии зоны (линии вдоль улицы) не больше `detectionRadiusM`.

| Название | Описание |
|---|---|
| Тип приложения | ASP.NET Core REST API + SignalR (`/hubs/events`) + фоновые задачи |
| Прикладное ПО / Frameworks | .NET 10 (C# 14), ASP.NET Core, EF Core 10 + Npgsql + NetTopologySuite, Confluent.Kafka, prometheus-net |
| Системное ПО | Linux (контейнер `mcr.microsoft.com/dotnet/aspnet:10.0`), PostgreSQL 17 + PostGIS 3.5, Apache Kafka 4.1 (KRaft) |
| Swagger | `/swagger` (UI), `/swagger/v1/swagger.json` при `OpenApi__Enabled=true`; контракт — `02_system/openapi.yaml` |
| Архитектура | сервисная: один backend (Clean Architecture: Domain / Application / Infrastructure / Api), transactional outbox → Kafka → SignalR |
| Операции API | 20 операций в 7 группах: Auth, Events, Notifications, Sessions, Tracking, Vehicles, Zones |

## Ссылки
|  | Production | Stage | Test |
|---|---|---|---|
| Сервис | отсутствует | стенд на командной VM: `http://<vm>:8080` (`STAND_URL`) | локально: `http://localhost:8080` |
| Swagger | — | `http://<vm>:8080/swagger` | `http://localhost:8080/swagger` |
| Логи | — | `docker compose logs backend` в `08_infra` на VM | то же локально |

## Требования к ресурсам
Сервис и зависимости запускаются в контейнерах, лимиты памяти заданы в compose (переопределяются `*_MEM` в `.env`):

| Сервис | Образ / сборка | Лимит памяти |
|---|---|---|
| postgres | imresamu/postgis:17-3.5 | 384m |
| kafka | apache/kafka:4.1.0 | 512m |
| backend | specforge/backend:local | 384m |
| parkomatika-mock | specforge/parkomatika-mock:local | 64m |

Итого лимит стенда ≈ 1344 МБ; фактическое потребление на кейсе 1 — около 0,5 ГБ. Целевая VM: 4 CPU, 4 ГБ RAM; диск — ~3 ГБ под образы и том БД.

## Отказоустойчивость
Стенд хакатона: один экземпляр каждого сервиса, рабочее время, без резервирования. Контейнеры перезапускаются политикой `restart: unless-stopped` (в том числе после `POST /api/v1/system/restarts`). Backend без состояния в памяти: горизонтальное масштабирование возможно, если вынести фоновые задачи на один экземпляр (лидер) — не реализовано.

## Предельные ограничения работы сервиса
- Ограничение частоты запросов: 600 запросов в минуту на пользователя (анонимные — на IP), `RateLimiting:PermitPerMinute`; сверх — 429.
- Нагрузочное тестирование не проводилось; функциональная проверка — e2e-сценарий стенда (`07_tests/e2e`).

## Работа сервиса
Принимает REST-запросы (JSON, ошибки — RFC 9457 `application/problem+json` с полем `code`), пишет в PostgreSQL, публикует доменные события через transactional outbox в Kafka и ретранслирует их клиентам по SignalR. Изменяющие операции поддерживают `Idempotency-Key`. Фоновые задачи (таймеры предметной области) работают на `TimeProvider`.

Топики Kafka: `sessions`, `tracking` (+ DLQ); ключ партиции — идентификатор агрегата.

Внешние провайдеры (адаптер с повторами, таймаутом и circuit breaker — Microsoft.Extensions.Http.Resilience):

| Провайдер | Адрес на стенде |
|---|---|
| Parkomatika | `http://parkomatika-mock:8090/` |

## Авторизация
JWT (HS256): `POST /api/v1/auth/tokens` выдаёт токен, далее заголовок `Authorization: Bearer <token>`; для SignalR — `access_token` в строке запроса. Роли — claim `role`, проверяются на каждой операции по `x-roles` контракта.

Роли: `admin`, `driver`.
Без токена доступны: `POST /api/v1/auth/tokens`, а также `/health*`, `/metrics`, `/swagger`.

## Описание окружений
### Prod окружение
Отсутствует.
### Stage окружение
Стенд на командной VM, разворачивается задачей `stand:deploy` из основной ветки:

| Направление | Куда | Протокол / порт |
|---|---|---|
| К сервису | клиенты (web-админка, Android), e2e | HTTP 8080 |
| От сервиса | PostgreSQL (`postgres`) | TCP 5432 (внутри сети compose) |
| От сервиса | Kafka (`kafka`) | TCP 9092 (внутри сети compose) |
| От сервиса | провайдер Parkomatika | HTTP |

### Test окружение
Локальный запуск того же compose (`docker compose up -d --build --wait` в `08_infra`) и Testcontainers в интеграционных тестах.

## Сборка
Многостадийный `04_backend/Dockerfile`: restore (кэшируемый слой) → `dotnet publish -c Release` → образ `aspnet:10.0`, пользователь без root, порт 8080. Версия приложения — аргумент `APP_VERSION` (в CI — короткий SHA коммита), видна в `GET /api/v1/system/info`.

Стадии `.gitlab-ci.yml`:

| Стадия | Задачи |
|---|---|
| build | `backend:build` |
| test | `backend:test` |
| package | `backend:image`, `web:image` |
| security | из `ci/*.gitlab-ci.yml` (QA) |
| deploy | `stand:deploy` |
| e2e | `stand:smoke` |

## Деплой
Shell-runner на VM (тег `vm5`): образ собирается в локальный Docker VM (registry не нужен) → сканирование образа (Trivy, задачи QA в стадии security) → `08_infra/deploy.sh`: секреты из `~/.specforge-stand.env` на VM, `docker compose up -d --build --wait` с healthcheck каждого сервиса → e2e против стенда. Деплой только из основной ветки, параллельные деплои исключены `resource_group`.

## Конфигурация
`appsettings.json` + переменные окружения (приоритет у переменных; вложенность через `__`). На стенде всё задаётся в compose и `.env` (создаётся `deploy.sh`, права 600, в git не попадает).

| Переменная | Значение на стенде | Описание |
|---|---|---|
| `ASPNETCORE_ENVIRONMENT` | `Production` | окружение ASP.NET (Production на стенде) |
| `ConnectionStrings__Database` | секрет | строка соединения с PostgreSQL (пароль — из секрета POSTGRES_PASSWORD) |
| `Kafka__Enabled` | `true` | публикация событий в Kafka |
| `Kafka__BootstrapServers` | `kafka:9092` | адрес брокера Kafka |
| `Kafka__TopicPrefix` | пусто | префикс топиков (разделение окружений на одном брокере) |
| `Jwt__SigningKey` | секрет | ключ подписи JWT (секрет JWT_SIGNING_KEY, ≥ 32 байт) |
| `OpenApi__Enabled` | `true` | Swagger UI и /swagger/v1/swagger.json (contract_diff) |
| `TIME_SCALE` | `1` | ускорение времени для демо таймеров (1 — реальное время) |
| `Cors__AllowedOrigins__0` | `http://localhost:4200` | разрешённый origin web-админки |
| `DOTNET_gcServer` | `0` | 0 — workstation GC, меньше памяти для одного экземпляра |
| `Providers__Parkomatika__BaseUrl` | `http://parkomatika-mock:8090/` | адрес внешнего провайдера Parkomatika (на стенде — мок) |

## Переменные окружения для CI/CD
| Имя переменной | Stage | Описание |
|---|---|---|
| `STAND_POSTGRES_PASSWORD` | секрет | пароль БД стенда (иначе генерируется один раз в ~/.specforge-stand.env на VM) |
| `STAND_JWT_SIGNING_KEY` | секрет | ключ подписи JWT стенда (аналогично) |
| `STAND_POSTGRES_PORT` | 15432 | порт PostgreSQL на хосте VM (5432 занят системным PostgreSQL) |
| `STAND_EXTRA_ENV` | — | дополнительные строки .env стенда, например TIME_SCALE=120 |
| `API_IMAGE` | specforge/backend:$CI_COMMIT_SHORT_SHA | образ backend; задаётся в .gitlab-ci.yml |
| `WEB_IMAGE` | specforge/web:$CI_COMMIT_SHORT_SHA | образ web-админки |
| `STAND_URL` | http://127.0.0.1:8080 | адрес стенда для e2e (раннер на той же VM) |

## Мониторинг
### Мониторинг OS Linux и Docker сервиса
zabbix-agent2 на VM по стандартным шаблонам организаторов (Linux by Zabbix agent, Docker by Zabbix agent 2); контейнеры стенда — `specforge-<сервис>-1`.
### Мониторинг Application
Технический мониторинг — Prometheus, `GET /metrics`:

| Метрика | Что показывает |
|---|---|
| `app_outbox_published` | события, опубликованные в Kafka |
| `app_outbox_publish_failures` | неудачные попытки публикации |
| `app_outbox_dead_lettered` | события, отправленные в DLQ |
| `app_job_runs` | проходы фоновых задач |
| `app_job_failures` | упавшие проходы фоновых задач |
| `http_requests_received_total, http_request_duration_seconds` | запросы и время ответа по маршрутам (prometheus-net) |
| `healthcheck (ForwardToPrometheus)` | состояние каждой health-проверки |

Бизнес-мониторинг — `GET /health` (Healthy / Degraded — 200, Unhealthy — 503), `GET /health/ready` (готовность: БД и брокер), `GET /health/live` (процесс жив). Перезапуск: `POST /api/v1/system/restarts` (роль `admin`).

| Проверка | Группа | Состояние при сбое | Что значит |
|---|---|---|---|
| `postgres` | ready | Unhealthy | нет соединения с PostgreSQL |
| `kafka` | ready | Degraded | брокер недоступен: API работает, события копятся в outbox |
| `outbox` | business | Degraded | события ждут публикации дольше порога или уходят в DLQ |
| `jobs` | business | Degraded | фоновая задача (таймер предметной области) падает 3 прохода подряд |

## Логирование
Структурированные JSON-логи (время в UTC) в STDOUT/STDERR контейнера: сбор в Elasticsearch через `docker logs`, просмотр в Kibana. Ошибка запроса возвращает `traceId` в теле problem+json — по нему запись находится в логах; заголовки `X-Request-Id` и `X-Correlation-Id` принимаются и возвращаются в ответе.

## Сетевая связность
Наружу публикуется только порт backend 8080; PostgreSQL и моки провайдеров слушают 127.0.0.1 на VM, Kafka — только внутри сети compose. Исходящие соединения — только к провайдерам из таблицы выше.

## Доступы к сервису
Клиенты web-админки и мобильного приложения по HTTP 8080; административные операции — роль `admin`. Доступ к VM — SSH команды.
