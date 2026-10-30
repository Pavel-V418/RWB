# SpecForge Agent SDK

Контракт между платформой и авторами агентов. Публичная поверхность — только
`specforge.core.contracts`, `specforge.core.agent_api` и `specforge.llm`. Всё остальное
внутри `specforge/` может меняться без предупреждения.

Изменения контракта сопровождаются правкой этого документа и пометкой **BREAKING** в коммите.
Добавление необязательного поля со значением по умолчанию совместимость не ломает.

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
from specforge.core.contracts import AgentSpec


class MyAnalyzer(StructuredAgent[Requirements]):
    spec = AgentSpec(
        name="my_analyzer",
        stage="analysis",
        kind="structured",
        inputs=["requirements/*.md"],
        outputs=["analysis/requirements.json"],
        temperature=0.1,
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
        tools=["read_file", "write_file", "str_replace", "shell"],
        sandbox_image="specforge/dotnet:10",
        gate_commands=["dotnet build backend -warnaserror"],
        protected_globs=["backend/tests/contract/**"],
        thinking=True,
        max_steps=40,
        token_budget=400_000,
    )


AGENT = BackendCoder()
```

Промпты: `prompts/system.md` и `prompts/task.md` (в `task.md` доступны `$input_dir` и
`$output_dir`). Для динамической задачи переопределите `task(ctx) -> str`.

`ToolAgent.run` вызывает

```python
agent_loop(ctx, system_prompt, task, spec.tools,
           max_steps=..., temperature=..., thinking=...) -> AgentOutcome
```

> ⚠️ **Сейчас `agent_loop` временный**: один вызов LLM без инструментов, `final_message` —
> ответ модели. Сигнатура окончательная: полноценный ReAct-цикл с инструментами,
> `str_replace`-правками и компакцией контекста заменит реализацию, ваш код менять не придётся.

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
| `tools` | `[]` | Инструменты tool-агента: `["read_file", "str_replace", "shell"]` |
| `sandbox_image` | `None` | Docker-образ для инструментов и шлюза: `"specforge/node:22"` |
| `gate_commands` | `[]` | Команды шлюза, см. §5: `["npx tsc --noEmit", "npm test"]` |
| `protected_globs` | `[]` | Что нельзя менять агенту и ремонту: `["**/*.spec.ts"]` |
| `thinking` | `False` | Включить режим размышлений модели (дороже, точнее) |
| `temperature` | `0.2` | 0.0–2.0; для JSON-анализа — 0.0–0.2 |
| `max_steps` | `30` | Лимит шагов `agent_loop` |
| `token_budget` | `200_000` | Бюджет токенов этапа (включая ремонты) |
| `max_repairs` | `2` | Попыток ремонта при красном шлюзе; после них — `degraded` |
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

Шлюз — список shell-команд в `spec.gate_commands`. Платформа запускает их после агента,
каждую в песочнице с рабочим каталогом `output_dir`; этап пройден, если все завершились
с кодом 0.

```python
gate_commands = [
    "dotnet build backend -warnaserror",
    "dotnet test backend/tests --no-build",
]
```

- Пишите команды, которые падают с ненулевым кодом на любую проблему (`-warnaserror`,
  `--max-warnings 0`, `tsc --noEmit`).
- Шлюз запускает платформа, а не агент: из `run()` проверки не вызывайте.
- Полный лог — `runs/<run_id>/logs/<name>.gate.log`; в `StageResult.gate.diagnostics`
  попадает усечённый хвост вывода каждой упавшей команды. Позже парсеры вывода
  (`gates/parsers/`) будут давать точные `Diagnostic` с файлом и строкой — для ремонта.
- У structured-агента схема уже проверена при разборе ответа; шлюз нужен, если есть
  дополнительные инварианты (например, `python -m json.tool out.json`).

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
| vLLM + Qwen3 | `LLM_CHAT_TEMPLATE_KWARGS=true`, `LLM_STRUCTURED_MODE=json_schema` |
| OpenAI | `LLM_BASE_URL=https://api.openai.com/v1`, `LLM_API_KEY=...` |
| Эндпоинт без `response_format` | `LLM_STRUCTURED_MODE=prompt` |

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

Ответ `FakeLLM` может быть функцией от сообщений: `FakeLLM([lambda msgs: "..."])`.
Фикстура `make_ctx` из `tests/conftest.py` собирает контекст во временном каталоге.

---

## 8. `AgentContext`

| Поле | Что это |
|---|---|
| `run_id` | Идентификатор запуска |
| `input_dir` | Корень входных артефактов |
| `output_dir` | Куда писать результаты |
| `workdir` | Служебный каталог запуска (логи, временные файлы) |
| `llm` | `LLMClient` — единственный способ вызвать модель |
| `sandbox` | Песочница для команд или `None` |
| `emit(event)` | Отправить `Event` в телеметрию: `ctx.emit(Event(kind="my.progress", message="..."))` |
| `budget_tokens` | Бюджет токенов этапа |

`LLMClient`: `chat(messages, temperature=, thinking=) -> ChatResponse`,
`structured(messages, Schema, ...) -> Schema`, `with llm.track() as t:` — токены блока в `t.usage`.

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
