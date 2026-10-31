# Метод POST /api/v1/sessions

## Основная информация

| Параметр | Значение |
| --- | --- |
| Тип | POST |
| Наименование метода | `startSession` |
| Назначение метода | Запустить новую сессию или подтвердить оплаченную |
| Ссылка на Swagger | [`openapi.yaml`](../openapi.yaml), operationId `startSession` |
| Входящие атрибуты запроса | vehicleId, zoneId, startMode, durationMinutes, confirmedPaidUntil |
| Реализует требования | FR-5, FR-14, FR-17, FR-19 |

## История изменений

| Дата | Версия | Описание изменения | Задача в JIRA | Автор |
| --- | --- | --- | --- | --- |
| 2026-10-31 | 1.0 | Описание метода | — | SpecForge, агент системного анализа |

## 1. Назначение

Запустить новую сессию или подтвердить оплаченную.

Транспорт: HTTPS (TLS 1.2+), HTTP/1.1. Схема взаимодействия: синхронный запрос — ответ. Формат тела: JSON, UTF-8.

## 2. Входные данные (Входящие параметры)

### Авторизация

JWT в заголовке `Authorization: Bearer <token>`; роль пользователя берётся из claims токена.

### Заголовки

| Наименование | Описание | Тип данных | Обязательность | Пример |
| --- | --- | --- | --- | --- |
| Authorization | токен доступа | string | да | `Bearer eyJhbGciOi…` |
| Content-Type | формат тела запроса | string | да | `application/json` |
| Idempotency-Key | ключ идемпотентности: повтор с тем же ключом возвращает первый результат | string (uuid) | да | `6f1c2b7e-3d4a-4e5f-9a8b-1c2d3e4f5a6b` |
| X-Correlation-Id | сквозной идентификатор запроса; если не передан — генерируется сервером | string | нет | `req-20261030-0001` |

### Параметры path

Отсутствуют.

### Параметры query

Отсутствуют.

### Модель данных тела запроса

| Поле | Описание | Тип данных | Обязательность | Варианты значений | Поле БД |
| --- | --- | --- | --- | --- | --- |
| vehicleId | автомобиль | string (uuid) | да | — | `parking_sessions.vehicle_id` |
| zoneId | зона | string (uuid) | да | — | `parking_sessions.zone_id` |
| startMode | запущена в системе или подтверждена оплаченная | string (enum) | да | `started`, `confirmed` | `parking_sessions.start_mode` |
| durationMinutes | длительность для режима started | integer | нет | ≥ 15; ≤ 1440 | — (не хранится) |
| confirmedPaidUntil | оплачено до, для режима confirmed | string (date-time, ISO 8601) | нет | — | — (не хранится) |

Если обязательное поле не передано или не проходит проверку — ответ 400 с перечнем полей в `errors`. Лишние поля отклоняются (`additionalProperties: false`).

### Пример запроса

```http
POST /api/v1/sessions HTTP/1.1
Host: localhost:8080
Authorization: Bearer <token>
Idempotency-Key: 6f1c2b7e-3d4a-4e5f-9a8b-1c2d3e4f5a6b
Content-Type: application/json

{
  "vehicleId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "zoneId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "startMode": "started",
  "durationMinutes": 60
}
```

### Кому разрешено вызывать метод

- Водитель (`driver`)

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
| id | — | string (uuid) | да | `parking_sessions.id`; — |
| vehicleId | автомобиль | string (uuid) | да | `parking_sessions.vehicle_id`; — |
| driverId | водитель (из токена) | string (uuid) | да | `parking_sessions.driver_id`; — |
| zoneId | зона | string (uuid) | да | `parking_sessions.zone_id`; — |
| startMode | запущена в системе или подтверждена оплаченная | string (enum) | да | `parking_sessions.start_mode`; `started`, `confirmed` |
| status | статус | string (enum) | да | `parking_sessions.status`; `active`, `expiring`, `expired`, `closed`; по умолчанию `active` |
| startedAt | начало | string (date-time, ISO 8601) | да | `parking_sessions.started_at`; — |
| paidUntil | оплачено до | string (date-time, ISO 8601) | да | `parking_sessions.paid_until`; — |
| endedAt | фактическое завершение | string (date-time, ISO 8601) | нет | `parking_sessions.ended_at`; — |
| providerSessionId | идентификатор сессии у поставщика | string | нет | `parking_sessions.provider_session_id`; до 100 симв. |
| createdAt | время создания | string (date-time, ISO 8601) | да | `parking_sessions.created_at`; — |
| updatedAt | время изменения | string (date-time, ISO 8601) | да | `parking_sessions.updated_at`; — |

Необязательное поле без значения возвращается как `null`; пустой список — `[]`.

#### Пример успешного ответа

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "vehicleId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "driverId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "zoneId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "startMode": "started",
  "status": "active",
  "startedAt": "2026-10-30T14:20:00+03:00",
  "paidUntil": "2026-10-30T15:20:00+03:00",
  "endedAt": null,
  "providerSessionId": null,
  "createdAt": "2026-10-30T16:20:00+03:00",
  "updatedAt": "2026-10-30T17:20:00+03:00"
}
```

### Неуспешная обработка запроса

| HTTP Code | Описание |
| --- | --- |
| 400 | Ошибка валидации запроса |
| 401 | Нет или истёк токен доступа |
| 403 | Недостаточно прав для операции |
| 409 | Конфликт состояния ресурса: Активная сессия уже есть |
| 422 | Нарушено бизнес-правило: Автомобиль не в платной зоне; confirmedPaidUntil в прошлом |
| 429 | Превышен лимит запросов |
| 500 | Внутренняя ошибка |
| 502 | Ошибка или таймаут внешнего поставщика (code: external_provider_error): Поставщик не ответил за 5 с |

#### Модель данных тела ответа (application/problem+json, RFC 9457)

| Поле | Описание | Тип данных | Обязательность |
| --- | --- | --- | --- |
| type | URI типа ошибки | string | да |
| title | краткое описание для пользователя | string | да |
| status | HTTP-код | integer | да |
| detail | подробности | string | нет |
| code | машиночитаемый код бизнес-ошибки | string | нет |
| traceId | идентификатор для поиска в логах | string | нет |
| errors | ошибки валидации: `{"поле": ["сообщение"]}` | object | нет |

#### Пример неуспешного ответа

```json
{
  "type": "https://errors.app.local/session-already-active",
  "title": "Конфликт состояния ресурса",
  "status": 409,
  "detail": "Активная сессия уже есть",
  "code": "session_already_active",
  "traceId": "00-4bf92f3577b34da6a3ce929d0e0e4736-01"
}
```

## 4. Описание алгоритма работы метода

**Основной сценарий:**

1. Проверить ключ идемпотентности: повтор возвращает первую сессию
2. Проверить, что автомобиль закреплён за водителем и текущая зона равна zoneId
3. Проверить, что в зоне нет активной сессии
4. Режим started: вызвать IParkingProvider.StartSession(зона, автомобиль, durationMinutes); режим confirmed: принять confirmedPaidUntil
5. Сохранить ParkingSession (active) и outbox parking.session.started в одной транзакции
6. Вернуть сессию

**Сценарий исключений:**

- Автомобиль не в платной зоне → 422 vehicle-not-in-paid-zone
- Активная сессия уже есть → 409 session-already-active
- confirmedPaidUntil в прошлом → 422 paid-until-in-past
- Поставщик не ответил за 5 с → 502 provider-unavailable

**Проверки и валидации:**

- `startMode`: `started`, `confirmed`, обязательно
- `durationMinutes`: ≥ 15; ≤ 1440

**Кэширование:** не применяется.

**Производительность:** таймаут обработки 30 с.

## 5. Взаимодействие с внешними компонентами. Источники данных

Внешние компоненты: Parkomatica Mock (IParkingProvider).

### Взаимодействие с таблицами

| Наименование таблицы | Описание таблицы |
| --- | --- |
| `parking_sessions` | Парковочная сессия автомобиля в зоне |
| `vehicles` | Служебный автомобиль |
| `parking_events` | Событие жизненного цикла: въезд, выход, смена зоны, этапы сессии |
