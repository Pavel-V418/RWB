# Матрица трассируемости

Каждое требование связано с критериями приёмки, сценариями, операциями API, событиями, экранами и тестами.

| Показатель | Значение |
| --- | --- |
| FR всего | 21 |
| FR с API или событием | 100.0% |
| FR с экранами | ожидается спецификация экранов |
| FR с тестами | ожидается отчёт о тестировании |
| FR подтверждены тестами | ожидается отчёт о тестировании |

| FR | Требование | Критерии | Сценарии | API | События | Экраны | Тесты | Статус |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FR-1 | Определение местоположения автомобиля | 3 | UC_001, UC_002 | POST /api/v1/vehicles/{vehicleId}/positions | — | — | — | спроектировано |
| FR-2 | Определение нахождения в платной зоне | 2 | UC_001, UC_002 | POST /api/v1/vehicles/{vehicleId}/positions | parking.zone.entered | — | — | спроектировано |
| FR-3 | Отображение номера и границ зоны | 2 | UC_002, UC_007 | GET /api/v1/me/status<br>GET /api/v1/zones<br>GET /api/v1/zones/{zoneId} | — | — | — | спроектировано |
| FR-4 | Отображение параметров парковки | 2 | UC_002 | GET /api/v1/zones<br>GET /api/v1/zones/{zoneId} | — | — | — | спроектировано |
| FR-5 | Хранение активной парковочной сессии | 2 | UC_003 | POST /api/v1/sessions<br>GET /api/v1/vehicles/{vehicleId}/sessions/active<br>PATCH /api/v1/sessions/{sessionId} | parking.session.closed | — | — | спроектировано |
| FR-6 | Отслеживание перемещения | 2 | UC_005 | POST /api/v1/vehicles/{vehicleId}/positions<br>GET /api/v1/vehicles/{vehicleId}/route | — | — | — | спроектировано |
| FR-7 | Определение выхода из зоны | 2 | UC_001, UC_005 | POST /api/v1/vehicles/{vehicleId}/positions<br>GET /api/v1/me/notifications | parking.zone.exited | — | — | спроектировано |
| FR-8 | Определение перехода в другую зону | 2 | UC_001, UC_005 | POST /api/v1/vehicles/{vehicleId}/positions<br>GET /api/v1/me/notifications | parking.zone.changed | — | — | спроектировано |
| FR-9 | Контроль окончания оплаченной сессии | 3 | UC_001, UC_004 | GET /api/v1/me/status<br>GET /api/v1/me/notifications | parking.session.expiring, parking.session.expired | — | — | спроектировано |
| FR-10 | Предупреждение о проверке или новой оплате | 2 | UC_001, UC_004, UC_005 | POST /api/v1/sessions/{sessionId}/extensions<br>GET /api/v1/me/notifications | parking.zone.changed, parking.session.expired | — | — | спроектировано |
| FR-11 | Переход к официальному сервису оплаты | 3 | UC_004 | GET /api/v1/zones/{zoneId}/payment-link | — | — | — | спроектировано |
| FR-12 | Маршрут и история парковочных сессий | 2 | UC_006, UC_007 | GET /api/v1/vehicles/{vehicleId}/route<br>GET /api/v1/sessions | — | — | — | спроектировано |
| FR-13 | Уведомление о въезде в платную зону | 2 | UC_001, UC_002 | POST /api/v1/vehicles/{vehicleId}/positions<br>GET /api/v1/me/notifications | parking.zone.entered | — | — | спроектировано |
| FR-14 | Отображение времени окончания оплаты | 3 | UC_001, UC_003 | GET /api/v1/me/status<br>POST /api/v1/sessions<br>POST /api/v1/sessions/{sessionId}/extensions | parking.session.extended | — | — | спроектировано |
| FR-15 | Административный просмотр данных | 3 | UC_001, UC_007 | POST /api/v1/auth/tokens<br>GET /api/v1/vehicles/{vehicleId}/route<br>GET /api/v1/vehicles<br>GET /api/v1/zones<br>GET /api/v1/sessions<br>GET /api/v1/events | — | — | — | спроектировано |
| FR-16 | Главный экран мобильного приложения | 2 | UC_004 | POST /api/v1/auth/tokens<br>GET /api/v1/me/status<br>PATCH /api/v1/me/notifications/{notificationId} | — | — | — | спроектировано |
| FR-17 | Адаптер поставщика парковочных услуг | 3 | UC_003 | POST /api/v1/sessions | — | — | — | спроектировано |
| FR-18 | Демонстрационный маршрут | 2 | UC_001, UC_008 | GET /api/v1/demo/routes<br>POST /api/v1/demo/routes/{routeId}/runs | — | — | — | спроектировано |
| FR-19 | Запуск и подтверждение сессии | 4 | UC_001, UC_003 | POST /api/v1/sessions | parking.session.started | — | — | спроектировано |
| FR-20 | Журнал событий | 2 | UC_001, UC_007 | GET /api/v1/events | parking.zone.entered, parking.zone.exited, parking.zone.changed, parking.session.started, parking.session.extended, parking.session.expiring, parking.session.expired, parking.session.closed | — | — | спроектировано |
| FR-21 | Загрузка геоданных парковочных зон | 2 | UC_008 | POST /api/v1/zone-imports | — | — | — | спроектировано |
