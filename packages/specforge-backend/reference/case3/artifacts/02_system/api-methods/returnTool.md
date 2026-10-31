# Метод POST /api/v1/return-records

## Основная информация

| Параметр | Значение |
| --- | --- |
| Тип | POST |
| Наименование метода | `returnTool` |
| Назначение метода | Зафиксировать возврат инструмента |
| Ссылка на Swagger | [`openapi.yaml`](../openapi.yaml), operationId `returnTool` |
| Входящие атрибуты запроса | issueRecordId, toolId, employeeId, returnDate, condition |
| Реализует требования | FR-6, FR-17 |

## История изменений

| Дата | Версия | Описание изменения | Задача в JIRA | Автор |
| --- | --- | --- | --- | --- |
| 2026-10-30 | 1.0 | Описание метода | — | SpecForge, агент системного анализа |

## 1. Назначение

Зафиксировать возврат инструмента.

Транспорт: HTTPS (TLS 1.2+), HTTP/1.1. Схема взаимодействия: синхронный запрос — ответ. Формат тела: JSON, UTF-8.

## 2. Входные данные (Входящие параметры)

### Авторизация

JWT в заголовке `Authorization: Bearer <token>`; роль пользователя берётся из claims токена.

### Заголовки

| Наименование | Описание | Тип данных | Обязательность | Пример |
| --- | --- | --- | --- | --- |
| Authorization | токен доступа | string | да | `Bearer eyJhbGciOi…` |
| Content-Type | формат тела запроса | string | да | `application/json` |
| X-Correlation-Id | сквозной идентификатор запроса; если не передан — генерируется сервером | string | нет | `req-20261030-0001` |

### Параметры path

Отсутствуют.

### Параметры query

Отсутствуют.

### Модель данных тела запроса

| Поле | Описание | Тип данных | Обязательность | Варианты значений | Поле БД |
| --- | --- | --- | --- | --- | --- |
| issueRecordId | — | string (uuid) | да | — | `return_records.issue_record_id` |
| toolId | — | string (uuid) | да | — | `return_records.tool_id` |
| employeeId | — | string (uuid) | да | — | `return_records.employee_id` |
| returnDate | — | string (date-time, ISO 8601) | да | — | `return_records.return_date` |
| condition | — | string (enum) | да | `working`, `needs_repair`, `lost` | `return_records.condition` |

Если обязательное поле не передано или не проходит проверку — ответ 400 с перечнем полей в `errors`. Лишние поля отклоняются (`additionalProperties: false`).

### Пример запроса

```http
POST /api/v1/return-records HTTP/1.1
Host: localhost:8080
Authorization: Bearer <token>
Content-Type: application/json

{
  "issueRecordId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "toolId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "employeeId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "returnDate": "2026-10-30T14:20:00+03:00",
  "condition": "working"
}
```

### Кому разрешено вызывать метод

- Кладовщик (`warehouse_keeper`)

Остальным ролям — ответ 403.

## 3. Выходные данные (Исходящие параметры)

### Успешная обработка запроса

| HTTP Code | Описание | Возвращаемая информация |
| --- | --- | --- |
| 201 | Ресурс создан | object |

#### Модель данных тела ответа

Формат: объект.

| Поле | Описание | Тип данных | Обязательность | Поле БД / ограничения |
| --- | --- | --- | --- | --- |
| id | — | string (uuid) | да | `return_records.id`; — |
| issueRecordId | — | string (uuid) | да | `return_records.issue_record_id`; — |
| toolId | — | string (uuid) | да | `return_records.tool_id`; — |
| employeeId | — | string (uuid) | да | `return_records.employee_id`; — |
| returnDate | — | string (date-time, ISO 8601) | да | `return_records.return_date`; — |
| condition | — | string (enum) | да | `return_records.condition`; `working`, `needs_repair`, `lost` |
| createdAt | время создания | string (date-time, ISO 8601) | да | `return_records.created_at`; — |
| updatedAt | время изменения | string (date-time, ISO 8601) | да | `return_records.updated_at`; — |

Необязательное поле без значения возвращается как `null`; пустой список — `[]`.

#### Пример успешного ответа

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "issueRecordId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "toolId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "employeeId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "returnDate": "2026-10-30T14:20:00+03:00",
  "condition": "working",
  "createdAt": "2026-10-30T14:20:00+03:00",
  "updatedAt": "2026-10-30T14:20:00+03:00"
}
```

### Неуспешная обработка запроса

| HTTP Code | Описание |
| --- | --- |
| 400 | Ошибка валидации запроса |
| 401 | Нет или истёк токен доступа |
| 403 | Недостаточно прав для операции |
| 404 | Ресурс не найден: Запись о выдаче не найдена; Инструмент не найден; Сотрудник не найден |
| 409 | Конфликт состояния ресурса: Запись о выдаче уже возвращена |
| 422 | Нарушено бизнес-правило: Некорректные входные данные |
| 429 | Превышен лимит запросов |
| 500 | Внутренняя ошибка |

#### Модель данных тела ответа (application/problem+json, RFC 9457)

| Поле | Описание | Тип данных | Обязательность |
| --- | --- | --- | --- |
| type | URI типа ошибки | string | да |
| title | краткое описание для пользователя | string | да |
| status | HTTP-код | integer | да |
| detail | подробности | string | нет |
| code | машиночитаемый код бизнес-ошибки | string | нет |
| traceId | идентификатор для поиска в логах | string | нет |
| errors | ошибки валидации: `[{field, message}]` | array | нет |

#### Пример неуспешного ответа

```json
{
  "type": "/errors/404",
  "title": "Ресурс не найден",
  "status": 404,
  "detail": "Запись о выдаче не найдена",
  "traceId": "00-4bf92f3577b34da6a3ce929d0e0e4736-01"
}
```

## 4. Описание алгоритма работы метода

**Основной сценарий:**

1. Проверить права роли warehouse_keeper
2. Валидировать входные данные: issueRecordId, toolId, employeeId, returnDate, condition
3. Проверить существование записи о выдаче
4. Проверить, что запись о выдаче в статусе active или overdue
5. Создать запись ReturnRecord
6. Обновить статус IssueRecord на returned
7. Обновить статус Tool в зависимости от condition: working → available, needs_repair → repair, lost → lost
8. Зафиксировать событие returned в MovementHistory
9. Зафиксировать действие в AuditLog
10. Опубликовать событие tool.returned в Kafka
11. Вернуть созданную запись ReturnRecord

**Сценарий исключений:**

- Запись о выдаче не найдена → 404
- Инструмент не найден → 404
- Сотрудник не найден → 404
- Запись о выдаче уже возвращена → 409
- Некорректные входные данные → 422

**Проверки и валидации:**

- `condition`: `working`, `needs_repair`, `lost`, обязательно

**Кэширование:** не применяется.

**Производительность:** ограничение частоты запросов, превышение — 429 и `Retry-After`; таймаут обработки 30 с.

## 5. Взаимодействие с внешними компонентами. Источники данных

Внешние компоненты: Kafka Broker.

### Взаимодействие с таблицами

| Наименование таблицы | Описание таблицы |
| --- | --- |
| `return_records` | Запись о возврате инструмента с оценкой состояния |
| `issue_records` | Запись о выдаче инструмента сотруднику |
| `tools` | Единица инструмента или оборудования в каталоге |
| `employees` | Сотрудник, получающий инструмент; данные загружаются из CSV-файла |
| `movement_histories` | История движения каждой единицы инструмента |
| `audit_logs` | Журнал аудита действий пользователей |
