# Золотой каркас backend (.NET 10)

Шаблон, в который детерминированные генераторы и backend-агент дописывают код приложения.
Всё сквозное (ошибки, безопасность, события, здоровье) уже здесь; доменный код только добавляется.

## Слои (Clean Architecture)

| Проект | Что лежит | Кто пишет |
|---|---|---|
| `App.Domain` | сущности, доменные события (`IIntegrationEvent`), `DomainException`, `GeoConstants` | генератор EF-моделей (сущности), backend-агент (события, правила) |
| `App.Application` | абстракции (`IAppDbContext`, `IOutbox`, `ICurrentUser`, …), сервисы сценариев, исключения → HTTP-коды | backend-агент |
| `App.Infrastructure` | EF Core + PostGIS, outbox → Kafka, JWT, PBKDF2, адаптеры внешних провайдеров | генератор EF-конфигураций, шаблон адаптера |
| `App.Api` | контроллеры `/api/v1`, SignalR `/hubs/events`, health, rate limit, идемпотентность | генератор контроллеров и DTO из OpenAPI |

Зависимости только внутрь: `Api → Infrastructure → Application → Domain`.

## Точки расширения (сюда пишут генераторы и агент)

- `AppDbContext.Generated.cs` — `partial void OnModelCreatingGenerated(ModelBuilder)` и `DbSet`-ы.
- `IAppDbContext.Generated.cs` — partial-интерфейс с `DbSet`-ами для Application.
- `DependencyInjection.AddApplicationServices` / `AddInfrastructureServices` — partial-методы регистрации.
- `ISubscriptionGuard` — кто может подписаться на realtime по ключу (по умолчанию только admin).
- Контроллеры: `[ApiController]`, `[Route("api/v1/...")]`, критичные операции помечаются `[Idempotent]`.

## Встроенные соглашения

- **Схема БД из SQL, без миграций EF:** `db/000_platform.sql` (outbox, идемпотентность), затем доменный DDL.
  Имена в БД — snake_case (`UseSnakeCaseNamingConvention`).
- **Ошибки:** RFC 9457 `application/problem+json` с полями `code` и `traceId`.
  `NotFoundException` → 404, `ConflictException` → 409, `BusinessRuleException`/`DomainException` → 422,
  `ExternalProviderException` → 502.
- **Безопасность:** всё требует JWT, если нет `[AllowAnonymous]`. Роли `admin`, `driver` (политики с теми же именами).
- **Rate limit:** `RateLimiting:PermitPerMinute` на пользователя/IP, ответ 429 + `Retry-After`.
- **События:** `IOutbox.Enqueue(event)` + `SaveChangesAsync` → в одной транзакции с данными;
  `OutboxPublisher` отправляет в Kafka (`Kafka:TopicPrefix` + `event.Topic`, ключ `PartitionKey`),
  затем в SignalR. После `Kafka:MaxAttempts` неудач — в `<topic>.dlq`. Потребители дедуплицируют по `eventId`.
- **Здоровье:** `/health/live` (процесс), `/health/ready` (Postgres + Kafka), `/health` (полная модель).
  Состояния: Healthy и Degraded → 200, Unhealthy → 503. Kafka недоступна или outbox отстаёт
  (`Kafka:MaxLagSeconds`, события в DLQ за час) → Degraded: API работает, события копятся.
  Перезапуск: `POST /api/v1/system/restarts` (только admin), контейнер поднимает restart policy.
  В контейнере — `dotnet App.Api.dll --healthcheck`.
- **Метрики:** `/metrics` в формате Prometheus — HTTP-запросы, рантайм .NET, статусы health checks,
  Meter `App` (`app_outbox_published`, `app_outbox_publish_failures`, `app_outbox_dead_lettered`).
  Доменные метрики — через `IMeterFactory` с именем Meter `App`. Наружу `/metrics` не публикуется.

- **Время:** только через `TimeProvider` (никаких `DateTime.UtcNow`). Таймеры — `Task.Delay(…, timeProvider)` или
  `PeriodicTimer(…, timeProvider)`, тогда `TIME_SCALE` ускоряет и их.
- **За прокси (nginx админки):** `UseForwardedHeaders` (X-Forwarded-For/Proto/Host), `X-Request-Id` становится
  идентификатором запроса и возвращается в ответе. API отвечает на `/api/v1/...` без обрезки префикса.

- **Внешние провайдеры:** интерфейс — в `App.Application/Integrations`, адаптер — наследник
  `ExternalProviderClient`, регистрация — `services.AddExternalProvider<IX, X>(configuration, "Name")`
  (секция `Providers:Name`: `BaseUrl`, `ApiKey`, `AttemptTimeoutSeconds`, `Retries`, `RetryDelayMs`).
  Повторы с backoff, таймауты, circuit breaker; любой сбой провайдера → 502 `external_provider_error`.
  Ключ `Idempotency-Key` текущего запроса — `IRequestContext.IdempotencyKey`, передаётся провайдеру.

- **Фоновые задачи (таймеры предметной области):** реализуй `IScheduledJob` (`Name`, `Interval`, `RunAsync`) и
  зарегистрируй `services.AddScoped<IScheduledJob, X>()`. `ScheduledJobsHost` гоняет каждую по `TimeProvider`
  (`TIME_SCALE` ускоряет), каждый проход — в своём DI-scope; 3 падения подряд → `/health` Degraded; метрики
  `app_job_runs`, `app_job_failures`. Токены JWT и здоровье outbox идут по реальным часам, а не по ускоренным.

## Команды

```bash
dotnet build App.slnx            # шлюз сборки (warnings = errors)
dotnet test --solution App.slnx  # unit + интеграционные (нужен Docker)
docker build -t app-api .        # образ
```

Интеграционные тесты поднимают `imresamu/postgis:17-3.5` и `apache/kafka:4.1.0` через Testcontainers.
Доменную схему можно подключить переменной `APP_DOMAIN_SCHEMA=/путь/schema.sql`.

## Конфигурация (переменные окружения)

| Переменная | Назначение |
|---|---|
| `ConnectionStrings__Database` | строка подключения Npgsql |
| `Kafka__Enabled`, `Kafka__BootstrapServers`, `Kafka__TopicPrefix`, `Kafka__MaxLagSeconds` | Kafka и порог Degraded для outbox |
| `Jwt__SigningKey` | ключ HMAC ≥ 32 байт (**обязателен**, без него API не стартует) |
| `Jwt__Issuer`, `Jwt__Audience`, `Jwt__LifetimeMinutes` | параметры токена |
| `Cors__AllowedOrigins__0` | origin админки |
| `RateLimiting__PermitPerMinute` | лимит запросов |
| `OpenApi__Enabled` | OpenAPI на `/swagger/v1/swagger.json` и Swagger UI на `/swagger` (по умолчанию только в Development) |
| `TIME_SCALE` (или `Time__Scale`) | ускорение времени и таймеров для демо и e2e: `10` — в 10 раз быстрее |
