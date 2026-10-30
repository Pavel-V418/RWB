# Данные кейса № 1

## spb.json — парковочные зоны Санкт-Петербурга

Выгрузка API зон платной парковки (`{"result": "Success", "message": "", "data": {...}}`),
5,4 МБ. Факты о содержимом (без интерпретации):

- `data.city` — `"spb"`; `data.zones` — 6 867 зон, у всех `entityState = "Active"`.
- Поля зоны: `externalZoneId` (строка), `displayName` (адрес, например «Введенская ул. д.22»),
  `geometry`, `price`, `priceDescription`, `minTime`, `timeStep`, `tariff`, `entityState`,
  `initDate`, `lastSyncedAt`.
- `geometry` — WKT в WGS 84 (долгота, широта): `LINESTRING` — 6 563 зоны, `MULTILINESTRING` —
  299, `GEOMETRYCOLLECTION` — 5. Полигонов нет: зона описана линией вдоль улицы.
- `price` — целое число от 10 020 до 36 000; `priceDescription` для тех же зон показывает
  «280-280,2 ₽/час» при `price = 28020`.
- `minTime` — 60 у всех зон, `timeStep` — 15 у всех зон.
- `tariff.hasHolidays` — флаг; `tariff.periods[]` — `from`, `to` (например 480 и 1200 при
  описании «08:00-20:00»), `duration` (60 или `null`), `price`, `type` — `car`, `truck` или `bus`.
