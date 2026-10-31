# API

Спецификации: [OpenAPI 3.1](contracts/openapi.yaml), [AsyncAPI 3.0](contracts/asyncapi.yaml).

- базовый путь `/api/v1`, аутентификация `Authorization: Bearer <JWT>`;
- ошибки — `application/problem+json` (RFC 9457) с `traceId`;
- критичные операции — заголовок `Idempotency-Key`; превышение лимита — 429 и `Retry-After`;
- списки — `page`, `pageSize`, ответ `{items, page, pageSize, total}`.

## Tools

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `POST /api/v1/tools` | Добавить инструмент в каталог | warehouse_manager | FR-1, FR-2, FR-12 |
| `GET /api/v1/tools/{id}` | Получить инструмент по ID | warehouse_manager, warehouse_keeper, employee | FR-1 |
| `GET /api/v1/tools` | Получить список инструментов | warehouse_manager, warehouse_keeper, employee | FR-1 |
| `GET /api/v1/tools/qr/{qrCode}` | Получить инструмент по QR-коду | warehouse_keeper | FR-3 |
| `POST /api/v1/tools/{id}/retirements` | Списать инструмент | warehouse_manager | FR-8 |

## Reservations

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `POST /api/v1/reservations` | Забронировать инструмент на дату | employee, warehouse_manager | FR-4, FR-13 |
| `POST /api/v1/reservations/{id}/cancellations` | Отменить бронирование | employee, warehouse_manager | FR-4 |
| `GET /api/v1/reservations` | Получить список бронирований | warehouse_manager, warehouse_keeper, employee | FR-4 |

## IssueRecords

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `POST /api/v1/issue-records` | Выдать инструмент сотруднику | warehouse_keeper | FR-3, FR-5, FR-14 |
| `GET /api/v1/issue-records` | Получить список записей о выдаче | warehouse_manager, warehouse_keeper | FR-7 |

## ReturnRecords

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `POST /api/v1/return-records` | Зафиксировать возврат инструмента | warehouse_keeper | FR-6, FR-17 |
| `GET /api/v1/return-records` | Получить список записей о возврате | warehouse_manager, warehouse_keeper | FR-6 |

## MovementHistory

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `GET /api/v1/tools/{id}/movements` | Получить историю движения инструмента | warehouse_manager, warehouse_keeper | FR-9 |

## EmployeeImports

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `POST /api/v1/employee-imports` | Загрузить справочник сотрудников из CSV-файла | system_admin | FR-11 |

## Employees

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `GET /api/v1/employees` | Получить список сотрудников | warehouse_manager, warehouse_keeper, system_admin | FR-11 |
| `GET /api/v1/employees/pass/{passNumber}` | Получить сотрудника по номеру пропуска | warehouse_keeper | FR-3 |

## SyncOperations

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `POST /api/v1/synchronization-operations` | Синхронизировать локальные операции с сервером | warehouse_keeper | FR-10, FR-15 |

## SystemSettings

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `GET /api/v1/system-settings` | Получить настройки системы | system_admin, warehouse_manager | FR-7 |
| `PATCH /api/v1/system-settings/{key}` | Обновить настройку системы | system_admin | FR-7 |

## AuditLogs

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `GET /api/v1/audit-logs` | Получить журнал аудита | system_admin | FR-3, FR-4, FR-6, FR-11, FR-12, FR-13 |

## Users

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `POST /api/v1/users` | Создать пользователя системы | system_admin | FR-3, FR-4, FR-6, FR-11, FR-12, FR-13 |
| `GET /api/v1/users` | Получить список пользователей | system_admin | FR-3, FR-4, FR-6, FR-11, FR-12, FR-13 |

## События

| Событие | Топик | Производитель | Потребители |
| --- | --- | --- | --- |
| `tools.tool.issued` | `tools.tool.issued.v1` | Backend API | TMЦ Mock Consumer |
| `tools.tool.returned` | `tools.tool.returned.v1` | Backend API | TMЦ Mock Consumer |
| `tools.tool.retired` | `tools.tool.retired.v1` | Backend API | TMЦ Mock Consumer |
