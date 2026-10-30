# Архитектура

## Компоненты

```mermaid
flowchart LR
    role_driver(["Водитель"])
    role_dispatcher(["Диспетчер"])
    Mobile_App["Mobile App<br/><small>Kotlin 2.2, Jetpack Compose, Material 3</small>"]
    Admin_Web["Admin Web<br/><small>Angular 20, UI Kit, MapLibre</small>"]
    Backend_API["Backend API<br/><small>ASP.NET Core 10, C#35, 14, EF Core + NetTopologySuite, SignalR</small>"]
    Background_Worker["Background Worker<br/><small>.NET 10 BackgroundService</small>"]
    PostgreSQL___PostGIS[("PostgreSQL + PostGIS<br/><small>PostgreSQL 17.9, PostGIS</small>")]
    Kafka[["Kafka<br/><small>Apache Kafka</small>"]]
    Parkomatica_Mock[/"Parkomatica Mock<br/><small>ASP.NET Core minimal API</small>"/]
    Official_Payment_Service{{"Official Payment Service<br/><small>Сайт и приложения «Парковки Санкт-Петербурга» / «Парковки России»</small>"}}
    role_driver --> Mobile_App
    role_dispatcher --> Admin_Web
    Backend_API -->|"публикует события"| Kafka
    Kafka -->|"события"| Background_Worker
    Kafka -->|"события"| Backend_API
    Background_Worker -->|"публикует события"| Kafka
    Mobile_App -->|"REST /api/v1, HTTPS + JWT"| Backend_API
    Admin_Web -->|"REST /api/v1 + обновления в реальном времени"| Backend_API
    Backend_API -->|"SQL"| PostgreSQL___PostGIS
    Background_Worker -->|"SQL"| PostgreSQL___PostGIS
    Backend_API -->|"адаптер, REST"| Parkomatica_Mock
    Mobile_App -->|"deeplink / ссылка"| Official_Payment_Service
```

| Компонент | Тип | Технология | Ответственность |
| --- | --- | --- | --- |
| Mobile App | mobile | Kotlin 2.2, Jetpack Compose, Material 3 | Карта, текущая зона, статус и таймер сессии, уведомления, передача координат, переход к оплате |
| Admin Web | web | Angular 20, UI Kit, MapLibre | Просмотр автомобилей, зон, сессий, маршрутов, журнала событий; загрузка зон; запуск демо-маршрута |
| Backend API | backend | ASP.NET Core 10, C# 14, EF Core + NetTopologySuite, SignalR | REST API, определение зоны, жизненный цикл сессий, outbox событий, push обновлений в Admin Web |
| Background Worker | worker | .NET 10 BackgroundService | Таймеры сессий (expiring/expired), публикация outbox в Kafka, создание уведомлений, проигрывание демо-маршрутов |
| PostgreSQL + PostGIS | database | PostgreSQL 17.9, PostGIS | Данные, геозапросы, outbox, ключи идемпотентности |
| Kafka | broker | Apache Kafka | Доменные события между компонентами |
| Parkomatica Mock | mock | ASP.NET Core minimal API | Имитация API поставщика парковочных сессий за адаптером IParkingProvider |
| Official Payment Service | external | Сайт и приложения «Парковки Санкт-Петербурга» / «Парковки России» | Оплата парковки водителем |

## Ключевые решения

- Бизнес-логика определения зоны и жизненного цикла сессии выполняется на сервере: устройство только передаёт координаты, поэтому правила едины для мобильного приложения и демо-режима
- Зона определяется запросом ST_Contains по GIST-индексу; вход и выход фиксируются после двух подряд точек, чтобы дрожание GPS на границе не порождало ложных уведомлений
- Поставщик парковочных услуг скрыт за интерфейсом IParkingProvider; в прототипе реализацию даёт Parkomatica Mock, замена поставщика не затрагивает доменный слой
- События пишутся в outbox в одной транзакции с данными и публикуются в Kafka фоновым процессом, поэтому событие не теряется и не публикуется без изменения данных
- Таймеры окончания сессий выполняет Background Worker по времени paidUntil в UTC; мобильное приложение дублирует таймер локально на случай потери связи
- Административный интерфейс получает новые события через SignalR без перезагрузки страницы
- Доступ по JWT с ролями driver и dispatcher; водитель работает только с закреплённым за ним автомобилем

Подробно — в журнале решений:

- [ADR-001. Где определяется платная зона](adr/ADR-001.md)
- [ADR-002. Защита от дрожания GPS на границе зоны](adr/ADR-002.md)
- [ADR-003. Интеграция с поставщиком парковочных услуг](adr/ADR-003.md)
- [ADR-004. Источник времени окончания оплаты](adr/ADR-004.md)
- [ADR-005. Доставка событий между компонентами](adr/ADR-005.md)
- [ADR-006. Аутентификация пользователей](adr/ADR-006.md)

## Въезд в платную зону

```mermaid
sequenceDiagram
    autonumber
    participant Mobile_App as Mobile App
    participant Backend_API as Backend API
    participant PostgreSQL___PostGIS as PostgreSQL + PostGIS
    participant Background_Worker as Background Worker
    participant Kafka as Kafka
    participant Admin_Web as Admin Web
    Mobile_App->>Backend_API: POST /vehicles/{id}/positions (точка, recordedAt)
    Backend_API->>PostgreSQL___PostGIS: ST_Contains: зона для точки, две последние точки
    PostgreSQL___PostGIS-->>Backend_API: зона 7801
    Backend_API->>PostgreSQL___PostGIS: ParkingEvent zone_entered + outbox в одной транзакции
    Backend_API-->>Mobile_App: 202 transition=entered, «Вы въехали в зону платной парковки»
    Background_Worker->>Kafka: parking.zone.entered.v1 из outbox
    Kafka->>Background_Worker: parking.zone.entered → Notification
    Backend_API->>Admin_Web: SignalR: новое событие в журнале
```

## Запуск парковочной сессии

```mermaid
sequenceDiagram
    autonumber
    participant ________ as Водитель
    participant Mobile_App as Mobile App
    participant Backend_API as Backend API
    participant Parkomatica_Mock as Parkomatica Mock
    participant PostgreSQL___PostGIS as PostgreSQL + PostGIS
    ________->>Mobile_App: «Начать сессию», 1 час
    Mobile_App->>Backend_API: POST /sessions, Idempotency-Key
    Backend_API->>Parkomatica_Mock: IParkingProvider.StartSession(зона, автомобиль, 60 мин)
    Parkomatica_Mock-->>Backend_API: providerSessionId, paidUntil 15:20
    Backend_API->>PostgreSQL___PostGIS: ParkingSession active + outbox session.started
    Backend_API-->>Mobile_App: 201 ParkingSession
    Mobile_App-->>________: «Парковка оплачена до 15:20»
```

## Предупреждение об окончании и переход к оплате

```mermaid
sequenceDiagram
    autonumber
    participant Background_Worker as Background Worker
    participant PostgreSQL___PostGIS as PostgreSQL + PostGIS
    participant Kafka as Kafka
    participant Mobile_App as Mobile App
    participant Backend_API as Backend API
    participant ________ as Водитель
    participant Official_Payment_Service as Official Payment Service
    Background_Worker->>PostgreSQL___PostGIS: сессии с paidUntil ≤ now + 10 мин → status expiring + outbox
    Background_Worker->>Kafka: parking.session.expiring.v1
    Mobile_App->>Backend_API: GET /me/notifications?unreadOnly=true (опрос) 
    Backend_API-->>Mobile_App: «До окончания парковки осталось 10 минут»
    ________->>Mobile_App: «Оплатить»
    Mobile_App->>Backend_API: GET /zones/{zoneId}/payment-link
    Backend_API-->>Mobile_App: appUrl, webUrl
    Mobile_App->>Official_Payment_Service: deeplink с номером зоны
```

## Жизненный цикл: ParkingSession.status

```mermaid
stateDiagram-v2
    [*] --> active
    active --> expiring: до paidUntil осталось 10 минут
    expiring --> active: водитель подтвердил продление
    expiring --> expired: наступил paidUntil
    expired --> active: водитель подтвердил новую оплату
    active --> closed: водитель завершил сессию
    expiring --> closed: водитель завершил сессию
    expired --> closed: водитель завершил сессию или начал новую
```
