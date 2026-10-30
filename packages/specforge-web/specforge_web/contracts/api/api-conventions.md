# Соглашения по API (общие для участников 2, 3, 4, 5)

Эти правила соблюдает агент системного архитектора (участник 2) при генерации OpenAPI,
backend-каркас (участник 3) при реализации, а генераторы клиентов и экранов (участники 4, 5)
опираются на них без дополнительных подсказок. Правила домен-независимы.

## 1. Общее
- OpenAPI **3.1**, один файл `artifacts/02_system/openapi.yaml`.
- Базовый путь: `/api/v1`. Версия — в пути; несовместимые изменения → `/api/v2`.
- JSON, `camelCase` для полей, `kebab-case` для сегментов пути, множественное число для коллекций: `/api/v1/work-orders`.
- Идентификаторы — UUID (`format: uuid`). Время — ISO 8601 в UTC (`format: date-time`).
- Геометрия — GeoJSON (RFC 7946) в поле `geometry`: `Point`, `LineString`, `Polygon`, `MultiPolygon`. Координаты `[lon, lat]`, WGS 84.
- Каждая схема объявлена в `components/schemas`, у полей заданы `required`, `maxLength`, `pattern`, `minimum/maximum`, где это применимо.

## 2. operationId — обязателен и предсказуем

| Операция | Метод и путь | operationId |
|---|---|---|
| Список | `GET /things` | `listThings` |
| Один объект | `GET /things/{id}` | `getThing` |
| Создание | `POST /things` | `createThing` |
| Изменение | `PUT/PATCH /things/{id}` | `updateThing` |
| Удаление | `DELETE /things/{id}` | `deleteThing` |
| Вложенный список | `GET /things/{id}/parts` | `listThingParts` |
| Действие | `POST /things/{id}/start` | `startThing` |
| Справочник/сводка | `GET /stats/things` | `getThingStats` |

Тег операции = ресурс во множественном числе (`Things`). На эти operationId ссылается спека экранов,
и шлюз UX проверяет, что каждый из них существует.

## 3. Списки и пагинация
- Query-параметры: `page` (с 1), `size` (по умолчанию 20, максимум 100), `sort=field,asc|desc`, `q` — полнотекстовый поиск, остальные фильтры — по имени поля (`status=active`, `from`, `to`).
- Ответ всегда обёрнут:
  ```json
  { "items": [ ... ], "total": 134, "page": 1, "size": 20 }
  ```
  Схема называется `<Thing>Page`. Если список заведомо мал (справочник), `pagination` всё равно присутствует — единообразие важнее.

## 4. Ошибки — RFC 7807
`Content-Type: application/problem+json`, схема `Problem`:
```json
{
  "type": "https://specforge.local/errors/validation",
  "title": "Validation failed",
  "status": 400,
  "code": "VALIDATION_FAILED",
  "detail": "Поле plateNumber не соответствует формату",
  "traceId": "00-4bf92f...-01",
  "errors": { "plateNumber": ["Неверный формат"] }
}
```
Коды статусов: 400 валидация, 401 нет токена, 403 нет прав, 404 не найдено, 409 конфликт/повтор, 422 нарушено бизнес-правило, 429 лимит запросов (с `Retry-After`), 500 внутренняя.
Frontend показывает `detail` (или `title`) в toast, а `errors` раскладывает под поля формы.

## 5. Аутентификация и роли
- `Authorization: Bearer <JWT>`; `POST /api/v1/auth/login` → `{ accessToken, expiresIn, user: { id, name, roles[] } }` (`operationId: login`), `GET /api/v1/auth/me` (`getCurrentUser`).
- Роли в claim `roles`. Каждая операция в OpenAPI помечена `x-roles: [admin, ...]` — из этого генерируются атрибуты `[Authorize]` и проверки на экранах.
- Демо-учётки задаются через переменные окружения, не в репозитории.

## 6. Надёжность
- Критичные `POST` (создание, запуск/остановка процессов) принимают заголовок `Idempotency-Key` (UUID). Повтор с тем же ключом возвращает исходный ответ. В OpenAPI помечаются `x-idempotent: true`.
- Ограничение частоты: 429 + `Retry-After`; лимиты описываются в `x-rate-limit`.
- Каждый ответ содержит `X-Request-Id`; он же попадает в логи и в `traceId` ошибок.

## 7. Realtime
Сервер дублирует доменные события в браузер через SignalR (см. `contracts/realtime/`).
REST остаётся источником истины: после переподключения клиент перечитывает данные через REST.
