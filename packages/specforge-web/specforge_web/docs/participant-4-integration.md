# Веб-часть SpecForge (участник 4): как устроена и как встроить в общий проект

Документ для команды. Здесь: что делает веб-часть, как она работает, какой файл за что отвечает и что
нужно от каждого участника, чтобы собрать всё в один конвейер. Подробности решений — `docs/web-branch.md`,
все команды — `README.md`.

## 1. Что это и где место в конвейере

Конвейер: **ТЗ → анализ (участник 2) → API и модель данных (участники 2, 3) → экраны и веб-админка (участник 4)
→ backend (3) и Android (5) → тесты и безопасность (5) → сборка и деплой (3) → документация (2)**.
Управляет всем оркестратор участника 1.

Веб-часть отвечает за четыре вещи:

| Что | Вход | Выход |
|---|---|---|
| **Проектирование интерфейса** (UX-агент) | `01_requirements/requirements.json`, `02_system/openapi.yaml` | `03_ux/screen-spec.web.json` и `.android.json`, `trace.*.json`, `user-flows.*.md` |
| **Веб-админка** (генератор + frontend-агент) | спека экранов + OpenAPI | `05_web/admin/` — готовый Angular-проект с тестами и Dockerfile |
| **Проверка интерфейса** (UI-ревьюер) | собранная админка | `07_tests/ui/` — скриншоты, дефекты, отчёт `ui-review.md` |
| **Документация интерфейса** | спека, проект, скриншоты | `09_docs/ui/` — руководство пользователя, каталог экранов, трассируемость |
| **Веб-консоль конвейера** | API оркестратора (`console-api.yaml`) | сайт, где запускают конвейер и смотрят прогон |

Главный принцип: модель (Qwen) пишет только короткую спеку экранов и нестандартные блоки. Весь остальной
код делает генератор без токенов. Если модель ошиблась или недоступна, срабатывает детерминированная
страховка, и сборка остаётся зелёной.

## 2. Как это работает по шагам

1. **UX-агент** (`agents/ux_designer/agent.py`)
   - Сначала строит черновик экранов по OpenAPI без модели (`baseline.py`). Это страховка.
   - Модель составляет план экранов (thinking on), потом каждый экран по JSON-схеме (thinking off, guided JSON).
   - Шлюз (`gate.py`) проверяет: схему, что все `operationId` есть в OpenAPI, что у каждого экрана 5 состояний
     (загрузка, пусто, ошибка, нет прав, данные), что компоненты только из UI Kit, что требования покрыты.
   - Ошибки шлюза уходят модели на исправление (до 2 раз), иначе экран берётся из черновика.
   - Требования привязываются к экранам по смыслу (`trace.py`), слабые привязки помечаются «проверить вручную».
2. **Генератор** (`generators/angular/`) по спеке и OpenAPI создаёт Angular-проект на основе каркаса
   `scaffolds/angular-admin/`: клиент API, страницы, маршруты, меню, unit-тесты, e2e-тесты.
   Места, которые шаблон не покрывает, помечаются как слоты (`slots.json`).
3. **Frontend-агент** (`agents/frontend/agent.py`) заполняет слоты моделью и проверяет быстрыми шлюзами
   (kit-lint, template-check, ts-check), затем `ng build` и jest. Если сборка падает из-за слота —
   слот откатывается к заглушке и проект пересобирается.
4. **UI-ревьюер** (`agents/vlm_reviewer/`) открывает каждый экран в Chromium в 5 состояниях и на 2 ширинах
   экрана (состояния подставляются перехватом запросов к API), проверяет DOM, доступность (axe-core) и
   показывает модели 3 ключевых кадра. Дефекты превращаются в правки спеки или замечания слотам — один повтор.
5. **Документация** (`tools/ui_docs.py`) — руководство пользователя по ролям, каталог экранов, схема навигации
   и матрица «требование → экран → автотесты» (unit, smoke, пользовательский сценарий).

Каждый шаг — функция `run(input_dir, output_dir, llm) → GateResult` (контракт `contracts/agent/agent_contract.py`)
и одновременно CLI-команда. Шаги можно гонять по отдельности или все вместе: `py -m agents.ui_pipeline`.

## 3. Какой файл за что отвечает

### Контракты (общие с соседями — менять только по согласованию, новые поля только необязательные)

| Файл | Что | С кем |
|---|---|---|
| `contracts/agent/agent_contract.py` | `GateResult`, `GateError`, интерфейс `LLM` | участник 1 |
| `contracts/ux/screen-spec.schema.json` | формат спеки экранов (web и Android) | участник 5 |
| `contracts/api/api-conventions.md` | как писать OpenAPI: `operationId`, `x-roles`, `{items,total}`, вход, ошибки RFC 7807 | участники 2, 3 |
| `contracts/realtime/` | формат событий и хаб SignalR `/hubs/events` | участник 3 |
| `contracts/console/console-api.yaml` | API между консолью и оркестратором | участник 1 |

### Агенты

| Файл | Что |
|---|---|
| `agents/nodes.py` | **точка встраивания в граф участника 1**: узлы `03_ux`, `05_web`, `07_ui`, `09_docs_ui` и `register(graph)` |
| `agents/ui_pipeline.py` | вся веб-ветка одной командой (без графа) |
| `agents/common/llm.py` | клиент модели (vLLM / Qwen): guided JSON, thinking on/off, учёт токенов. Участник 1 может подменить своим |
| `agents/common/api_digest.py` | сжатое описание OpenAPI для промптов (в ~5 раз меньше токенов) |
| `agents/ux_designer/agent.py` | UX-агент |
| `agents/ux_designer/baseline.py` | черновик экранов без модели (страховка) |
| `agents/ux_designer/gate.py` | шлюз спеки экранов |
| `agents/ux_designer/trace.py` | привязка требований к экранам и действиям с оценкой уверенности |
| `agents/ux_designer/flows.py` | пользовательские потоки по ролям (`user-flows.*.md`) |
| `agents/ux_designer/labels.py` | русские подписи полей без модели |
| `agents/ux_designer/prompts.py` | промпты UX-агента |
| `agents/frontend/agent.py` | frontend-агент: слоты, шлюзы, `ng build`, откат |
| `agents/vlm_reviewer/capture.py` | Playwright: съёмка экранов, форсирование состояний, DOM-проверки, axe |
| `agents/vlm_reviewer/review.py` | UI-ревьюер: DOM + модель, отчёт |
| `agents/vlm_reviewer/autofix.py` | дефекты → правки спеки и замечания слотам |

### Генератор и каркас

| Файл | Что |
|---|---|
| `scaffolds/angular-admin/` | золотой каркас Angular 20.1.6 + PrimeNG 20: оболочка, вход, состояния, карта, realtime, nginx с CSP, Dockerfile |
| `generators/angular/generate.py` | главный генератор: спека + OpenAPI → проект |
| `generators/angular/openapi_ts.py` | модели и `ApiService` из OpenAPI |
| `generators/angular/pages.py` | страницы по архетипам (список, карточка, форма, карта, лента, сводка, особый экран) |
| `generators/angular/scenarios.py` | e2e-сценарии пользователя + `e2e/scenarios.trace.json` (требование → сценарий) |
| `generators/angular/samples.py` | тестовые значения полей для сценариев |
| `generators/angular/showcase.py` | витрина компонентов UI Kit |
| `design-system/primeng/` | манифест UI Kit, правила для промптов, токены, пресет темы |
| `design-system/material3/` | манифест для Android-спеки (владелец — участник 5, сейчас минимальный) |

### Проверки и утилиты

| Файл | Что |
|---|---|
| `tools/kit_lint.py` | соответствие UI Kit (сырые элементы, выдуманные компоненты, цвета литералом) |
| `tools/template_check.py` | шаблоны Angular: баланс тегов, неизвестные имена |
| `tools/ts_check.py`, `tools/ts-shims/` | проверка типов без `node_modules` |
| `tools/mock_api.py`, `tools/spa_server.py` | mock-API из OpenAPI и сервер собранной админки |
| `tools/contract_diff.py` | совместим ли интерфейс с поднятым backend (Swagger) |
| `tools/ui_docs.py` | документация интерфейса и трассируемость |
| `tools/prompt_budget.py` | сколько токенов уйдёт в модель (сухой прогон) |
| `scripts/rehearse.py` | репетиция на всех кейсах из `fixtures/` |

### Консоль конвейера

| Файл | Что |
|---|---|
| `console/spec/` | спека экранов консоли — консоль собирается тем же генератором |
| `console/slots/` | написанные вручную блоки: граф этапов (+ подтверждение этапа), метрики, артефакты, форма запуска |
| `console/build.py` | сборка консоли в `console/app` (в Docker: `/console-api` → `ORCHESTRATOR_URL`) |
| `console/demo_server.py` | **имитация** оркестратора по `console-api.yaml`, пока нет настоящего; прогоны помечены «Демо-режим» |

### Данные для проверок

| Папка | Что |
|---|---|
| `fixtures/case1/` | эталон кейса №1 — **не трогаем до финального прогона** |
| `fixtures/synthetic-*` | синтетические ТЗ других доменов — только для репетиций (проверяем, что конвейер не заточен под один кейс). В продукт не входят |
| `tests/` | автотесты конвейера (`py -m pytest -q tests`) |

## 4. Как встроить в общий проект

### Участник 1 — оркестратор

**Граф.** Веб-ветка встраивается одной функцией после системного анализа:

```python
from typing import Annotated, Any, TypedDict
from agents.nodes import register, merge_stages

class State(TypedDict, total=False):
    artifacts: str          # корень папки артефактов прогона
    llm: Any                # клиент с методом complete() по contracts/agent/agent_contract.py; None — без модели
    build: bool             # запускать ng build и jest (по умолчанию True)
    ui_base_url: str | None # адрес поднятой админки для ревью; None — ревьюер поднимет mock сам
    stages: Annotated[dict, merge_stages]

register(graph, after="02_system")
# 02_system → 03_ux → 05_web → 07_ui → (один повтор 05_web, если ревьюер поправил спеку) → 09_docs_ui
graph.add_edge("09_docs_ui", "<следующий узел>")
```

Узлы не бросают исключений: при сбое возвращают `passed=False` с ошибкой. Результат этапа лежит в
`state["stages"]["03_ux" | "05_web" | "07_ui" | "09_docs_ui"]`. Проверено на заменителе с тем же API, на
настоящем LangGraph — при стыковке.

**Модель.** Переменные `SPECFORGE_LLM_URL` (напр. `http://vllm:8000/v1`), `SPECFORGE_LLM_MODEL`, `SPECFORGE_LLM_KEY`.
vLLM запускать с `--reasoning-parser qwen3`. Или передать свой клиент в `state["llm"]`.

**API консоли.** Сервер оркестратора должен отвечать по `contracts/console/console-api.yaml` на путях
`/console-api/v1/...`:

| Что | Путь | Важно |
|---|---|---|
| запуск прогона | `POST /runs` (multipart: file или text, mode) | вернуть `Run` |
| список и прогон | `GET /runs`, `GET /runs/{id}` | `simulated` не выставлять — тогда пропадёт «Демо-режим» |
| поток событий | `GET /runs/{id}/events` (SSE) | `event:` = тип, `data:` = RunEvent, `id:` = seq, поддержать `Last-Event-ID` |
| подтверждение этапа | `POST /runs/{id}/approve` | `stageId`, `decision`, `comment`, `answers`; reject без comment → 422; approve с неотвеченным блокирующим вопросом → 409; reject перезапускает этап с комментарием как замечанием агенту |
| файлы | `GET /runs/{id}/artifacts`, `.../artifacts/content?path=` | |
| метрики | `GET /runs/{id}/metrics` | токены, попытки, время по этапам |

У этапа можно отдавать `summary` (итог для человека) и `questions[{id, text, assumption, blocking}]` — консоль
их покажет. В конце прогона — `repoUrl` (GitLab) и `appUrl` (развёрнутый прототип), в консоли появятся ссылки.
Эталон поведения — `console/demo_server.py`.

### Участник 2 — анализ

`01_requirements/requirements.json`:

```json
{
  "product": "…",
  "roles": [{ "id": "manager", "title": "Руководитель" }],
  "functional": [{ "id": "FR-01", "title": "…", "channels": ["web", "android"] }],
  "openQuestions": [{ "id": "Q1", "text": "…", "assumption": "…", "blocking": true }],
  "assumptions": ["…"]
}
```

`channels` решает, для каких платформ строить экраны. `openQuestions` и `assumptions` необязательны, их увидит
человек в консоли при подтверждении. Необязательный `01_requirements/profile.json` с `{"platforms": ["web"]}`
задаёт платформы явно. В OpenAPI — русские `title` у полей и `x-enum-labels` у перечислений, тогда подписи в
интерфейсе будут человеческими. Папки `09_docs/ui/*` и `03_ux/user-flows.*.md` включить в MkDocs.

### Участник 3 — backend и DevOps

- **OpenAPI** — по `contracts/api/api-conventions.md`: уникальные `operationId`, `x-roles`, списки `{items, total}`,
  вход `{accessToken, expiresIn, user{roles}}`, ошибки RFC 7807, `examples` у геометрий и полей с `pattern`.
- **События** — хаб `/hubs/events`, формат в `contracts/realtime/`.
- **Проверка совместимости** с поднятым backend:
  `py tools\contract_diff.py --spec <artifacts>\03_ux\screen-spec.web.json --api http://<backend>/swagger/v1/swagger.json`
- **Docker.** Админка (`05_web/admin`) и консоль (`console/app`) — готовые Dockerfile на nginx. Адреса задаются
  переменными окружения, пересборка не нужна:

```yaml
services:
  admin:                     # сгенерированная админка
    build: ./artifacts/05_web/admin
    environment:
      BACKEND_URL: http://backend:8080      # /api и /hubs пересылаются сюда
    ports: ["8081:80"]
    depends_on: [backend]
  console:                   # консоль конвейера
    build: ./console/app
    environment:
      ORCHESTRATOR_URL: http://orchestrator:8765   # /console-api пересылается сюда (SSE без буфера)
    ports: ["8080:80"]
```

  Healthcheck обоих — `GET /healthz`. Имена сервисов и порты — подставить свои.
- **CI** — для админки вызывать `npm run gate` (kit-lint + `ng build` + jest) и `npx playwright test`
  (e2e против стенда: `BASE_URL`, `E2E_USERS` — учётные записи по ролям в JSON).
- **Репозиторий** создаёт оркестратор через GitLab участника 3; веб-часть только пишет файлы в папку артефактов.

### Участник 5 — Android, тесты, безопасность

- Мобильная спека экранов: `03_ux/screen-spec.android.json` (схема та же, что у веба).
- Манифест Material 3 `design-system/material3/kit-manifest.json` сейчас минимальный (только теги) — нужно
  дополнить компонентами, свойствами и правилами.
- Скриншоты Android для UI-ревьюера: `<screenId>--<state>--<viewport>.png`, `testTag` как `data-testid` в вебе
  (`<screen>`, `<screen>-state-<state>`, `<screen>-action-<id>`, `<screen>-field-<name>`, `<screen>-submit`).
- E2E-сценарии веба: `05_web/admin/e2e/scenarios.spec.ts`, теги требований `@FR-xx`
  (`npx playwright test e2e/scenarios.spec.ts --grep @FR-01`).

## 5. Порядок стыковки

1. Участники 2 и 3 дают `requirements.json` и `openapi.yaml` → кладём в `fixtures/team-real/01_requirements/`
   и `02_system/` → `py -m scripts.rehearse --build --only team-real`. Проверяет, что их форматы подходят.
2. Поднят backend → `contract_diff` (см. выше) → e2e-сценарии против стенда.
3. Поднята модель → `SPECFORGE_LLM_URL=…` → `py -m scripts.rehearse --llm --build --only team-real`, записать
   реальные токены и попытки.
4. Готов граф участника 1 → `register(graph)` → прогон из консоли через настоящий оркестратор.
5. Docker-compose: админка + консоль + backend + оркестратор, проверка `/healthz`.
6. Финальный прогон кейса №1 всей командой.

## 6. Что проверено и что нет

**Проверено:** 121 автотест конвейера; шлюзы (kit-lint, шаблоны, типы) — 0 ошибок на 5 синтетических кейсах и
консоли; `ng build` и jest на ноутбуке (репетиция на синтетическом кейсе); цепочка узлов графа на заменителе LangGraph;
в коде конвейера нет слов предметной области (тест).

**Не проверено (нужны части коллег):** работа с настоящей Qwen (агенты проверены на поддельной модели);
настоящий LangGraph; админка и e2e-сценарии против настоящего backend; консоль с настоящим оркестратором;
конфиги nginx в Docker (`nginx -t` не запускался).

**Имитация:** `console/demo_server.py` — этапы по таймеру, токены из таблицы, поэтому «Демо-режим». В видео и на
защите — только настоящий оркестратор или с видимой пометкой.
