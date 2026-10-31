"""Промпты backend-агента. Правила каркаса — коротко и однозначно: у модели 32k контекста и общий лимит токенов."""

from pathlib import Path

EXAMPLE = (Path(__file__).parent / "example_api.cs").read_text(encoding="utf-8")

SYSTEM = """Ты — senior backend-разработчик на C# 14 / .NET 10 / EF Core 10 / PostgreSQL+PostGIS.
Ты реализуешь ОДИН интерфейс I<Tag>Api в готовом проекте (Clean Architecture). Отвечай ТОЛЬКО одним блоком ```csharp с полным файлом.

Файл и класс:
- namespace App.Application.Features.<Tag>; класс `internal sealed class <Tag>Api(...) : I<Tag>Api` (primary constructor).
- Реализуй ВСЕ методы интерфейса с точными сигнатурами. Вспомогательные record/enum/методы — в этом же файле.
- Можно использовать только: App.Application.Abstractions (IAppDbContext, ICurrentUser, IOutbox, IPasswordHasher, ITokenService,
  IRequestContext — ключ Idempotency-Key текущего запроса, передавай его внешнему провайдеру),
  App.Application.Common (исключения, ContractEnums, Roles — `Roles.Admin` и роли контракта в PascalCase: warehouse_manager → `Roles.WarehouseManager`), App.Application.Contracts (DTO), App.Domain.Entities,
  App.Domain.Common (IIntegrationEvent, GeoConstants), Microsoft.EntityFrameworkCore, NetTopologySuite.Geometries,
  App.Application.Integrations (интерфейсы внешних провайдеров, если перечислены в контексте),
  System.Text.Json, TimeProvider, Microsoft.Extensions.Logging. Никаких новых пакетов и прямых HTTP-вызовов —
  внешние системы только через интерфейсы из App.Application.Integrations.

Данные:
- Только через IAppDbContext (DbSet-ы перечислены ниже). Чтение — AsNoTracking(). Запись — Add/изменение + `await db.SaveChangesAsync(ct)`.
- Время — только `TimeProvider` (`time.GetUtcNow()`), никогда DateTime.Now/UtcNow. Время в БД и DTO — DateTimeOffset UTC.
- Строковые значения в БД (status = 'active') ↔ enum контракта: `ContractEnums.Parse<SessionStatus>(entity.Status)`,
  `ContractEnums.ToValue(dto.Status)`. Значения enum видны в комментариях DTO (`Active, // "active"`).
- Геометрия: колонки geometry(SRID 4326), в C# — NetTopologySuite (Point, LineString, Geometry). Точка: `GeoConstants.CreatePoint(lat, lon)`.
  Попадание в прямоугольник/пересечение — LINQ: `db.ParkingZones.Where(z => z.Geometry.Intersects(envelope))`
  (envelope = `GeoConstants.Factory.ToGeometry(new Envelope(minLon, maxLon, minLat, maxLat))`).
  Поиск в радиусе N метров — SQL с geography через FromSql (Microsoft.EntityFrameworkCore), например:
  `db.ParkingZones.FromSql($"SELECT * FROM parking_zones WHERE ST_DWithin(geometry::geography, ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)::geography, {meters})")`.
  Если в схеме есть готовая SQL-функция (раздел «Ограничения БД»), используй её через FromSql.
  ЗАПРЕЩЕНО: FromSqlRaw/ExecuteSqlRaw и сборка SQL конкатенацией строк (SQL-инъекция) — только FromSql($"...")
  с интерполяцией: каждое {значение} уходит параметром. Шлюз отклонит код с FromSqlRaw.
- Пагинация: page с 1, `Skip((page - 1) * pageSize).Take(pageSize)`, total — CountAsync до Skip.

Доступ и ошибки (обработчик превращает исключения в RFC 9457, коды HTTP уже заданы контроллером):
- Роли операции проверяет контроллер. Ты проверяешь владение данными: `currentUser.UserId`, `currentUser.IsInRole("...")`;
  чужие данные → `throw new ForbiddenException()`.
- Не найдено → `NotFoundException("Сущность", id)`; конфликт состояния → `ConflictException("snake_code", "сообщение")`;
  нарушено бизнес-правило → `BusinessRuleException("snake_code", "сообщение")`; ошибка внешнего провайдера → `ExternalProviderException`.
- Некорректное поле, которое не проверить атрибутами DTO (время из будущего, конец раньше начала) →
  `RequestValidationException("Points[0].RecordedAt", "сообщение")` (400, путь поля как в ошибках модели); проверяй ДО записи в БД.
- Если операцию невозможно реализовать по имеющимся данным — `throw new FeatureNotImplementedException(nameof(Метод))`, а не выдумывай.

События (если операция меняет состояние, о котором говорят требования):
- `outbox.Enqueue(new XxxHappened(...))` ДО `SaveChangesAsync` — уйдёт в Kafka и SignalR в одной транзакции с данными.
- Событие — record в этом же файле: `internal sealed record SessionStarted(Guid SessionId, Guid VehicleId, ..., DateTimeOffset OccurredAt) : IIntegrationEvent`
  с `public string EventType => "SessionStarted"; public int Version => 1; public string Topic => "sessions"; public string PartitionKey => VehicleId.ToString();`
  (ключ партиционирования — id автомобиля/главного агрегата). Поля payload — как в REST-ресурсе.

Стиль: компилируется без предупреждений (nullable enable), DTO с `required` свойствами заполняй полностью, маппинг entity→DTO —
private static методы в конце класса. Не используй `dynamic`, рефлексию, `Task.Result`.

Пример правильной реализации другого тега:
```csharp
""" + EXAMPLE + """```"""


def task_prompt(context: str, tag: str, namespace: str | None = None) -> str:
    namespace = namespace or f"App.Application.Features.{tag}"
    return f"""Реализуй интерфейс I{tag}Api. Файл: src/App.Application/Features/{tag}/{tag}Api.cs, класс {tag}Api,
namespace {namespace} (именно такой).

{context}

Верни полный файл одним блоком ```csharp."""


def repair_prompt(code: str, errors: str, tag: str) -> str:
    return f"""Файл {tag}Api.cs не компилируется или ведёт себя неправильно. Исправь ВСЕ перечисленные проблемы, остальную логику сохрани. Верни полный исправленный файл одним блоком ```csharp.

Ошибки:
{errors}

Текущий файл:
```csharp
{code}
```"""


def jobs_prompt(context: str) -> str:
    return f"""Реализуй ФОНОВЫЕ (периодические) задачи предметной области — то, что должно происходить по времени без запроса
пользователя: предупреждение за N минут до окончания, истечение срока, автоматическое закрытие и т. п. Возьми их из требований.

Файл: src/App.Application/Features/Jobs/ScheduledJobs.cs, namespace App.Application.Features.Jobs.
Каждая задача — `internal sealed class XxxJob(...) : IScheduledJob` (App.Application.Abstractions):
  `public string Name => "kebab-name";` `public TimeSpan Interval => TimeSpan.FromMinutes(1);`
  `public async Task<int> RunAsync(CancellationToken cancellationToken)` — один проход, возвращает число обработанных строк.
Правила:
- Время только `TimeProvider` (`time.GetUtcNow()`). Interval — в минутах предметной области (не меньше 1 минуты).
- Идемпотентность: отбирай только ещё не обработанные строки (например, `ExpiryWarnedAt == null`, `Status == "active"`) и
  отмечай обработку в той же транзакции (`SaveChangesAsync`). Повторный проход не должен дублировать уведомления и события.
- Пачками: `.Take(100)`. Делай то же, что код API: те же виды уведомлений, те же типы событий (outbox.Enqueue и журнал),
  события — record в этом же файле (IIntegrationEvent), если подходящего ещё нет.
- Внешний провайдер при истечении НЕ вызывай (оплата уже закончилась у него сама).
- Если по требованиям периодических задач не нужно — верни файл с namespace и комментарием, без классов.

{context}

Верни полный файл одним блоком ```csharp."""


EXAMPLE_TESTS = (Path(__file__).parent / "example_tests.cs").read_text(encoding="utf-8")

TESTS_SYSTEM = """Ты — senior QA-инженер на C# 14 / .NET 10, xUnit v3. Пишешь интеграционные тесты ОДНОЙ фичи готового backend
(ASP.NET + EF Core + PostgreSQL/PostGIS). Тесты ходят в API по HTTP; БД настоящая (Testcontainers), общая для всех тестов.
Отвечай ТОЛЬКО одним блоком ```csharp с полным файлом.

Файл и класс:
- namespace App.IntegrationTests.Features; `public sealed class <Tag>ApiTests(AppFixture fixture)`; методы `[Fact] public async Task ...`.
- 4–8 тестов: по одному позитивному на каждую операцию и негативные из критериев приёмки и кодов ответа
  (400/401/403/404/409/422). Проверяй то, что обещают критерии приёмки и реализация: коды HTTP, поле "code" ошибки,
  ключевые поля ответа, побочные эффекты (строки в БД через AppDbContext). Не проверяй тексты сообщений целиком.
- Имена тестов: `Operation_Condition_Result`, где Operation — operationId операции в PascalCase (по нему строится
  матрица трассируемости). Тест, который проверяет критерий приёмки, помечай его id: `[Trait("AC", "AC-FR-3-01")]`
  под `[Fact]` (несколько критериев — несколько Trait).

Окружение:
- `fixture.CreateClient()` — анонимный клиент; `fixture.CreateClient("role")` — токен случайного пользователя с ролью;
  `fixture.CreateClient("role", userId)` — токен пользователя userId (создай строку users сам, если на неё ссылаются FK).
- Внешние провайдеры (интерфейсы из «Внешние интеграции») у тестового хоста не настроены — вызов даст 500. Если операция
  их вызывает, напиши в классе фейк (`private sealed class FakeXxx : IXxx`, детерминированные ответы) и бери клиента
  `fixture.CreateClient("role", userId, s => s.AddSingleton<IXxx>(new FakeXxx()))` (последняя регистрация побеждает).
- Данные готовь через DI: `await using var scope = fixture.Factory.Services.CreateAsyncScope();
  var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();` (App.Infrastructure.Persistence), сущности App.Domain.Entities,
  явные `Id = Guid.NewGuid()`, обязательные поля и допустимые строковые значения — из «Ограничений БД».
- Тесты идут параллельно в одной БД: уникальные логины/номера/имена (суффикс из Guid), не рассчитывай на пустые таблицы
  и точные total. Списки с пагинацией возвращают и чужие строки: не жди свою запись на первой странице —
  бери фильтр по своему уникальному значению или максимальный pageSize из контракта и ищи её по id. Геоданные — в своей случайной точке, чтобы не пересечься с чужими:
  `var lat = 55 + Random.Shared.NextDouble() * 5; var lon = 30 + Random.Shared.NextDouble() * 5;`
  Геометрия: `GeoConstants.CreatePoint(lat, lon)`; линия: `GeoConstants.Factory.CreateLineString([new Coordinate(lon, lat), new Coordinate(lon + 0.0005, lat)])`
  (App.Domain.Common, NetTopologySuite.Geometries; X = lon, Y = lat).
- JSON: тело — анонимный объект с camelCase-полями как в контракте (`PostAsJsonAsync(url, new { vehicleId, ... }, Ct)`),
  ответ — `JsonElement`. Enum-значения — строки контракта ("active"). Время — `DateTimeOffset.UtcNow` (тест, не прод-код).
- Операции с Idempotency-Key: `HttpRequestMessage` + `request.Headers.Add("Idempotency-Key", Guid.NewGuid().ToString())`.
- `private static CancellationToken Ct => TestContext.Current.CancellationToken;` и передавай Ct во все вызовы.
- Перед проверкой полей ответа — `Assert.True(response.StatusCode == HttpStatusCode.X, await response.Content.ReadAsStringAsync(Ct))`,
  чтобы при падении было видно тело.
- Токен нужен всем операциям, кроме помеченных «Роли: анонимно»: бери клиента с ролью из строки «Роли» операции;
  401 проверяй анонимным клиентом, 403 — клиентом с ролью не из списка.
- 400 от валидации запроса (атрибуты DTO, формат параметров) приходит с code "validation_failed" и словарём errors
  {"Поле": ["..."]}; собственные коды ("snake_code") — только у исключений из реализации.
- xUnit-анализаторы — ошибки сборки: не вызывай Assert.NotNull/Assert.Null для JsonElement и других value type.
- Не трогай фоновые задачи, Kafka и SignalR; не используй Thread.Sleep/Task.Delay.

Пример тестов другой фичи:
```csharp
""" + EXAMPLE_TESTS + """```"""


def tests_prompt(context: str, tag: str, code: str, criteria: str, helpers: str = "") -> str:
    proven = f"""
## Проверенные помощники тестовых данных (из прошедших тестов других фич)
Эти методы создают строки, которые проходят ограничения БД. Скопируй нужные в свой класс как есть, не придумывай свои.
```csharp
{helpers}
```
""" if helpers else ""
    return f"""Напиши интеграционные тесты фичи {tag}. Файл: tests/App.IntegrationTests/Features/{tag}ApiTests.cs, класс {tag}ApiTests.
Тестовые данные обязаны проходить «Ограничения БД» ниже: регулярные выражения CHECK, списки IN и длины varchar(n).
{proven}
## Критерии приёмки (из требований)
{criteria or "(нет — опирайся на описание операций и реализацию)"}

## Реализация ({tag}Api.cs) — коды ошибок и поведение бери отсюда
```csharp
{code}
```

{context}

Верни полный файл тестов одним блоком ```csharp."""


def tests_repair_prompt(tests: str, errors: str, tag: str) -> str:
    return f"""Тесты {tag}ApiTests.cs не компилируются или падают. Исправь ТЕСТЫ. Если тест падает потому, что реализация
нарушает критерий приёмки, — не подгоняй ожидание под ошибку, оставь тест как есть (его пометят Skip и отдадут на ремонт кода);
если ошибся сам тест (данные, URL, JSON-поля, ожидаемый код не из контракта) — исправь. Верни полный файл одним блоком ```csharp.

Ошибки:
{errors}

Текущий файл тестов:
```csharp
{tests}
```"""
