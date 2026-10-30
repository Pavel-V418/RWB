# Realtime-хаб SignalR (backend → web)

| Параметр | Значение |
|---|---|
| URL | `/hubs/events` (за тем же origin, что `/api`) |
| Аутентификация | JWT в query `access_token` (стандарт SignalR для WebSocket) |
| Транспорт | WebSocket, fallback на SSE/long polling |
| Протокол | JSON |

## Методы

| Направление | Метод | Аргументы | Назначение |
|---|---|---|---|
| сервер → клиент | `event` | `EventEnvelope` (`event-envelope.schema.json`) | Каждое доменное событие |
| клиент → сервер | `Subscribe` | `string[] types` | Подписка на типы событий (группы SignalR по `type`); `["*"]` — все |
| клиент → сервер | `Unsubscribe` | `string[] types` | Отписка |

## Правила
- Backend публикует в хаб то же событие, что ушло в Kafka (консьюмер Kafka → `IHubContext`).
- Порядок внутри одного `subject.id` сохраняется; между разными субъектами не гарантирован.
- Клиент дедуплицирует по `id` (последние 500 ID).
- После переподключения клиент перечитывает экран через REST (события за время разрыва не досылаются).
- В dev-режиме вместо хаба работает `MockRealtimeService`, проигрывающий `fixtures/<case>/events.json`.

## Поля payload (важно для backend)
Веб-клиент обновляет экраны без перезапроса по двум правилам — backend должен их соблюдать:
1. **patch**: если на экране есть объект с `id == subject.id`, поля `payload` сливаются в него.
   Поэтому поля состояния в payload называются **так же, как в REST-ресурсе** сущности `subject.entity`
   (например `lastPosition`, `status`, `currentZoneNumber` у ресурса из `GET /<things>/{id}`).
2. **prepend** в журнал: строка журнала = `{ id, type, occurredAt, ...payload }`, поэтому payload содержит
   поля записи журнала из REST (`message`, `severity`, ссылочные `…Id` и подписи).
Если payload не соответствует ресурсу, клиент просто перечитает экран через REST (без ошибок).
