# API

Спецификации: [OpenAPI 3.1](contracts/openapi.yaml), [AsyncAPI 3.0](contracts/asyncapi.yaml).

- базовый путь `/api/v1`, аутентификация `Authorization: Bearer <JWT>`;
- ошибки — `application/problem+json` (RFC 9457) с `traceId`;
- критичные операции — заголовок `Idempotency-Key`; превышение лимита — 429 и `Retry-After`;
- списки — `page`, `pageSize`, ответ `{items, page, pageSize, total}`.

## Auth

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `POST /api/v1/auth/tokens` | Выдать JWT по логину и паролю | anonymous | FR-15, FR-16 |

## Me

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `GET /api/v1/me/status` | Состояние для главного экрана водителя: зона, сессия, оставшееся время | driver | FR-3, FR-9, FR-14, FR-16 |
| `GET /api/v1/me/notifications` | Уведомления водителя | driver | FR-7, FR-8, FR-9, FR-10, FR-13 |
| `PATCH /api/v1/me/notifications/{notificationId}` | Отметить уведомление прочитанным | driver | FR-16 |

## Positions

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `POST /api/v1/vehicles/{vehicleId}/positions` | Передать координату автомобиля; ответ содержит определённую зону и переход | driver | FR-1, FR-2, FR-6, FR-7, FR-8, FR-13 |
| `GET /api/v1/vehicles/{vehicleId}/route` | Маршрут автомобиля за период | driver, dispatcher | FR-6, FR-12, FR-15 |

## Vehicles

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `GET /api/v1/vehicles` | Список автомобилей с текущей зоной | dispatcher | FR-15 |

## Zones

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `GET /api/v1/zones` | Парковочные зоны с границами и параметрами | driver, dispatcher | FR-3, FR-4, FR-15 |
| `GET /api/v1/zones/{zoneId}` | Карточка зоны | driver, dispatcher | FR-3, FR-4 |
| `GET /api/v1/zones/{zoneId}/payment-link` | Ссылки на официальный сервис оплаты с номером зоны | driver | FR-11 |
| `POST /api/v1/zone-imports` | Загрузить зоны из GeoJSON (снимок открытых данных или демо-набор) | dispatcher | FR-21 |

## Sessions

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `POST /api/v1/sessions` | Запустить новую сессию или подтвердить оплаченную | driver | FR-5, FR-14, FR-17, FR-19 |
| `GET /api/v1/vehicles/{vehicleId}/sessions/active` | Активная сессия автомобиля | driver, dispatcher | FR-5 |
| `POST /api/v1/sessions/{sessionId}/extensions` | Подтвердить продление оплаты | driver | FR-10, FR-14 |
| `PATCH /api/v1/sessions/{sessionId}` | Завершить сессию (status = closed) | driver | FR-5 |
| `GET /api/v1/sessions` | История парковочных сессий | driver, dispatcher | FR-12, FR-15 |

## Events

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `GET /api/v1/events` | Журнал событий | dispatcher | FR-15, FR-20 |

## Demo

| Метод и путь | Назначение | Роли | Требования |
| --- | --- | --- | --- |
| `GET /api/v1/demo/routes` | Демонстрационные маршруты | dispatcher | FR-18 |
| `POST /api/v1/demo/routes/{routeId}/runs` | Запустить проигрывание маршрута для автомобиля | dispatcher | FR-18 |

## События

| Событие | Топик | Производитель | Потребители |
| --- | --- | --- | --- |
| `parking.zone.entered` | `parking.zone.entered.v1` | Backend API | Background Worker, Backend API |
| `parking.zone.exited` | `parking.zone.exited.v1` | Backend API | Background Worker, Backend API |
| `parking.zone.changed` | `parking.zone.changed.v1` | Backend API | Background Worker, Backend API |
| `parking.session.started` | `parking.session.started.v1` | Backend API | Background Worker, Backend API |
| `parking.session.extended` | `parking.session.extended.v1` | Backend API | Background Worker, Backend API |
| `parking.session.expiring` | `parking.session.expiring.v1` | Background Worker | Background Worker, Backend API |
| `parking.session.expired` | `parking.session.expired.v1` | Background Worker | Background Worker, Backend API |
| `parking.session.closed` | `parking.session.closed.v1` | Backend API | Backend API |
