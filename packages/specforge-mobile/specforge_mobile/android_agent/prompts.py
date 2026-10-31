"""Промпты Android-агента. Доменных слов нет — всё приходит из входов."""
from __future__ import annotations

import json
import re

OUTPUT_RULES = """\
ФОРМАТ ОТВЕТА — только блоки файлов, без пояснений до и после:
<<<FILE путь/от/корня/android-проекта>>>
полное содержимое файла
<<<END>>>
Каждый файл — целиком (не diff, не «...»). Пути — только разрешённые контрактом."""


def _pascal(screen_id: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", screen_id) if p)


def system_prompt(contract: str, core: str, libs: str) -> str:
    return f"""Ты — ведущий Android-разработчик (Kotlin 2.2, Jetpack Compose, Material 3, Navigation Compose,
корутины, Retrofit). Ты пишешь слой экранов (feature/) поверх готового каркаса. Каркас и ядро менять нельзя.

{contract}

Доступные библиотеки (новые добавлять нельзя): {libs}

Публичный API ядра (пакет com.specforge.app.core.*), используй его, а не своё:
```kotlin
{core}
```

Требования к коду:
- Код должен компилироваться с первого раза: все импорты явные, никаких несуществующих API.
- Только классы, которые реально есть в клиенте API и в ядре. Не выдумывай поля моделей.
- Без Hilt/kapt/ksp/рефлексии. ViewModel — через конструктор, в AppRoot: viewModel {{ Xxx(...) }}.
- Тексты — на языке требований. Пользователю — понятные сообщения, без стектрейсов.
- Не логируй токены, пароли и персональные данные."""


def plan_prompt(spec_raw, screens: list[dict], requirements: str, api: str) -> str:
    return f"""Составь план слоя экранов по UX-спеке и требованиям.

UX-спека (экраны и состояния — обязательны все):
```json
{json.dumps(spec_raw, ensure_ascii=False)[:9000]}
```

Требования:
{requirements}

Клиент API:
```kotlin
{api}
```

Ответь ТОЛЬКО JSON без markdown:
{{
  "startScreen": "<screenId первого экрана>",
  "repositories": [{{"name": "XxxRepository", "package": "<подпакет feature>",
                    "methods": ["suspend fun ...: ApiResult<...>"], "api": ["XxxApi.method"]}}],
  "screens": [{{"screenId": "...", "package": "<подпакет feature, латиницей>", "repository": "XxxRepository",
               "states": ["ready", ...], "requirements": ["FR-1"], "actions": ["что делает пользователь"],
               "navigatesTo": ["screenId"]}}],
  "positionSink": "<какой класс реализует PositionSink или null, если геолокация в ТЗ не нужна>"
}}
Экраны: {", ".join(s["screenId"] for s in screens)}. Каждый screenId из спеки должен быть в плане."""


def data_prompt(plan: dict, api: str, reference: str) -> str:
    return f"""Шаг 1 из 3: слой данных.
Напиши по плану:
- модели экранов и интерфейсы репозиториев (не зависят от сгенерированного клиента);
- реализации репозиториев поверх клиента API (через apiCall {{ }} → ApiResult);
- feature/wiring/GeneratedApi.kt — создание сервисов клиента;
- feature/FeatureModule.kt — class FeatureModule(container: AppContainer) с полем positionSink
  (если геолокация в ТЗ не нужна — val positionSink: PositionSink = object : PositionSink {{
  override suspend fun send(batch: List<GeoFix>): List<Alert> = emptyList() }});
- примерные данные каждого экрана — object <Pascal>Samples в файле feature/<пакет>/<Pascal>Samples.kt (main);
- фейки репозиториев для тестов в {"app/src/test/kotlin/com/specforge/app/feature"}/, данные берут из <Pascal>Samples:
  каждый метод записывает вызовы (xxxCalls), результат настраивается (xxxResult), а действия можно
  «подвесить» через var xxxGate: CompletableDeferred<Unit>? — для теста повторного нажатия.

План:
```json
{json.dumps(plan, ensure_ascii=False)}
```

Клиент API:
```kotlin
{api}
```

Образец из другого кейса (повторяй устройство, но не доменные сущности):
{reference}

{OUTPUT_RULES}"""


def screen_prompt(screen: dict, plan: dict, outline: str, reference: str, requirements: str) -> str:
    sid = screen["screenId"]
    return f"""Шаг 2: экран «{sid}».
Напиши:
- ViewModel (UiState<...>, загрузка, действия, защита от двойного нажатия, новый Idempotency-Key на нажатие);
- {sid}Route(vm, ...) + stateless {_pascal(sid)}Screen(state, callbacks) внутри StateContainer("{sid}", ...);
- strings_{sid}.xml;
- в feature/<пакет>/{_pascal(sid)}Samples.kt (верни файл целиком, сохранив данные) —
  @Composable fun {_pascal(sid)}ScreenSample(state: String): when(state) с ветками ровно для
  {", ".join(f'"{st}"' for st in screen.get("states", []))} → {_pascal(sid)}Screen(...) на примерных данных и пустых колбэках.
  Скриншот-тесты НЕ пиши: их генерирует конвейер из UX-спеки, вызывая эту функцию;
- unit-тест ViewModel: успех, ошибка, пустое состояние, forbidden; @Req("FR-n") и тег в имени теста.
  Тест «повторное нажатие игнорируется» — только с подвешенным вызовом фейка (xxxGate = CompletableDeferred()),
  как в образце; если в фейке нет такого поля — добавь его и верни файл фейка целиком.
Состояния экрана: {", ".join(screen.get("states", []))}. testTag интерактивных элементов: "{sid}-<элемент>".

Экран в плане:
```json
{json.dumps(screen, ensure_ascii=False)}
```
Весь план (для навигации): экраны {", ".join(s["screenId"] for s in plan.get("screens", []))}.

Требования (выжимка):
{requirements[:2500]}

Уже написано (сигнатуры, используй их как есть):
```kotlin
{outline}
```

Образец экрана из другого кейса:
{reference}

{OUTPUT_RULES}"""


def wiring_prompt(plan: dict, screens: list[dict], outline: str, reference: str) -> str:
    return f"""Шаг 3: сборка приложения.
Напиши feature/AppRoot.kt — @Composable fun AppRoot(container: AppContainer): NavHost, маршруты = screenId
({", ".join(s["screenId"] for s in screens)}), старт: "{plan.get("startScreen", screens[0]["screenId"])}";
если есть вход — после него переход дальше. Скриншот-тесты не пиши — их генерирует конвейер.

Уже написано (сигнатуры):
```kotlin
{outline}
```

Образец:
{reference}

{OUTPUT_RULES}"""


def repair_prompt(errors: list[dict], files: str, outline: str, attempt: int, first_file: str = "") -> str:
    lines = []
    for e in errors:
        loc = f"{e.get('file')}:{e.get('line')}:{e.get('column')}" if e.get("file") else "(без файла)"
        lines.append(f"- [{e.get('category')}] {loc} {e.get('message')}")
        if e.get("code_window"):
            lines.append("```\n" + e["code_window"] + "\n```")
    return f"""Ремонт, попытка {attempt}. Сборка/тесты упали.
Сначала реши, где ошибка: в коде экрана или в самом тесте/фейке. Если тест некорректен (например, фейк
отвечает мгновенно, и «повторное нажатие во время запроса» невозможно воспроизвести) — исправь тест и фейк
(подвесь вызов через CompletableDeferred). Удалять проверку требования и ослаблять ожидания нельзя.

Ошибки:
{chr(10).join(lines)}

Файлы с ошибками:
{files}

Остальные файлы feature/ (сигнатуры):
```kotlin
{outline}
```

Верни исправленные файлы целиком. Можно вернуть и другие файлы feature/, если причина в них.
Если вызываешь метод, которого нет в интерфейсе (см. сигнатуры), — либо используй существующий,
либо верни и исправленный файл интерфейса/фейка.
{OUTPUT_RULES}
{f"Начни ответ ровно со строки: <<<FILE {first_file}>>>" if first_file else ""}"""
