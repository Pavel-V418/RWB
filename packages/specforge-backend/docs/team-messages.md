# Сообщения участникам (31.10, ночь)

## Участнику 1 (оркестратор)

Моя часть готова к подключению в граф. Каждый шаг — CLI или Python-функция, у каждого есть `gate.json` вида
`{gate, passed, errors: [{file, line, message}]}` (вход для ремонтника):

| Шаг | Команда | Шлюз |
|---|---|---|
| генераторы (без LLM) | `generators/run-all.sh <artifacts> --verify` | `04_backend/gate.json`, `08_infra/gate.json`, `06_android/gate.json` |
| backend-агент | `python -m agents.backend.agent <artifacts> [--no-tests]` или `run(input_dir, output_dir, llm) -> GateResult` | `04_backend/backend-agent.json` |
| DevOps-агент | `python -m agents.devops.agent <artifacts> [--seed DIR]` или `run(...)` | `08_infra/gate.json` (gate: stand) |
| ремонт по e2e | `python -m agents.backend.agent <artifacts> --feedback <файл> --tags X` | тот же отчёт |

Ключ LLM — переменная `SPECFORGE_LLM_API_KEY` (настройки в `~/.specforge.env`), клиент сам держит лимит 30k токенов/мин.
Вопрос: ты вызываешь шаги через CLI или импортом `run()`? И в каком каталоге будут артефакты прогона (имя каталога над
`artifacts` становится именем стенда `specforge-<имя>`)?

## Участнику 2 (аналитика и документация)

1. Прогнал свои генераторы и backend-агента на твоём кейсе 3 (`runs/case3_tool_issuing`). Нашёл в контракте две вещи,
   которые стоит поправить в архитекторе (генератор я уже сделал к ним устойчивым):
   - у `GET /api/v1/tools` (и других списков) параметры `page`/`pageSize` объявлены дважды — inline и через `$ref`.
     По OpenAPI пара (name, in) должна быть уникальной; хорошо бы ловить это шлюзом;
   - `Idempotency-Key` стоит у GET-операций — для чтения он не нужен;
   - в кейсе 3 нет операции входа (`POST /auth/tokens` или аналог): клиентам негде получить JWT.
2. Матрица трассируемости: backend-агент в конце прогона заполняет столбец `tests` в `02_system/traceability.csv`
   (остальные столбцы не трогаю) и пишет `04_backend/traceability.md` — требование → API → файл кода → тесты.
   FR операции беру из `x-requirements` — пожалуйста, оставь его в контракте.
3. Паспорт сервиса и ADR: `08_infra/services/backend-api.md`, `08_infra/adr/*.md` — подключи в MkDocs.
4. Формат ошибок валидации — словарь `errors: {"Points[0].RecordedAt": ["…"]}` (стандарт ASP.NET), а не массив
   `[{field, message}]`. Предлагаю поправить описания методов под словарь.
5. Структуру `acceptance` в `requirements.json` (`id`, `fr_id`, `given/when/then`) не меняй — по ней агент пишет тесты.

## Участнику 4 (web)

- Формат ошибок валидации — словарь `errors` (см. выше).
- Образ web собирается в CI, как только появится `05_web/Dockerfile` (`WEB_IMAGE=specforge/web:<sha>`), сервис compose
  по профилю `web`; nginx ходит в `http://backend:8080`, имя сервиса не меняется.
- Стенд теперь называется по кейсу (`specforge-case1`), если ты ссылался на имена контейнеров — используй имя сервиса.
- Всё ещё жду `api-conventions.md` и `signalr-hub.md`; пагинация у нас `{items, meta: {page, pageSize, total}}` —
  договоримся, если тебе нужно иначе.

## Участнику 5 (Android/QA)

- Kotlin-клиент кладу как договаривались: `06_android/app/src/main/kotlin/<appId>/core/network/generated`,
  пакет `<appId>.core.network.generated` (кейс 1 — `com.specforge.case1`); это совпадает с твоим каркасом.
- Стенд на vm5 переименован в `specforge-case1` (deploy.sh останавливает другие стенды specforge — порты общие).
  Доступ — туннель `ssh -p 10005 -L 8080:127.0.0.1:8080 team5@85.143.113.230`. e2e 26/26 проходит.
- Правило смены зоны и 400 на точку из будущего уже на стенде; формат ошибок валидации — словарь `errors`.
- CI: подключаю твои `ci/tests.gitlab-ci.yml` и `ci/security.gitlab-ci.yml` через `include: local`, как только они появятся
  в репозитории; package идёт до security, образ для Trivy — `$API_IMAGE` / `$WEB_IMAGE`.
