# Этап 2 — эталон событий и mock-маршрут (участник 5)

> **Обновлено после ответа участника 3:** имена событий теперь как в журнале стенда (`ZoneEntered`, `ZoneChanged`, …), правила зон — гистерезис 2 точки, выезд закрывает сессию, смена зоны в течение 300 с; `TIME_SCALE=10`. Актуальный порядок событий — в `reference/case1/mock_route/expected_events.json` и `participant-5-integration.md`. Таблица ниже — историческая.

```
tools/route_player/            ← код конвейера: универсальный, слова «parking» здесь нет
  geo_scenario.py              генератор: scenario.json → точки, GeoJSON, ожидаемые события
  play_route.py                проигрыватель: шлёт точки в API в ускоренном времени
reference/case1/               ← эталон кейса №1 (доменные имена живут только здесь)
  02_system/asyncapi.yaml      8 топиков, общая оболочка, ключ vehicleId
  mock_route/scenario.json     маршрут по Невскому, 2 соседние зоны, действия пользователя
  mock_route/points.json       1457 точек (шаг 1 с, ~24 мин сценария)
  mock_route/route.geojson     маршрут + зоны + точки ожидаемых событий (открыть на geojson.io)
  mock_route/expected_events.json  оракул e2e
```

## Сценарий кейса №1

| Время сценария | Событие | Шаг ТЗ |
|---|---|---|
| 0 с | старт у Адмиралтейства, едет по Невскому | 1 |
| 46 с | `zone.entered` (Казанский собор) | 2–3 |
| 91 с | `session.started`, оплачено 15 мин | 4 |
| 191 с | `session.extended`, +10 мин | 5 |
| 991 с | `session.expiring`, за 10 мин до конца | 6 |
| 1133 с | `zone.changed` → Гостиный двор, `requiresPaymentCheck` | 7–8 |
| 1383 с | `zone.exited` | 7 |
| — | `session.closed` — необязательное, зависит от правил backend | |

При `TIME_SCALE=60` весь сценарий идёт ~25 секунд.

## Команды

```bash
# пересобрать точки и оракул после правки сценария
python tools/route_player/geo_scenario.py reference/case1/mock_route/scenario.json

# сухой прогон
python tools/route_player/play_route.py reference/case1/mock_route/points.json --dry-run --scale 600

# на стенд (токен только из переменной окружения)
export API_TOKEN=...   STAND_URL=http://...
python tools/route_player/play_route.py reference/case1/mock_route/points.json --scale 60

# валидация AsyncAPI (на vm5, где есть npm)
npx @asyncapi/cli validate reference/case1/02_system/asyncapi.yaml
```

## Открытые TODO

- **Участник 3:** имена полей `PositionPoint` → поправить `pointFieldMap` в `scenario.json`; тело запроса (`{"points": [...]}` или массив) → `BODY_KEY` в `play_route.py`; `TIME_SCALE` для таймеров backend; тестовая машина `veh-demo-001` и учётка в сиде.
- **Участники 2 и 3:** поле нагрузки `payload` или `data` (в AsyncAPI сейчас `payload`).
- **Участник 3:** полигоны зон `Z-NEV-KAZ` и `Z-NEV-GOS` должны совпасть с сидом зон в БД — передать ему `route.geojson`.
