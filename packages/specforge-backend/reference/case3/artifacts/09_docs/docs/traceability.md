# Матрица трассируемости

Каждое требование связано с критериями приёмки, сценариями, операциями API, событиями, экранами и тестами.

| Показатель | Значение |
| --- | --- |
| FR всего | 17 |
| FR с API или событием | 94.1% |
| FR с экранами | ожидается спецификация экранов |
| FR с тестами | ожидается отчёт о тестировании |
| FR подтверждены тестами | ожидается отчёт о тестировании |

| FR | Требование | Критерии | Сценарии | API | События | Экраны | Тесты | Статус |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FR-1 | Ведение каталога инструмента | 3 | UC_001, UC_010 | POST /api/v1/tools<br>GET /api/v1/tools/{id}<br>GET /api/v1/tools | — | — | — | спроектировано |
| FR-2 | Печать QR-кода для инструмента | 1 | UC_001, UC_010 | POST /api/v1/tools | — | — | — | спроектировано |
| FR-3 | Выдача инструмента сотруднику | 3 | UC_003, UC_010 | GET /api/v1/tools/qr/{qrCode}<br>POST /api/v1/issue-records<br>GET /api/v1/employees/pass/{passNumber}<br>GET /api/v1/audit-logs<br>POST /api/v1/users<br>GET /api/v1/users | — | — | — | спроектировано |
| FR-4 | Бронирование инструмента на дату | 3 | UC_002, UC_010 | POST /api/v1/reservations<br>POST /api/v1/reservations/{id}/cancellations<br>GET /api/v1/reservations<br>GET /api/v1/audit-logs<br>POST /api/v1/users<br>GET /api/v1/users | — | — | — | спроектировано |
| FR-5 | Запрет выдачи забронированного инструмента | 1 | UC_003, UC_010 | POST /api/v1/issue-records | — | — | — | спроектировано |
| FR-6 | Фиксация возврата инструмента | 3 | UC_004, UC_010 | POST /api/v1/return-records<br>GET /api/v1/return-records<br>GET /api/v1/audit-logs<br>POST /api/v1/users<br>GET /api/v1/users | — | — | — | спроектировано |
| FR-7 | Контроль срока возврата | 2 | UC_005, UC_010 | GET /api/v1/issue-records<br>GET /api/v1/system-settings<br>PATCH /api/v1/system-settings/{key} | — | — | — | спроектировано |
| FR-8 | Передача событий в систему учёта ТМЦ | 2 | UC_006, UC_010 | POST /api/v1/tools/{id}/retirements | tools.tool.issued, tools.tool.returned, tools.tool.retired | — | — | спроектировано |
| FR-9 | История движения инструмента | 1 | UC_007, UC_010 | GET /api/v1/tools/{id}/movements | — | — | — | спроектировано |
| FR-10 | Работа в мобильном приложении без связи | 2 | UC_003, UC_008, UC_010 | POST /api/v1/synchronization-operations | — | — | — | спроектировано |
| FR-11 | Загрузка справочника сотрудников из файла | 3 | UC_009 | POST /api/v1/employee-imports<br>GET /api/v1/employees<br>GET /api/v1/audit-logs<br>POST /api/v1/users<br>GET /api/v1/users | — | — | — | спроектировано |
| FR-12 | Добавление инструмента руководителем | 1 | UC_001 | POST /api/v1/tools<br>GET /api/v1/audit-logs<br>POST /api/v1/users<br>GET /api/v1/users | — | — | — | спроектировано |
| FR-13 | Бронирование сотрудником | 1 | UC_002, UC_010 | POST /api/v1/reservations<br>GET /api/v1/audit-logs<br>POST /api/v1/users<br>GET /api/v1/users | — | — | — | спроектировано |
| FR-14 | Отказ в выдаче забронированного инструмента | 1 | UC_003, UC_010 | POST /api/v1/issue-records | — | — | — | спроектировано |
| FR-15 | Синхронизация операций после появления связи | 1 | UC_008, UC_010 | POST /api/v1/synchronization-operations | — | — | — | спроектировано |
| FR-16 | Показ невозвращённого инструмента при истечении срока | 2 | UC_005, UC_010 | — | — | — | — | спроектировано |
| FR-17 | Возврат инструмента с отметкой ремонта | 1 | UC_004, UC_010 | POST /api/v1/return-records | — | — | — | спроектировано |
