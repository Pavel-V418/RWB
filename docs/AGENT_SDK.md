# SpecForge Agent SDK

Контракт между платформой и авторами агентов. Публичная поверхность — только
`specforge.core.contracts`, `specforge.core.agent_api` и `specforge.llm`. Всё остальное
внутри `specforge/` может меняться без предупреждения.

Изменения контракта сопровождаются правкой этого документа и пометкой **BREAKING** в коммите.
Добавление необязательного поля со значением по умолчанию совместимость не ломает.
Что поменялось и как перевести своего агента — в разделе [«Изменения»](#10-изменения).

---

## 1. Что такое агент

Агент — один этап конвейера. Он читает входные артефакты, пишет выходные и возвращает
`StageResult`. После агента платформа запускает **шлюз** (команды проверки). Этап пройден,
только если шлюз зелёный.

```
agents/<name>/
  __init__.py
  agent.py          # обязательно: переменная AGENT
  prompts/          # system.md, user.md (structured) или system.md, task.md (tool)
  schemas/          # pydantic-схемы ответа (structured)
```

Реестр находит агента по каталогу: `agents/<name>/agent.py` должен определять
`AGENT`, а `AGENT.spec.name` должен совпадать с `<name>`. Имя — `[a-z_][a-z0-9_]*`.

Есть два вида агентов:

| Вид | Базовый класс | Когда использовать |
|---|---|---|
| `structured` | `StructuredAgent[Schema]` | Один вызов LLM → JSON по pydantic-схеме (анализ ТЗ, планы, профили) |
| `tool` | `ToolAgent` | Многошаговая работа с файлами и командами (генерация и правка кода) |

В обоих случаях автору остаётся написать промпты и (для structured) схему.

---

## 2. Structured-агент

Полный рабочий образец — `agents/_example/`.

**`agents/my_analyzer/schemas/result.py`**
```python
from pydantic import BaseModel, Field


class Requirements(BaseModel):
    actors: list[str] = Field(description="Роли пользователей")
    features: list[str] = Field(description="Функции системы, по одной на элемент")
```

Описания полей (`Field(description=...)`) попадают в JSON Schema, которую видит модель, —
пишите их как часть промпта.

**`agents/my_analyzer/agent.py`**
```python
from agents.my_analyzer.schemas.result import Requirements
from specforge.core.agent_api import StructuredAgent
from specforge.core.contracts import AgentSpec, ProfileName


class MyAnalyzer(StructuredAgent[Requirements]):
    spec = AgentSpec(
        name="my_analyzer",
        stage="analysis",
        kind="structured",
        inputs=["requirements/*.md"],
        outputs=["analysis/requirements.json"],
        profile=ProfileName.ANALYSIS,
    )
    schema = Requirements
    output_file = "analysis/requirements.json"


AGENT = MyAnalyzer()
```

**`prompts/system.md`** — роль и правила. **`prompts/user.md`** (необязателен) — шаблон
запроса; `$inputs` заменяется содержимым входных файлов:

```
<file path="requirements/case.md">
...текст...
</file>
```

Без `user.md` модель получает только этот блок.

Что делает `StructuredAgent.run`:
1. Если `spec.inputs` не пуст и ни один файл не нашёлся — `failed` без вызова LLM.
2. `build_messages(ctx)` → `ctx.llm.structured(messages, schema)`. Если JSON не прошёл
   валидацию, модель получает текст ошибки и пробует снова (`LLM_STRUCTURED_ATTEMPTS`).
3. `write_outputs(ctx, result)` пишет `output_dir/<output_file>`.
4. Возвращает `StageResult` с usage, артефактами и `summarize(result)`.

Что можно переопределить:

| Метод | Зачем |
|---|---|
| `build_messages(ctx) -> list[ChatMessage]` | Своя сборка запроса (например, только часть входов) |
| `write_outputs(ctx, result) -> list[str]` | Несколько файлов или не-JSON формат; вернуть пути относительно `output_dir` |
| `summarize(result) -> str` | Короткая строка для отчёта |

Подставляйте переменные через `self.load_prompt("x.md", name=value)` — используется
`string.Template` (`$name`), поэтому фигурные скобки в примерах JSON экранировать не нужно.

---

## 3. Tool-агент

```python
from specforge.core.agent_api import ToolAgent
from specforge.core.contracts import AgentSpec


class BackendCoder(ToolAgent):
    spec = AgentSpec(
        name="backend_coder",
        stage="backend",
        kind="tool",
        inputs=["analysis/*.json"],
        outputs=["backend/**/*.cs"],
        tools=["read_file", "write_file", "str_replace", "list_dir", "grep", "run"],
        sandbox_image="specforge/dotnet:10",
        gate_commands=["dotnet build backend -warnaserror"],
        protected_globs=["backend/tests/contract/**"],
        profile=ProfileName.CODE,
        max_steps=40,
        token_budget=400_000,
    )


AGENT = BackendCoder()
```

Промпты: `prompts/system.md` и `prompts/task.md`. В `task.md` доступны `$inputs` (содержимое
входных файлов по `spec.inputs`), `$workdir` (`/work`), `$input_dir` и `$output_dir` (пути на хосте).
Для динамической задачи переопределите `task(ctx) -> str`.

`ToolAgent.run` вызывает

```python
agent_loop(ctx, system_prompt, task, spec.tools,
           max_steps=spec.max_steps, temperature=spec.temperature,
           thinking=spec.thinking, profile=spec.profile) -> AgentOutcome
```

и считает этап выполненным (`passed`, дальше — шлюз), только если модель вызвала `finish`.
`profile` — профиль LLM (по умолчанию `CODE`); `temperature` и `thinking` перекрывают
значения профиля, только если они не `None`. Причина остановки попадает в
`StageResult.stopped_reason`.

### Цикл (ReAct)

Шаг — один вызов LLM. Вызовы инструментов из ответа выполняются по порядку, результаты
возвращаются модели. Ошибка инструмента (нет файла, неуникальный `old`, путь вне `/work`,
невалидные аргументы) приходит модели текстом — цикл не падает.

`AgentOutcome`:

| Поле | Что это |
|---|---|
| `summary` | Аргумент `finish(summary)` или описание причины остановки |
| `steps` | Число вызовов LLM |
| `usage` | Токены цикла |
| `stopped_reason` | `finish` · `max_steps` (`spec.max_steps`) · `token_budget` (`ctx.budget_tokens`) · `loop` (один и тот же вызов 3 раза подряд или 3 ответа подряд без инструментов) · `error` (LLM недоступна, неизвестный инструмент в `spec.tools`) |
| `changed_files` | Файлы, созданные или изменённые `write_file`/`str_replace` |

### Инструменты (`spec.tools`; `finish` доступен всегда)

| Имя | Аргументы | Поведение |
|---|---|---|
| `read_file` | `path, start_line?, end_line?` | Текст с номерами строк, не больше 400 строк за вызов |
| `write_file` | `path, content` | Только новый файл; существующий — ошибка «используй str_replace» |
| `str_replace` | `path, old, new` | `old` должен встречаться ровно один раз, иначе ошибка с числом совпадений |
| `list_dir` | `path=".", depth=2` | Дерево каталога; `node_modules`, `.git`, `bin`, `obj`… не раскрываются |
| `grep` | `pattern, glob="**/*"` | Регулярное выражение, до 100 совпадений |
| `run` | `cmd, timeout=300` | Команда в песочнице; в контекст — первые 2000 и последние 4000 символов вывода, полный лог — `runs/<run_id>/logs/` |
| `finish` | `summary` | Завершить работу |

Все пути — внутри `/work` (= `output_dir`): `src/a.py`, `./src/a.py` и `/work/src/a.py`
равнозначны, выход наружу через `..`, абсолютные пути и симлинки запрещён. Файлы из
`spec.protected_globs` менять нельзя.

### Песочница

Платформа создаёт песочницу на этап; в ней работают и инструменты, и шлюз:
- задан `sandbox_image` → контейнер Docker: `/work` смонтирован из `output_dir`, кэши
  nuget/npm/gradle/pip — общие именованные volumes, лимиты `SANDBOX_CPUS`, `SANDBOX_MEMORY`,
  сеть `SANDBOX_NETWORK`; после этапа контейнер удаляется;
- иначе — локальный subprocess на хосте (без изоляции; для разработки и тестов).

`SPECFORGE_SANDBOX=local` заставляет работать локально даже с `sandbox_image`.

### Prefix caching и компакция

Запрос всегда начинается с одного и того же: system, задача, описания инструментов —
байт-в-байт. Поэтому в `system.md`/`task.md` не должно быть ничего изменчивого (время, номер
шага, случайные id), а большие неизменные входы кладите в задачу через `$inputs`.

Когда история превышает `SPECFORGE_COMPACT_AT` токенов (по умолчанию 60000), результаты
инструментов старше `SPECFORGE_KEEP_LAST` шагов (по умолчанию 4) заменяются заглушками вида
`[run: exit 0, 340 строк, лог: runs/…/logs/….log]`, длинные аргументы старых вызовов — пометкой
о пропуске. Модель может перечитать нужное через `read_file`.

### События (`ctx.emit`)

`step_started` (step, tokens) · `tool_called` (tool, arguments) · `tool_result` (tool, ok,
result — усечён до 500 символов) · `history_compacted` · `agent_finished` (stopped_reason,
steps, tokens, changed_files). Structured-агенты шлют `step_started` и `agent_finished`.

Правила для tool-агентов:
- Правка существующих файлов — через `str_replace`, а не переписывание целиком.
- Не возвращайте в контекст полные логи — инструменты сами их усекают.

---

## 4. Поля `AgentSpec`

| Поле | Тип / умолчание | Смысл и пример |
|---|---|---|
| `name` | `str` | Имя = каталог: `"backend_coder"` |
| `stage` | `str` | Этап конвейера для отчётов: `"backend"` |
| `kind` | `"structured" \| "tool"` | Вид агента |
| `description` | `""` | Одна строка для `specforge agent list` |
| `inputs` | `[]` | Glob-шаблоны относительно `artifacts/` (локально — `--in`): `["analysis/*.json"]` |
| `outputs` | `[]` | Glob-шаблоны создаваемых файлов относительно `output_dir`: `["web/src/**/*.ts"]` |
| `prompt_dir` | `"prompts"` | Каталог промптов относительно каталога агента |
| `tools` | `[]` | Инструменты tool-агента (§3): `["read_file", "str_replace", "run"]` |
| `sandbox_image` | `None` | Docker-образ для инструментов и шлюза: `"specforge/node:22"` |
| `gate_commands` | `[]` | Команды шлюза, см. §5: `["npx tsc --noEmit", "npm test"]` |
| `gate_parsers` | `{}` | Явный парсер вывода для команды шлюза: `{"npm run build": "tsc"}` (§5) |
| `protected_globs` | `[]` | Что нельзя менять агенту и ремонту: `["**/*.spec.ts"]` |
| `profile` | `CODE` | Профиль LLM (§8): `ANALYSIS`, `REVIEW`, `CODE`, `REPAIR` |
| `thinking` | `None` | Перекрыть режим размышлений профиля; `None` — как в профиле |
| `temperature` | `None` | Перекрыть температуру профиля (0.0–2.0); с `thinking=True` меньше 0.5 нельзя — поднимается до 0.6 с предупреждением |
| `max_steps` | `30` | Лимит шагов `agent_loop` |
| `token_budget` | `200_000` | Бюджет токенов этапа (включая ремонты) |
| `max_repairs` | `2` | Попыток ремонта при красном шлюзе (§5, «Ремонт»); после них — `degraded`; `0` — без ремонта, `failed` |
| `depends_on` | `[]` | Имена агентов, которые должны завершиться раньше: `["analyzer"]` |
| `enabled_if` | `None` | Флаг `CaseProfile`: `"has_mobile"` — этап `skipped`, если флаг ложен |
| `parallel_group` | `None` | Агенты одной группы могут идти параллельно: `"codegen"` |
| `llm_free` | `False` | Детерминированный агент; любой вызов LLM — ошибка |

Флаги `CaseProfile`: `has_mobile`, `has_web`, `has_backend`, `has_geo`, `has_events`,
`has_integrations` и произвольные из `extra` (например, `"has_payments"`).

`AgentSpec` неизменяем (`frozen`) и не принимает неизвестных полей — опечатка в имени поля
сразу падает при импорте агента.

---

## 5. Шлюз

Шлюз — список shell-команд в `spec.gate_commands`. Платформа запускает их после агента
в песочнице этапа (той же, где работал агент) с рабочим каталогом `/work` (= `output_dir`):
по очереди и **до первой упавшей** — тесты не запускаются на некомпилируемом коде.
Этап пройден, если все команды завершились с кодом 0.

```python
gate_commands = [
    "dotnet build backend -warnaserror",
    "dotnet test backend/tests --no-build --logger trx",
]
```

- Пишите команды, которые падают с ненулевым кодом на любую проблему (`-warnaserror`,
  `--max-warnings 0`, `tsc --noEmit`).
- Шлюз запускает платформа, а не агент: из `run()` проверки не вызывайте.
- Полный вывод — `runs/<run_id>/logs/<name>.gate.log`. В `StageResult.gate` попадают только
  разобранные `Diagnostic` — их увидит ремонт.
- Таймаут одной команды — `SPECFORGE_GATE_TIMEOUT_S` (по умолчанию 1800 с).
- У structured-агента схема уже проверена при разборе ответа; шлюз нужен, если есть
  дополнительные инварианты (например, `python -m json.tool out.json`).

### Разбор вывода

Вывод каждой команды разбирает парсер. Он выбирается по виду команды:

| Парсер | Команда (regex) | Что разбирает | Категория |
|---|---|---|---|
| `msbuild` | `dotnet build/publish/run`, `msbuild` | `path(line,col): error CS1234: … [project]` | compile |
| `dotnet-test` | `dotnet test` | msbuild + `trx` | compile, test |
| `trx` | — | `**/*.trx` этого запуска; без отчёта — консоль `Failed …` | test |
| `tsc` | `tsc`, `ng build`, `vue-tsc` | `path:line:col - error TS…`, `path(line,col): error TS…`, Angular esbuild `✘ [ERROR] TS…/NG…` | compile |
| `eslint` | `eslint`, `ng lint` | `--format json` | lint |
| `gradle` | `gradle`, `gradlew` | `kotlinc` + `junit` | compile, test |
| `kotlinc` | `kotlinc` | `e: file:///path:line:col msg` (и старый `e: path: (l, c): msg`) | compile |
| `junit` | — | JUnit XML (`build/test-results`, `surefire-reports`) этого запуска | test |
| `spectral` | `spectral` | `-f json`; без него — формат stylish | lint |
| `asyncapi` | `asyncapi` | `asyncapi validate` (stylish) | compile |
| `psql` | `psql` | `psql:file:line: ERROR: …` + `LINE`/`DETAIL`/`HINT` | compile |
| `generic` | всё остальное | последние строки со словами error / exception / failed | generic |

Если команда не опознаётся (обёртка `make check`, `npm run build`), укажите парсер явно —
ключ словаря должен совпадать с командой из `gate_commands` символ в символ:

```python
spec = AgentSpec(
    name="web_coder",
    stage="web",
    kind="tool",
    gate_commands=["npm run build", "npm run lint -- --format json"],
    gate_parsers={
        "npm run build": "tsc",
        "npm run lint -- --format json": "eslint",
    },
)
```

Неизвестное имя парсера — ошибка конфигурации, этап падает до запуска команд.
Если парсер не нашёл ошибок в упавшей команде, в ход идёт `generic`, а если и он пуст —
одна диагностика с хвостом вывода.

### Что получает ремонт

- Диагностики дедуплицируются (MSBuild печатает каждую ошибку дважды) и упорядочиваются:
  **compile → test → lint → generic**, внутри категории ошибки раньше предупреждений.
- В `GateResult.diagnostics` — не больше 20; `total_errors` — число всех уникальных ошибок.
- Пути приводятся к виду относительно `/work` (`/work/src/a.cs`, `file:///work/…` → `src/a.cs`).
- Если файл есть в `/work`, у диагностики заполнен `code_window` — 15 строк вокруг ошибки:

```
     5 | using Api.Services;
 ...
>   12 | var app = builder.Build()
 ...
    19 | app.Run();
```

Отчёты тестов (`*.trx`, JUnit XML) включайте в команду (`--logger trx`; Gradle пишет их
сам): из них берутся имя теста, сообщение и место в коде из стека.

### Ремонт

Если шлюз красный и `spec.max_repairs > 0`, платформа запускает общий агент-ремонтник
(`specforge/repair`). Писать его для своего этапа не нужно. До `max_repairs` итераций:

1. git-снимок `/work` (служебный репозиторий `/work/.specforge/git`, ваш `.git` не трогается);
2. узкий контекст: диагностики шлюза с `code_window`, определения упомянутых символов,
   фрагмент контракта (OpenAPI/AsyncAPI/DDL), если ошибка в сгенерированном или защищённом файле
   (лимит `REPAIR_CONTEXT_TOKENS`), и до 3 «известных решений» из памяти исправлений;
3. `agent_loop` с профилем `REPAIR` и инструментами `read_file`, `str_replace`, `grep`, `run`
   (только команды шлюза этапа);
4. повторный шлюз.

Итерация **откатывается** к снимку, если ошибок стало больше, изменён файл из
`protected_globs` или в правке появилось глушение ошибок / отключение тестов
(`#pragma warning disable`, `@Suppress`, `eslint-disable`, `@ts-ignore`, `any`,
`# type: ignore`, `Skip`/`@Disabled`/`it.skip`, пустой или catch-all `catch`, удалённый тест).
Если ремонтник считает, что виноват контракт, он завершает работу с `finish("КОНТРАКТ: …")` —
этап сразу эскалируется.

Шлюз позеленел — `passed`, решение пишется в память исправлений (Postgres, `SPECFORGE_PG_DSN`;
без неё память отключена). Ремонты исчерпаны — `degraded`: workspace откатывается к последнему
зелёному снимку (состоянию после последнего этапа с зелёным шлюзом), если он есть, и
публикуется событие `escalation`. Бюджет `token_budget` — общий на агента и ремонты.

События ремонта: `repair_started`, `fix_memory_hits`, `repair_rolled_back`,
`repair_succeeded`, `escalation`.

Чтобы ремонт работал хорошо:
- кладите контракты в стандартные места (`**/openapi*.yaml`, `**/asyncapi*.yaml`,
  `**/schema*.sql`, `**/migrations/*.sql`) и помечайте сгенерированный код
  (`<auto-generated>`, каталог `generated/` или `protected_globs`);
- в образе песочницы должен быть `git`, иначе ремонт идёт без снимков и откатов.

Статусы `StageResult.status`: `passed`, `failed`, `degraded` (шлюз красный, ремонты
исчерпаны, конвейер идёт дальше), `skipped` (`enabled_if` ложен).

---

## 6. Локальный запуск

```bash
uv sync
cp .env.example .env        # LLM_BASE_URL, LLM_MODEL (+ LLM_API_KEY)
uv run specforge agent list
uv run specforge agent run _example --in examples/ --out out/
```

`agent run` печатает `StageResult` в JSON (stdout), логи — JSON в stderr, код выхода
`0` — `passed`/`degraded`, `1` — `failed`, `2` — ошибка настройки. Опции: `--workdir`
(по умолчанию `runs/<run_id>`), `--run-id`, `--budget`.

Работает с любым OpenAI-совместимым эндпоинтом:

| Эндпоинт | Настройки |
|---|---|
| vLLM + Qwen3 (по умолчанию) | `LLM_VLLM_EXTRAS=true`, `LLM_STRUCTURED_MODE=json_schema` |
| OpenAI | `LLM_BASE_URL=https://api.openai.com/v1`, `LLM_API_KEY=...`, `LLM_VLLM_EXTRAS=false` |
| Эндпоинт без `response_format` | `LLM_STRUCTURED_MODE=prompt` |

Свой vLLM поднимается через `infra/docker-compose.platform.yml`; проверить эндпоинт целиком
(чат, tool call, json_schema, thinking on/off) — `uv run python infra/smoke_llm.py`.

---

## 7. Тесты агента

Агент тестируется без GPU и сети: `FakeLLM` отдаёт скриптованные ответы и наследует
`LLMClient`, поэтому разбор JSON, повторы и учёт токенов работают как в бою.

```python
from agents.my_analyzer.agent import AGENT
from specforge.core.agent_api import AgentContext
from specforge.core.contracts import StageStatus
from specforge.llm import FakeLLM


def test_my_analyzer(tmp_path):
    (tmp_path / "in" / "requirements").mkdir(parents=True)
    (tmp_path / "in" / "requirements" / "case.md").write_text("...", "utf-8")
    llm = FakeLLM(['{"actors": ["оператор"], "features": ["поиск"]}'])
    ctx = AgentContext(
        run_id="t",
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
        workdir=tmp_path / "run",
        llm=llm,
        budget_tokens=10_000,
    )

    result = AGENT.run(ctx)

    assert result.status == StageStatus.passed
    assert (tmp_path / "out" / "analysis" / "requirements.json").exists()
    assert "case.md" in llm.calls[0].messages[1].content  # что ушло в модель
```

Элементы скрипта `FakeLLM`:

| Элемент | Что вернёт |
|---|---|
| `"текст"` | Ответ с этим content (`<think>…</think>` уйдёт в reasoning) |
| `FakeReply(content=, reasoning=, tool_calls=[FakeToolCall(name, {args})])` | Ответ с размышлениями и вызовами инструментов |
| `FakeReply(structured=Model(...))` | JSON для `structured()` |
| `FakeReply(..., expect=Expect(profile="ANALYSIS", tools=[...], schema="Model", contains="..."))` | То же, но сначала проверит запрос; несовпадение — `FakeExpectationError` |
| `LLMTransientError("503")` | Исключение транспорта (проверка повторов) |
| `lambda request: "..."` | Ответ, вычисленный по `LLMRequest` |

`llm.calls` — список отправленных `LLMRequest`, `llm.assert_exhausted()` — все ответы
использованы. Фикстура `make_ctx` из `tests/conftest.py` собирает контекст во временном каталоге.

---

## 8. `AgentContext`

| Поле | Что это |
|---|---|
| `run_id` | Идентификатор запуска |
| `input_dir` | Корень входных артефактов |
| `output_dir` | Куда писать результаты |
| `workdir` | Служебный каталог запуска (логи, временные файлы) |
| `llm` | `LLMClient` — единственный способ вызвать модель |
| `sandbox` | Песочница этапа (`/work` = `output_dir`) или `None` — тогда `agent_loop` создаст локальную |
| `protected_globs` | Файлы, закрытые для изменений инструментами (из `spec.protected_globs`) |
| `allowed_commands` | Если задано — `run` выполняет только эти команды (так ремонт ограничен командами шлюза) |
| `emit(event)` | Отправить `Event` (в `EventBus` платформы): `ctx.emit(Event(kind="my.progress", message="..."))` |
| `budget_tokens` | Бюджет токенов этапа |

### `LLMClient` (`specforge.llm`)

```python
r = ctx.llm.chat(messages, tools=[ToolSpec(...)], profile=ProfileName.CODE)
r.content, r.reasoning, r.tool_calls, r.usage, r.latency_s
history.append(r.to_message())  # без reasoning — размышления в историю не попадают

value = ctx.llm.structured(messages, MySchema, profile=ProfileName.ANALYSIS)

with ctx.llm.track() as meter:  # токены вызовов внутри блока
    ...
meter.usage
```

- **Профили** (`ProfileName`) — значения по умолчанию:

  | Профиль | thinking | temperature | top_p | max_tokens |
  |---|---|---|---|---|
  | `ANALYSIS` | да | 0.6 | 0.95 | 16384 |
  | `REVIEW` | да | 0.6 | 0.95 | 8192 |
  | `CODE` (по умолчанию) | нет | 0.2 | 0.8 | 8192 |
  | `REPAIR` | нет | 0.1 | 0.8 | 4096 |

  Ещё у профиля есть `top_k`, `presence_penalty` и `timeout_s` (по умолчанию
  `max_tokens / LLM_MIN_TOKENS_PER_S + 60`, `LLM_MIN_TOKENS_PER_S=25`). Переопределения — в
  `config/profiles.yaml` (`LLM_PROFILES_PATH`; относительный путь — от корня проекта).
  `temperature=`, `thinking=`, `max_tokens=` в вызове перекрывают профиль, если не `None`.
  С включённым thinking температура ниже 0.5 поднимается до 0.6 с предупреждением.
- **structured**: `response_format=json_schema`; при ошибке валидации — один повтор с текстом
  ошибки, затем `StructuredOutputError` (`.raw`, `.validation_error`).
- **tool calls**: если сервер не разобрал вызов и он остался в content (`<tool_call>` XML или
  JSON-блок), клиент достанет его сам. Невалидный вызов приходит как `ToolCall` с `error` —
  верните модели `ChatMessage.tool_result(call.id, tool_error_message(call))`, не исполняя его.
- **Сбои сети/5xx/429** повторяются с экспоненциальным backoff (`LLM_MAX_RETRIES`), после —
  `LLMUnavailableError`. Параллелизм ограничен `LLM_MAX_CONCURRENCY`.
- **Учёт токенов**: платформа привязывает `ctx.llm` к run/stage/agent (`llm.bind(...)`), разрез
  доступен через `llm.tracker.breakdown("stage")`; хуки `llm.add_hook(fn)` получают
  `LLMCallRecord` каждого вызова (для телеметрии).

---

## 9. Правила

1. **Никакого доменного кода в `specforge/` и `agents/`.** Агенты универсальны: детали кейса
   приходят только из входных артефактов. Слова из `tests/domain_leak_words.txt` вне
   `examples/` и `tests/fixtures/` роняют тесты.
2. **LLM — только через `ctx.llm`.** Не импортируйте `openai`, `httpx` и т.п. для вызова модели:
   в обход `LLMClient` теряются учёт токенов, профили, ретраи и телеметрия.
3. **Детерминированное — без LLM.** Если шаг можно сделать кодом (переименовать, собрать
   индекс, скопировать шаблон), делайте кодом и ставьте `llm_free=True`.
4. **Пишите только в `output_dir`** и только то, что объявлено в `spec.outputs`.
   Не трогайте `protected_globs`.
5. **Стабильный префикс промпта.** Неизменная часть (system.md, правила, схема) — в начале,
   переменная — в конце: так работает prefix caching в vLLM.
6. **Не тащите в промпт полные логи и файлы целиком без нужды** — экономьте токены.
7. **Типы и тесты.** Схемы — pydantic-модели, не `dict`. У агента есть тест на `FakeLLM`;
   `uv run pytest -q`, `uv run ruff check .`, `uv run mypy specforge agents` — зелёные.

---

## 10. Изменения

### BREAKING: профили, события, невалидные tool call

**Что изменилось**

| Где | Было | Стало |
|---|---|---|
| `AgentSpec.profile` | — | `ProfileName`, по умолчанию `CODE` |
| `AgentSpec.thinking` | `bool = False` | `bool \| None = None` — `None` значит «как в профиле» |
| `AgentSpec.temperature` | `float = 0.2` | `float \| None = None`; с `thinking=True` меньше 0.5 → 0.6 и предупреждение |
| `agent_loop(temperature=, thinking=)` | `0.2`, `False` | `None`, `None` (берутся из `profile`) |
| `Profile` | — | новые поля `presence_penalty`, `timeout_s`; у профилей по умолчанию есть `max_tokens`; `REVIEW` — t=0.6 (было 0.3) |
| `ProfileName` | `specforge.llm.profiles` | `specforge.core.contracts` (из `specforge.llm` импортируется как раньше) |
| `PipelineState.events` | список событий в состоянии графа | удалено: события только через `ctx.emit` → `EventBus` |
| `LLM_PROFILES_PATH` | относительно текущего каталога | относительно корня проекта |
| История с невалидным tool call | в `arguments` уходил сырой текст модели | уходит `"{}"`; сырой текст — в логах (`llm.tool_call.invalid`) и событии `tool_called`; ошибку модель по-прежнему получает в результате инструмента |

Новые необязательные поля (совместимость не ломают): `GateResult.failed_command_index`,
`GateResult.tests_executed`, `StageResult.stopped_reason`.

**Миграция агента**

1. Выберите профиль по характеру работы и уберите ручные `temperature`/`thinking`, если они
   повторяли профиль:

   ```python
   # было
   spec = AgentSpec(
       name="analyzer", stage="analysis", kind="structured", thinking=True, temperature=0.6
   )
   # стало
   spec = AgentSpec(name="analyzer", stage="analysis", kind="structured", profile=ProfileName.ANALYSIS)
   ```

   Анализ и планирование — `ANALYSIS`, ревью — `REVIEW`, генерация кода и JSON — `CODE`.
2. Агент, у которого не было ни `temperature`, ни `thinking`, раньше работал с t=0.2 без
   thinking — это профиль `CODE` по умолчанию, менять ничего не нужно.
3. `thinking=True` вместе с `temperature` ниже 0.5 больше не работает как раньше: температура
   поднимется до 0.6. Либо уберите `temperature`, либо выключите thinking.
4. Если вы вызываете `agent_loop` или `ctx.llm.chat/structured` напрямую — передавайте
   `profile=self.spec.profile` и только те перекрытия, которые действительно нужны.
5. Если вы читали `state["events"]` — подпишитесь на `EventBus` (`bus.subscribe(fn)`).
6. Проверьте тесты агента: в `FakeLLM` профиль виден как `llm.calls[i].profile`, лимит —
   `llm.calls[i].max_tokens`; ожидание — `Expect(profile="ANALYSIS")`.
