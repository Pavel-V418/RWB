# Архитектура

## Компоненты

```mermaid
flowchart LR
    role_warehouse_keeper(["Кладовщик"])
    role_warehouse_manager(["Руководитель склада"])
    role_employee(["Сотрудник"])
    role_system_admin(["Администратор системы"])
    Mobile_App["Mobile App<br/><small>Kotlin</small>"]
    Web_UI["Web UI<br/><small>Angular</small>"]
    Backend_API["Backend API<br/><small>ASP.NET Core</small>"]
    Worker["Worker<br/><small>ASP.NET Core Worker</small>"]
    Database[("Database<br/><small>PostgreSQL</small>")]
    Kafka_Broker[["Kafka Broker<br/><small>Kafka</small>"]]
    TM__Mock_Consumer[/"TMЦ Mock Consumer<br/><small>Mock</small>"/]
    Employee_CSV_File{{"Employee CSV File<br/><small>CSV</small>"}}
    role_warehouse_keeper --> Mobile_App
    role_warehouse_manager --> Web_UI
    role_employee --> Web_UI
    role_system_admin --> Web_UI
    Backend_API -->|"публикует события"| Kafka_Broker
    Kafka_Broker -->|"события"| TM__Mock_Consumer
    Mobile_App -->|"REST /api/v1, HTTPS + JWT"| Backend_API
    Web_UI -->|"REST /api/v1 + обновления в реальном времени"| Backend_API
    Backend_API -->|"SQL"| Database
    Worker -->|"SQL"| Database
    Backend_API -->|"адаптер, REST"| TM__Mock_Consumer
    Mobile_App -->|"deeplink / ссылка"| Employee_CSV_File
```

| Компонент | Тип | Технология | Ответственность |
| --- | --- | --- | --- |
| Mobile App | mobile | Kotlin | Мобильное приложение кладовщика: сканирование QR-кодов, локальное хранение операций при отсутствии связи, синхронизация с сервером |
| Web UI | web | Angular | Web-интерфейс для руководителя склада и администратора: каталог, история, список невозвращённого, загрузка справочника |
| Backend API | backend | ASP.NET Core | REST API, бизнес-логика, аутентификация, авторизация, публикация событий в Kafka |
| Worker | worker | ASP.NET Core Worker | Scheduled jobs: проверка сроков возврата, обновление статусов IssueRecord |
| Database | database | PostgreSQL | Хранение данных: Tool, Employee, Reservation, IssueRecord, ReturnRecord, MovementHistory, SyncQueue, AuditLog, SystemSetting |
| Kafka Broker | broker | Kafka | Передача событий выдачи, возврата и списания в корпоративную систему учёта ТМЦ |
| TMЦ Mock Consumer | mock | Mock | Mock-потребитель событий из Kafka для интеграции с корпоративной системой учёта ТМЦ |
| Employee CSV File | external | CSV | Файл справочника сотрудников и номеров пропусков для загрузки |

## Ключевые решения

- Бизнес-логика сосредоточена в ASP.NET Core Backend API, который обеспечивает атомарность операций и валидацию бизнес-правил.
- Внешняя система учёта ТМЦ подключена через событийную шину Kafka, что обеспечивает асинхронность и отказоустойчивость интеграции.
- Мобильное приложение использует локальную базу данных для хранения операций при отсутствии связи, синхронизируясь с сервером через очередь SyncQueue.
- Справочник сотрудников загружается из CSV-файла, что позволяет легко обновлять данные без изменения кода приложения.
- Данные защищены ролевой моделью доступа (RBAC), где каждый пользователь имеет строго определённые права на операции.
- События Kafka содержат минимальный набор данных (eventType, toolId, employeeId, timestamp) для обеспечения совместимости с внешними системами.

Подробно — в журнале решений:

- [ADR-001. Выбор стека технологий для клиентских приложений](adr/ADR-001.md)
- [ADR-002. Механизм передачи событий во внешнюю систему](adr/ADR-002.md)
- [ADR-003. Обработка работы без сети в мобильном приложении](adr/ADR-003.md)
- [ADR-004. Определение срока возврата инструмента](adr/ADR-004.md)
- [ADR-005. Формат загрузки справочника сотрудников](adr/ADR-005.md)

## Добавление инструмента и печать QR-кода

```mermaid
sequenceDiagram
    autonumber
    participant warehouse_manager as warehouse_manager
    participant Web_UI as Web UI
    participant Backend_API as Backend API
    participant Database as Database
    warehouse_manager->>Web_UI: Открывает форму добавления инструмента
    warehouse_manager->>Web_UI: Вводит данные: инвентарный номер, наименование, категория, склад, состояние
    Web_UI->>Backend_API: POST /api/v1/tools
    Backend_API->>Database: Проверяет уникальность инвентарного номера
    Database-->>Backend_API: Уникальность подтверждена
    Backend_API->>Database: Сохраняет Tool, генерирует QR-код
    Database-->>Backend_API: Запись сохранена
    Backend_API-->>Web_UI: 201 Created: Tool с QR-кодом
    Web_UI->>warehouse_manager: Отображает QR-код для печати
```

## Выдача инструмента сотруднику

```mermaid
sequenceDiagram
    autonumber
    participant warehouse_keeper as warehouse_keeper
    participant Mobile_App as Mobile App
    participant Backend_API as Backend API
    participant Database as Database
    participant Kafka_Broker as Kafka Broker
    warehouse_keeper->>Mobile_App: Сканирует QR-код инструмента
    Mobile_App->>Backend_API: GET /api/v1/tools/qr/{qrCode}
    Backend_API->>Database: Ищет Tool по QR-коду
    Database-->>Backend_API: Находит Tool
    Backend_API-->>Mobile_App: 200 OK: Tool
    warehouse_keeper->>Mobile_App: Сканирует пропуск сотрудника
    Mobile_App->>Backend_API: GET /api/v1/employees/pass/{passNumber}
    Backend_API->>Database: Ищет Employee по passNumber
    Database-->>Backend_API: Находит Employee
    Backend_API-->>Mobile_App: 200 OK: Employee
    Mobile_App->>Backend_API: POST /api/v1/issue-records
    Backend_API->>Database: Проверяет доступность инструмента и бронирования
    Database-->>Backend_API: Инструмент доступен
    Backend_API->>Database: Создаёт IssueRecord, обновляет Tool
    Database-->>Backend_API: Записи сохранены
    Backend_API->>Kafka_Broker: Публикует tools.tool.issued
    Kafka_Broker-->>Backend_API: Событие опубликовано
    Backend_API-->>Mobile_App: 201 Created: IssueRecord
```

## Возврат инструмента с оценкой состояния

```mermaid
sequenceDiagram
    autonumber
    participant warehouse_keeper as warehouse_keeper
    participant Mobile_App as Mobile App
    participant Backend_API as Backend API
    participant Database as Database
    participant Kafka_Broker as Kafka Broker
    warehouse_keeper->>Mobile_App: Сканирует QR-код возвращаемого инструмента
    Mobile_App->>Backend_API: GET /api/v1/tools/qr/{qrCode}
    Backend_API->>Database: Ищет Tool и активную IssueRecord
    Database-->>Backend_API: Находит Tool и IssueRecord
    Backend_API-->>Mobile_App: 200 OK: Tool, IssueRecord
    warehouse_keeper->>Mobile_App: Выбирает состояние: исправен, требует ремонта, утерян
    Mobile_App->>Backend_API: POST /api/v1/return-records
    Backend_API->>Database: Создаёт ReturnRecord, обновляет IssueRecord и Tool
    Database-->>Backend_API: Записи сохранены
    Backend_API->>Kafka_Broker: Публикует tools.tool.returned
    Kafka_Broker-->>Backend_API: Событие опубликовано
    Backend_API-->>Mobile_App: 201 Created: ReturnRecord
```

## Синхронизация операций после появления связи

```mermaid
sequenceDiagram
    autonumber
    participant Mobile_App as Mobile App
    participant Backend_API as Backend API
    participant Database as Database
    participant Kafka_Broker as Kafka Broker
    Mobile_App->>Mobile_App: Обнаруживает восстановление связи
    Mobile_App->>Mobile_App: Читает операции из локального SyncQueue
    Mobile_App->>Backend_API: POST /api/v1/sync-operations
    Backend_API->>Database: Проверяет конфликты для каждой операции
    Database-->>Backend_API: Конфликты проверены
    Backend_API->>Database: Выполняет операции (issue, return, reservation)
    Database-->>Backend_API: Операции выполнены
    Backend_API->>Kafka_Broker: Публикует события для успешных операций
    Kafka_Broker-->>Backend_API: События опубликованы
    Backend_API-->>Mobile_App: 200 OK: результат синхронизации
    Mobile_App->>Mobile_App: Очищает SyncQueue для синхронизированных операций
```

## Жизненный цикл: Tool.status

```mermaid
stateDiagram-v2
    [*] --> available
    available --> reserved: create_reservation
    reserved --> available: cancel_reservation
    available --> issued: issue_tool
    reserved --> issued: issue_tool
    issued --> available: return_tool_working
    issued --> repair: return_tool_needs_repair
    issued --> lost: return_tool_lost
    repair --> available: repair_completed
    available --> retired: retire_tool
    repair --> retired: retire_tool
```

## Жизненный цикл: Reservation.status

```mermaid
stateDiagram-v2
    [*] --> active
    active --> cancelled: cancel_reservation
    active --> fulfilled: issue_tool
```

## Жизненный цикл: IssueRecord.status

```mermaid
stateDiagram-v2
    [*] --> active
    active --> overdue: scheduled_job_check_deadline
    active --> returned: return_tool
    overdue --> returned: return_tool
```

## Жизненный цикл: SyncQueue.status

```mermaid
stateDiagram-v2
    [*] --> pending
    pending --> synced: sync_success
    pending --> rejected: sync_conflict
```
