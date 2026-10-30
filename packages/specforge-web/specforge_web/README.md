# SpecForge — web/UX-часть конвейера (участник 4)

Веб-ветка конвейера: требования + OpenAPI → спека экранов → Angular-админка → UI-ревью → автоисправления.
LLM пишет только спеку экранов (UX-агент) и нестандартные блоки (frontend-агент); всё остальное — код без токенов.
На любом шаге, где LLM не справилась, срабатывает детерминированная страховка — сборка остаётся зелёной.

**Для команды:** как всё устроено, какой файл за что отвечает и как встроить в общий проект — `docs/participant-4-integration.md`.

```
requirements + openapi
   │
   ├─► baseline (эвристики по OpenAPI, 0 токенов) ──────────────┐ черновик и страховка
   └─► UX-агент: план (thinking on) → экраны ∥ (guided JSON) → [шлюз UX] → исправление ≤2 → baseline-экран
                                                                    │ screen-spec.web.json / .android.json
                                     генератор (0 токенов) ◄────────┘
                                         │ Angular-проект + slots.json
                         frontend-агент: слоты (LLM) → [kit-lint · template-check · ts-check] ≤3 → откат к заглушке
                                         │ [ng build · jest]
                         VLM-ревьюер: экраны × 5 состояний × 2 вьюпорта → DOM-проверки + VLM на ключевых кадрах
                                         │ defects.json
                         autofix: правки спеки (hideBelow…) + замечания слотам → повтор (≤2 круга)
```

## Агенты

| Агент | Модуль | LLM | Шлюз | Страховка |
|---|---|---|---|---|
| UX-проектировщик | `agents/ux_designer/agent.py` | план (+ глоссарий русских подписей) — thinking on, экраны — off, guided JSON | `gate.py` (схема, OpenAPI, FR, роли) | baseline-экран / вся baseline-спека, с русскими подписями |
| Frontend-разработчик | `agents/frontend/agent.py` | только слоты, thinking off | kit-lint, template-check, ts-check, ng build, jest | заглушка слота; при падении ng build — откат слотов и пересборка |
| VLM-ревьюер UI | `agents/vlm_reviewer/review.py` | Qwen3.8 VL на 3 кадрах экрана | нет critical/major | DOM-проверки без LLM |
| Вся веб-ветка | `agents/ui_pipeline.py` | — | все шлюзы выше | — |

## Состав

| Путь | Что это |
|---|---|
| `contracts/ux/screen-spec.schema.json` | Формат спеки экранов (web и Android), 9 архетипов, 5 обязательных состояний |
| `contracts/api/api-conventions.md` | Соглашения OpenAPI: `operationId`, пагинация, RFC 7807, `Idempotency-Key`, `x-roles` |
| `contracts/realtime/` | Конверт события, хаб SignalR, правило полей payload |
| `contracts/console/console-api.yaml` | API веб-консоли конвейера (с участником 1) |
| `contracts/agent/agent_contract.py` | `run(input_dir, output_dir, llm) → GateResult` |
| `design-system/primeng/` | Манифест UI Kit (40 компонентов), правила для промптов, токены, пресет, раскладка |
| `design-system/material3/` | Минимальный манифест для Android-спеки (владелец — участник 5) |
| `agents/ux_designer/gate.py` | Шлюз UX: схема + сверка с OpenAPI и требованиями + подсказки ремонтнику |
| `scaffolds/angular-admin/` | Золотой каркас Angular 20.1.6 + PrimeNG 20 (домен-независимый) |
| `generators/angular/` | Генератор: OpenAPI → модели и `ApiService`; спека → страницы, маршруты, меню, тесты, витрина UI Kit |
| `tools/kit_lint.py` | Строгое соответствие UI Kit (сырые элементы, устаревшие/выдуманные компоненты, цвета) |
| `tools/template_check.py` | Баланс тегов и блоков, неизвестные имена в шаблонах — за миллисекунды |
| `tools/ts_check.py` | Проверка типов без `node_modules` (типовые заглушки Angular/rxjs) — секунды |
| `agents/common/llm.py` | Клиент OpenAI-совместимого эндпоинта (vLLM/Qwen): guided JSON, thinking on/off, учёт токенов и кэша |
| `agents/common/api_digest.py` | Компактный дайджест OpenAPI для промптов (≈ в 5 раз меньше токенов, чем YAML) |
| `agents/ux_designer/baseline.py` | Спека экранов из OpenAPI эвристиками — проходит шлюз на любом API |
| `agents/ux_designer/trace.py` | Привязка требований к экранам по смыслу + честная уверенность (`trace.<platform>.json`, предупреждение `UX_FR_WEAK`) |
| `agents/ux_designer/flows.py` | Пользовательские потоки по ролям: основные и альтернативные сценарии, схема Mermaid → `03_ux/user-flows.<platform>.md` |
| `generators/angular/scenarios.py`, `samples.py` | E2E-сценарии пользователя из спеки (`e2e/scenarios.spec.ts`) и тестовые значения полей по `pattern`/примерам OpenAPI |
| `agents/ux_designer/labels.py` | Русские подписи без LLM: `title`/`x-label`/`description`/`summary`/теги/`x-enum-labels` из OpenAPI → глоссарий модели → словарь типовых полей и статусов |
| `agents/vlm_reviewer/capture.py` | Playwright: форсирование 5 состояний перехватом API, DOM-проверки |
| `agents/vlm_reviewer/autofix.py` | Дефекты → правки спеки и замечания слотам |
| `tools/mock_api.py`, `tools/spa_server.py` | Mock-API из OpenAPI (без Prism): разные значения в строках, геометрия вокруг точки-примера из спеки; сервер собранной админки |
| `agents/nodes.py` | Узлы веб-ветки для графа LangGraph участника 1 (`register(graph)`), не бросают исключений |
| `scripts/rehearse.py` | Репетиция кейса №2: прогон всех кейсов `fixtures/` (с LLM или без) с таблицей результатов |
| `tools/ui_docs.py` | Руководство пользователя, каталог экранов, схема навигации и трассируемость FR → экраны → тесты — из спеки |
| `tools/contract_diff.py` | Совместим ли интерфейс с реально поднятым backend (Swagger URL/файл) + изменения операций |
| `tools/prompt_budget.py` | Сухой прогон: сколько вызовов и токенов уйдёт в модель, доля кэшируемого префикса |
| `docs/web-branch.md` | Архитектура веб-ветки, ADR, метрики, сценарий видео, ограничения |
| `console/` | Веб-консоль конвейера: спека + слоты (живой граф, метрики, запуск, просмотр артефактов), собирается нашим генератором; `demo_server.py` — имитация оркестратора по `console-api.yaml`, прогоны помечены «Демо-режим» |
| `fixtures/case1/` | Эталон кейса №1: требования*, OpenAPI*, спека web (13 экранов) и Android (6), mock-события |
| `fixtures/synthetic-repair/` | Синтетический кейс другой предметной области — репетиция кейса №2 |

\* временные эталоны: заменяются артефактами участников 2 и 3 того же формата.

## Команды

```bash
# шлюз UX
python3 -m agents.ux_designer.gate --spec fixtures/case1/03_ux/screen-spec.web.json \
  --openapi fixtures/case1/02_system/openapi.yaml --requirements fixtures/case1/01_requirements/requirements.json

# генерация админки
python3 -m generators.angular.generate --spec fixtures/case1/03_ux/screen-spec.web.json \
  --openapi fixtures/case1/02_system/openapi.yaml --events fixtures/case1/03_ux/mock-events.json --out out/admin

# быстрые проверки (без npm)
python3 tools/kit_lint.py out/admin/src && python3 tools/template_check.py out/admin/src && python3 tools/ts_check.py out/admin

# полный шлюз (нужен npm)
cd out/admin && npm install && npm run gate          # kit-lint + ng build + jest
npm run start:mock                                     # Prism + mock-события, http://localhost:4200

# агенты (LLM: SPECFORGE_LLM_URL, SPECFORGE_LLM_MODEL, SPECFORGE_LLM_KEY; --offline — без LLM)
python3 -m agents.ux_designer.agent --in artifacts --out artifacts/03_ux
python3 -m agents.frontend.agent --in artifacts --out artifacts/05_web --build
python3 -m agents.vlm_reviewer.review --in artifacts --out artifacts/07_tests/ui --base-url http://localhost:4200
python3 -m agents.ui_pipeline --in artifacts --build --review          # вся веб-ветка одной командой

# собранная админка с mock-API (без backend и Prism)
python3 tools/spa_server.py artifacts/05_web/admin/dist/app/browser --port 4200 --mock artifacts/02_system/openapi.yaml

# веб-консоль конвейера
python3 -m console.build --out console/app && cd console/app && npm install && npm run build && cd ../..
python3 -m console.demo_server --seed            # http://127.0.0.1:8765 — имитация прогона с SSE («Демо-режим»)
python3 -m console.demo_server --seed --artifacts out/rehearsal/synthetic-booking   # + просмотр настоящих файлов артефактов

# репетиция на всех кейсах, документация, сверка с backend, бюджет токенов
python3 -m scripts.rehearse [--llm] [--build]
python3 tools/ui_docs.py --in artifacts --out artifacts/09_docs/ui --project artifacts/05_web/admin --shots artifacts/07_tests/ui/screenshots
python3 tools/contract_diff.py --spec artifacts/03_ux/screen-spec.web.json --api http://localhost:8080/swagger/v1/swagger.json --was artifacts/02_system/openapi.yaml
python3 tools/prompt_budget.py --in fixtures/case1

# e2e-сценарии пользователя против поднятого стенда (вход — учётные записи по ролям)
cd artifacts/05_web/admin && npx playwright install chromium
BASE_URL=http://localhost:8080 E2E_USERS='{"<роль>": {"login": "...", "password": "..."}}' npx playwright test e2e/scenarios.spec.ts
# локально против mock (данные созданного запоминаются в памяти mock): BASE_URL=http://127.0.0.1:4200 npx playwright test

# тесты конвейера
python3 -m pytest -q tests
```

## Встраивание в граф (участник 1)

```python
from agents.nodes import register, merge_stages
# State: TypedDict с artifacts, llm, build, ui_base_url, stages: Annotated[dict, merge_stages]
register(graph, after="02_system")   # 03_ux → 05_web → 07_ui → (повтор 05_web один раз) → 09_docs_ui
```

## Соглашения для соседей

- **Участник 3 (backend):** имена `operationId` и формат ответов — по `api-conventions.md`; payload событий — по правилу в `contracts/realtime/signalr-hub.md`. TS-клиент генерирую я, от тебя нужен только Kotlin-клиент для участника 5.
- **Участник 5 (Android, QA):** `fixtures/case1/03_ux/screen-spec.android.json` + `design-system/material3/`. Тестовые id в вебе: `<screen>`, `<screen>-state-<loading|empty|error|forbidden>`, `<screen>-action-<id>`, `<screen>-field-<name>`, `<screen>-submit`, `<screen>-filter-<field>`, `<screen>-table-row`, `<screen>-widget-<id>`, `<screen>-map`.
- **Участник 1 (оркестратор):** шлюз UX уже возвращает `GateResult`; frontend-агент читает `slots.json`.

## Статус проверок

| Проверка | Статус |
|---|---|
| Шлюз UX на эталоне web/Android и синтетике | ✅ покрытие FR 100% |
| Шлюз ловит 22 типа ошибок спеки | ✅ |
| kit-lint / template-check / ts-check на двух сгенерированных проектах | ✅ 0 ошибок |
| Отсутствие доменных слов в коде конвейера | ✅ (тест) |
| UX-агент: штатный путь, исправление экрана, мусор от LLM → baseline, сломанный экран → baseline | ✅ (тесты с ReplayLLM) |
| Baseline-спека проходит шлюз и генерируется без ошибок на обоих кейсах | ✅ |
| Frontend-агент: слот с первой попытки, исправление по kit-lint и по типам, откат к заглушке | ✅ |
| VLM-ревьюер на настоящем Chromium: 5 состояний × 2 вьюпорта, переполнение, кнопки без подписи | ✅ |
| Консоль: сборка генератором без ошибок, демо-сервер (approve, SSE, метрики) | ✅ |
| Репетиция: 5 кейсов из разных доменов проходят веб-ветку без LLM, покрытие FR 100% | ✅ |
| Узлы графа, сверка контракта с backend, документация из спеки | ✅ |
| Откат слотов LLM при падении `ng build` (виновные → все), честная ошибка, если виноват не слот | ✅ (тесты) |
| Baseline на русском: аннотации OpenAPI, глоссарий из плана модели, словарь типовых полей | ✅ (тесты) |
| Ответы text/* и файлы в клиенте API (`responseType`), mock-API с разными данными и геометрией | ✅ (тесты) |
| Ревьюер: без ложных срабатываний от форсированных ошибок, хаба и экранов без данных | ✅ (тесты + прогон в Chromium) |
| Привязка FR по смыслу, неуверенные привязки видны («не уверено — проверить»), а не прячутся в 100% | ✅ (тесты) |
| E2E-сценарии генерируются для форм, действий и ролей; типы e2e проверяет ts-check | ✅ (тесты) — запуск против стенда: участники 3/5 |
| Ревьюер: пустая отправка и ошибка сохранения формы, доступность axe-core под CSP | ✅ (тесты в Chromium) |
| CSP сайта: 0 нарушений на обзоре, карте, списке, форме, ленте | ✅ (Chromium) |
| `ng build`, `jest`, Playwright по собранной админке синтетических кейсов и консоли | ⏳ на ноутбуке: `python -m scripts.rehearse --build` |
