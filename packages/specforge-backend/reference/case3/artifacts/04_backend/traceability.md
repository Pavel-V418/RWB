# Трассируемость backend: требование → API → код → тест

Сгенерировано `generators/docs/traceability.py` из OpenAPI, кода фич и интеграционных тестов.
Тест «пропущен» — `[Fact(Skip=...)]`: агент не смог добиться прохождения, причина в тексте Skip.

| Требование | Операции API | Реализация | Тесты |
| --- | --- | --- | --- |
| FR-1 Ведение каталога инструмента | `GET /api/v1/tools`<br>`POST /api/v1/tools`<br>`GET /api/v1/tools/{id}` | `src/App.Application/Features/Tools/ToolsApi.cs` | — |
| FR-2 Печать QR-кода для инструмента | `POST /api/v1/tools` | `src/App.Application/Features/Tools/ToolsApi.cs` | — |
| FR-3 Выдача инструмента сотруднику | `GET /api/v1/tools/qr/{qrCode}`<br>`POST /api/v1/issue-records`<br>`GET /api/v1/employees/pass/{passNumber}`<br>`GET /api/v1/audit-logs`<br>`GET /api/v1/users`<br>`POST /api/v1/users` | `src/App.Application/Features/AuditLogs/AuditLogsApi.cs`<br>`src/App.Application/Features/Employees/EmployeesApi.cs`<br>`src/App.Application/Features/IssueRecords/IssueRecordsApi.cs`<br>`src/App.Application/Features/Tools/ToolsApi.cs`<br>`src/App.Application/Features/Users/UsersApi.cs` | — |
| FR-4 Бронирование инструмента на дату | `GET /api/v1/reservations`<br>`POST /api/v1/reservations`<br>`POST /api/v1/reservations/{id}/cancellations`<br>`GET /api/v1/audit-logs`<br>`GET /api/v1/users`<br>`POST /api/v1/users` | `src/App.Application/Features/AuditLogs/AuditLogsApi.cs`<br>`src/App.Application/Features/Reservations/ReservationsApi.cs`<br>`src/App.Application/Features/Users/UsersApi.cs` | — |
| FR-5 Запрет выдачи забронированного инструмента | `POST /api/v1/issue-records` | `src/App.Application/Features/IssueRecords/IssueRecordsApi.cs` | — |
| FR-6 Фиксация возврата инструмента | `GET /api/v1/return-records`<br>`POST /api/v1/return-records`<br>`GET /api/v1/audit-logs`<br>`GET /api/v1/users`<br>`POST /api/v1/users` | `src/App.Application/Features/AuditLogs/AuditLogsApi.cs`<br>`src/App.Application/Features/ReturnRecords/ReturnRecordsApi.cs`<br>`src/App.Application/Features/Users/UsersApi.cs` | — |
| FR-7 Контроль срока возврата | `GET /api/v1/issue-records`<br>`GET /api/v1/system-settings`<br>`PATCH /api/v1/system-settings/{key}` | `src/App.Application/Features/IssueRecords/IssueRecordsApi.cs`<br>`src/App.Application/Features/SystemSettings/SystemSettingsApi.cs` | — |
| FR-8 Передача событий в систему учёта ТМЦ | `POST /api/v1/tools/{id}/retirements` | `src/App.Application/Features/Tools/ToolsApi.cs` | — |
| FR-9 История движения инструмента | `GET /api/v1/tools/{id}/movements` | `src/App.Application/Features/MovementHistory/MovementHistoryApi.cs` | — |
| FR-10 Работа в мобильном приложении без связи | `POST /api/v1/synchronization-operations` | `src/App.Application/Features/SyncOperations/SyncOperationsApi.cs` | — |
| FR-11 Загрузка справочника сотрудников из файла | `POST /api/v1/employee-imports`<br>`GET /api/v1/employees`<br>`GET /api/v1/audit-logs`<br>`GET /api/v1/users`<br>`POST /api/v1/users` | `src/App.Application/Features/AuditLogs/AuditLogsApi.cs`<br>`src/App.Application/Features/EmployeeImports/EmployeeImportsApi.cs`<br>`src/App.Application/Features/Employees/EmployeesApi.cs`<br>`src/App.Application/Features/Users/UsersApi.cs` | — |
| FR-12 Добавление инструмента руководителем | `POST /api/v1/tools`<br>`GET /api/v1/audit-logs`<br>`GET /api/v1/users`<br>`POST /api/v1/users` | `src/App.Application/Features/AuditLogs/AuditLogsApi.cs`<br>`src/App.Application/Features/Tools/ToolsApi.cs`<br>`src/App.Application/Features/Users/UsersApi.cs` | — |
| FR-13 Бронирование сотрудником | `POST /api/v1/reservations`<br>`GET /api/v1/audit-logs`<br>`GET /api/v1/users`<br>`POST /api/v1/users` | `src/App.Application/Features/AuditLogs/AuditLogsApi.cs`<br>`src/App.Application/Features/Reservations/ReservationsApi.cs`<br>`src/App.Application/Features/Users/UsersApi.cs` | — |
| FR-14 Отказ в выдаче забронированного инструмента | `POST /api/v1/issue-records` | `src/App.Application/Features/IssueRecords/IssueRecordsApi.cs` | — |
| FR-15 Синхронизация операций после появления связи | `POST /api/v1/synchronization-operations` | `src/App.Application/Features/SyncOperations/SyncOperationsApi.cs` | — |
| FR-16 Показ невозвращённого инструмента при истечении срока | — | — | — |
| FR-17 Возврат инструмента с отметкой ремонта | `POST /api/v1/return-records` | `src/App.Application/Features/ReturnRecords/ReturnRecordsApi.cs` | — |

Итого: требований 17, реализовано полностью 16, покрыто тестами 0; операций 22, тестов 0 (пропущено 0).
