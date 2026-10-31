# Android, тесты и безопасность SpecForge (участник 5): как устроено и как встроить в общий проект

Документ для команды: что делает моя часть, как она работает, какой файл за что отвечает и что нужно от
каждого участника, чтобы собрать всё в один конвейер. Подробности по этапам — `README_stage2…7.md`,
образ — `infra/images/android/README.md`, весь процесс и решения — `participant5_report.pdf`,
краткая сводка — `messages/svodka_uchastnik5.txt`.

**Статус на утро 31.10: роль готова на ~80%** (без репетиций и видео). Всё, что зависит только от меня, сделано
и проверено на живом стенде и настоящей модели. Остаток упирается в коллег и организаторов — см. раздел 5.

| Блок | Результат | Готово |
|---|---|---|
| Каркас Android + генератор скриншот-тестов | собирается, 21 тест, 7 снимков | 100% |
| Образ песочницы `specforge/android:jdk21-api36` | на vm5, без сети / 3 ГБ / не root — шлюз за 83 с | 100% |
| Mock-маршрут и эталон событий | по правилам backend участника 3 | 100% |
| Android-агент | на Qwen кейс №1 с нуля — со 2-й попытки; не перенесён на `ToolAgent` | ~70% |
| Контрактные тесты | на стенде 57 из 60, находки переданы | ~90% |
| e2e демо-сценарий | на стенде 14 из 16 (оба провала — находки backend), запускатель для CI готов | ~95% |
| Security-контур | реальный скан PASSED, отчёт по SEC; CI ещё не запускался (нет GitLab) | ~90% |

## 1. Что это и где место в конвейере

Конвейер: **ТЗ → анализ (2) → API и модель данных (2, 3) → экраны и веб-админка (4) → backend (3) и
Android (5) → тесты и безопасность (5) → сборка и деплой (3) → документация (2)**. Управляет оркестратор (1).

Моя часть отвечает за шесть вещей:

| Что | Вход | Выход |
|---|---|---|
| **Android-приложение** (каркас + Android-агент) | требования, `screen-spec.android.json` (4), Kotlin-клиент API (3) | Android-проект: экраны, тесты, APK, снимки `<screenId>--<state>--mobile.png` |
| **Mock-маршрут и эталон событий** | `scenario.json` + зоны из сида (3) | `points.json`, `route.geojson`, `expected_events.json` |
| **Контрактные тесты API** | `openapi.yaml` (3), стенд | `contract-junit.xml`, `raw/contract.sarif` |
| **e2e демо-сценарий** | стенд, маршрут, сценарий | `07_tests/e2e/route_e2e.py` (для CI) → `e2e-junit.xml`, `e2e.json`, `raw/e2e.sarif` |
| **Security-контур** | весь код, образы, стенд, `requirements.json → security` | `security.json`, `findings.json`, `report.md` |
| **Образ песочницы Android** | каркас | `specforge/android:jdk21-api36` |

Главный принцип: всё, что можно вывести из входов, делается **без LLM**. Без модели генерируются скриншот-тесты
(из UX-спеки), контрактные проверки (из OpenAPI), эталон событий (из сценария), проверки и отчёты. Модель
(Qwen) пишет только экраны Android и разбирает находки сканеров. Если модель недоступна, контур не падает:
находки остаются «требует проверки», ничего не блокируется ложно.

## 2. Как это работает по шагам

1. **Маршрут и эталон** (`tools/route_player/geo_scenario.py`): из `scenario.json` строится трек по улицам,
   по зонам (линия + радиус) вычисляются события по правилам backend (гистерезис 2 точки, выезд → закрытие
   сессии, смена зоны в течение 300 с). Доменные имена и правила живут только в `scenario.json`.
2. **Android-агент** (`agents/android_agent/`): копирует каркас и очищает `feature/`. Модель составляет план
   экранов (JSON), пишет слой данных, каждый экран (ViewModel, Screen, `<Pascal>ScreenSample`, тест) и навигацию.
   Затем генерируются скриншот-тесты из UX-спеки без LLM, выполняется предпроверка (без Gradle), шлюз
   (сборка, тесты, снимки) и ремонт: модели отдаются только упавшие файлы, до 3 раз. Запись вне `feature/`
   отклоняется, замороженные файлы ядра откатываются.
3. **Контрактные тесты** (`tools/contract/`) строятся из OpenAPI: 401 без токена и с поддельным, 403 по `x-roles`,
   чужой или несуществующий id, `Idempotency-Key`, 429 + `Retry-After`, отсутствие 5xx.
4. **e2e** (`tools/e2e/`): машина едет по маршруту на стенде, водитель начинает и продлевает сессию. Журнал
   (admin) сверяется с эталоном по типу, зоне, порядку и времени; также проверяются уведомления водителя,
   403 на журнал для водителя и аудит. Эталон строится по реальным зонам стенда (вокруг Невского их ~2000,
   строго проверяются сценарные), длительность оплаты подстраивается под правила зоны, перед стартом машина
   приводится в исходное состояние. В CI запускается через `artifacts/07_tests/e2e/route_e2e.py`.
5. **Security** (`tools/security/`): Semgrep (+ свои правила), gitleaks, Trivy (код и образы), ZAP (в CI).
   Находки приводятся к единому формату и привязываются к SEC-требованиям **по категории** (номер берётся из
   `requirements.json`). LLM-триаж секреты в модель не отправляет, а вердикты кэширует. Повторный скан помечает
   исправленное. Провал контракта или e2e — подтверждённая находка без LLM.

Каждый шаг — CLI-команда (`py -m …`), результаты — в формате, который читают участник 2 (трассируемость) и CI.

## 3. Какой файл за что отвечает

### Контракты и соглашения (менять только по согласованию)

| Файл | Что | С кем |
|---|---|---|
| `android/AGENT_CONTRACT.md` | что агенту можно и нельзя в Android-проекте, правила экранов | 1 |
| `android/frozen.sha256` | хеши замороженного каркаса (Gradle, манифест, `core/`) | 1 |
| `reference/case1/mock_route/scenario.json` | маршрут, правила зон, имена событий, теги FR/SEC шагов | 3 |
| `reference/case1/02_system/openapi.yaml` | API стенда (копия от участника 3) | 3 |
| `reference/case1/03_ux/screen-spec.android.json` | **черновик** Android-спеки, заменить спекой участника 4 | 4 |
| `tools/security/gitleaks.toml`, `tools/security/semgrep-rules/` | исключения и свои правила сканеров | 3 |

### Android

| Файл | Что |
|---|---|
| `android/` | каркас: Kotlin 2.2, Compose/Material 3, ручной DI, ядро `core/` (геолокация, трекинг, уведомления, таймер, `StateContainer`), эталон экранов кейса №1 в `feature/` |
| `android/gradle.properties` | параметры кейса: appId, `apiBaseUrl`, `mockRoute`, `timeScale`, лимиты памяти |
| `agents/android_agent/agent.py` | Android-агент: план → данные → экраны → навигация → шлюз → ремонт |
| `agents/android_agent/context.py` | сжатие входов для промптов (сигнатуры API и ядра, выжимка требований) |
| `agents/android_agent/prompts.py` | промпты |
| `agents/android_agent/workspace.py` | копия каркаса, запись файлов строго по контракту, откат ядра |
| `agents/android_agent/llm.py` | клиент OpenAI-совместимого API (повторы при 429 с `Retry-After`) |
| `agents/android_agent/replay.py` | «модель» без сети: отвечает эталоном (дымовая проверка) |
| `tools/android_gate/android_gate.py` | шлюз: Gradle → ошибки kotlinc / JUnit в едином формате |
| `tools/android_gate/screens.py` | скриншот-тесты из UX-спеки без LLM + проверки `check` / `verify` (`file:line: error:`) |
| `infra/images/android/` | `Dockerfile`, `smoke.sh` (проверка в условиях песочницы), README |

### Тесты и безопасность

| Файл | Что |
|---|---|
| `tools/route_player/geo_scenario.py`, `play_route.py` | генератор маршрута и эталона, плеер с ускорением |
| `tools/contract/` | `spec.py` (OpenAPI → запросы), `checks.py` (проверки), `run.py` (JUnit, SARIF) |
| `tools/e2e/` | `oracle.py` (эталон под стенд, сверка журнала), `run.py` (сценарий на стенде) |
| `artifacts/07_tests/e2e/route_e2e.py` | запускатель для стадии e2e: `python3 <файл> --base $STAND_URL`, сам находит `tools/e2e/` и `01_requirements/` |
| `tools/security/` | `findings.py` (SARIF/ZAP → находки), `classify.py` (категория → SEC, политика), `triage.py`, `report.py`, `scan.py`, `run.py` |
| `ci/tests.gitlab-ci.yml` | `tests:contract` (стадия e2e, тег `vm5`) |
| `ci/security.gitlab-ci.yml` | gitleaks, Semgrep, Trivy fs и образов, ZAP, итоговый `security:report` |

### Автотесты моей части (36, все зелёные)

| Файл | Тестов | Что проверяет |
|---|---|---|
| `agents/android_agent/test_android_agent.py` | 10 | разбор ответов модели, контракт путей, откат ядра, цикл ремонта, предпроверка снимков |
| `tools/security/test_security.py` | 7 | разбор всех сканеров, SEC-привязка, триаж без утечки секретов, baseline, совместимость с участником 2 |
| `tools/contract/test_contract.py` | 1 | фейковый API с заложенными уязвимостями: ловит все, без ложных срабатываний |
| `tools/e2e/test_e2e.py` | 5 | эталон под правила backend, подстройка под зону, сверка журнала |
| `tools/android_gate/test_android_gate.py` | 9 | разбор логов Gradle, kotlinc, JUnit, контракт замороженных файлов |
| `tools/android_gate/test_screens.py` | 4 | генерация скриншот-тестов, ошибки на Kotlin-файл экрана |

```
py -m unittest agents.android_agent.test_android_agent tools.security.test_security tools.contract.test_contract tools.e2e.test_e2e
py tools/android_gate/test_android_gate.py
py tools/android_gate/test_screens.py
```

### Документы и передача

| Файл | Что |
|---|---|
| `participant-5-integration.md` | этот документ — начинать с него |
| `participant5_report.pdf` | весь процесс: этапы, проблемы и решения, находки, что осталось, команды |
| `messages/svodka_uchastnik5.txt` | короткая сводка для пересылки |
| `README_stage2…7.md`, `infra/images/android/README.md` | подробности по этапам и образу |
| `participant5_android_tests_security.zip` | всё вышеперечисленное одним архивом (176 файлов, без секретов и мусора сборки) |

## 4. Как встроить в общий проект

### Участник 1 — оркестратор и платформа

- **Android-этап под `ToolAgent`.** Каркас уйдёт в `agents/android_coder/template/`, промпты — в
  `prompts/system.md` и `task.md`. **Блокер: нужен `git push main` с `specforge.core` и `docs/AGENT_SDK.md`.**
  До этого работает мой собственный цикл (`py -m agents.android_agent.run`), проверенный на Qwen.
- **Образ песочницы** `specforge/android:jdk21-api36` уже собран в Docker на vm5. Проверен без сети, с 3 ГБ
  памяти, 512 процессами, не от root: шлюз проходит за 83 с. Переменную `JAVA_TOOL_OPTIONS` не перезаписывать.
- **gate_commands** (работают без сети):
  ```
  python3 /opt/specforge/screens.py . --spec <ux-спека> generate
  python3 /opt/specforge/screens.py . --spec <ux-спека> check
  ./gradlew --no-daemon --offline assembleDebug testDebugUnitTest recordRoborazziDebug
  python3 /opt/specforge/screens.py . --spec <ux-спека> verify
  ```
  `recordRoborazziDebug`, а не `verify` (согласовано): у новых экранов нет эталонов. JUnit XML — в стандартном
  `app/build/test-results/`.
- **Таблица «этап → папка».** Мои этапы: `android`, `tests`, `security`. Входы Android: требования,
  `screen-spec.android.json`, Kotlin-клиент из `codegen`. Сейчас пути по старой схеме (`01_…`, `07_tests`) —
  переименую под таблицу из `AGENT_SDK.md`.

### Участник 2 — анализ и трассируемость

- Читаю `requirements.json → security[]` (категории `authn`, `authz`, `privacy`, `audit`, `input_validation`,
  `rate_limiting`, `secrets`) и FR из `requirements.json`. Если категории не изменятся, номера SEC могут меняться.
- Отдаю: `contract-junit.xml`, `e2e-junit.xml` (теги `FR-n`/`SEC-n` в имени теста), `security.json`
  (поле `req`, статусы `open|fixed|false_positive|accepted`). Совместимость с `team_inputs.py` проверена тестом.
- В Kotlin тесты помечаются `@Req("FR-n")` + тег в имени теста.

### Участник 3 — backend, DevOps, CI

- **Стенд и учётки.** e2e и контракт используют `driver.demo` и `admin`. Пароли — только masked-переменные
  `CONTRACT_PASSWORD_DRIVER`, `CONTRACT_PASSWORD_ADMIN`.
- **CI.** Файлы `ci/tests.gitlab-ci.yml` и `ci/security.gitlab-ci.yml` подключаются через include + exists.
  Раннер shell, тег `vm5`, сканеры запускаются через `docker run`. Нужно: `needs: backend:image` (уже указан),
  `$STAND_URL`, `$API_IMAGE`, `$WEB_IMAGE`, masked `LLM_BASE_URL/LLM_MODEL/LLM_API_KEY`.
  Проверить, что на vm5 скачивается база Trivy.
- **e2e.** Запускатель уже лежит в `artifacts/07_tests/e2e/route_e2e.py` (запуск `python3 <файл> --base $STAND_URL`,
  он сам находит `tools/e2e/` и пишет результаты в `07_tests/`). Он использует ту же
  демо-машину, что `demo_scenario.py`: **запускать не одновременно**. Между прогонами нужна пауза около 5 мин
  (окно смены зоны 300 с), скрипт выжидает её сам.
- **Kotlin-клиент** для Android-агента — из `codegen` (пакет `…core.network.generated`). Агент находит его сам,
  для другого кейса: `--api-client <папка>`.
- **Открытые находки** — в `messages/to_uchastnik3_contract.txt` и `to_uchastnik3_e2e.txt`:
  - нет лимита на `/auth/tokens`;
  - `active-session` отвечает 204 вместо 404;
  - нет пользователя в событиях сессии (SEC-04);
  - разный регистр полей в payload;
  - нет `session_paid`;
  - таймеры не ускорены `TIME_SCALE`;
  - `Level.BODY` в шаблоне Kotlin-клиента.

### Участник 4 — UX и веб

- **Android-спека** `03_ux/screen-spec.android.json` по общей схеме `contracts/ux/screen-spec.schema.json`.
  Мне нужны `screenId`, `states`, элементы, навигация. Пока работаю на своём черновике.
- **Снимки для UI-ревьюера** уже в его формате: `<screenId>--<state>--mobile.png` (Roborazzi, без эмулятора).
  testTag: `<screen>`, `<screen>-state-<state>`. Добавлю `<screen>-action-<id>`, `<screen>-field-<name>`,
  `<screen>-submit`, как в вебе.
- **Манифест Material 3** (`design-system/material3/kit-manifest.json`) — мой, дополню компонентами.
- **Веб e2e** (`05_web/admin/e2e/scenarios.spec.ts`, теги `@FR-xx`) можно включить в общий отчёт тестов
  рядом с моими JUnit.

## 5. Порядок стыковки (что осталось)

Уже сделано без коллег: каркас, образ, маршрут, агент на Qwen, контракт и e2e на стенде, security-скан,
CI-файлы, запускатель e2e.

**Закрыто 31.10:** ✅ повторный e2e после правок (через запускатель CI: 14 из 16, оба провала — находки backend);
✅ сценарий положен в `07_tests/e2e/` (`artifacts/07_tests/e2e/route_e2e.py`, проверен на стенде).

Дальше — по зависимостям:

1. Участник 1 делает push → переношу агента на `ToolAgent`, прогон через `uv run specforge agent run android_coder`.
2. Фиксируем таблицу «этап → папка» → переименовываю пути входов и выходов в своих инструментах и CI.
3. Участник 4 даёт `screen-spec.android.json`, участник 3 — Kotlin-клиент из `codegen` → прогон агента на
   кейсе №1 и на репетиционном ТЗ (case3 или case4).
4. Выдан GitLab → первый прогон CI: `security:*`, `tests:contract`, e2e. Правки окружения.
5. Участник 3 исправляет находки (аудит, `session_paid`, лимит входа) → повторный e2e и контракт → SEC-04 и
   уведомления зелёные.
6. Финальный прогон кейса №1 всей командой, видео (эмулятор на моём ноутбуке недоступен — снимаем на
   реальном Android-телефоне или эмуляторе коллеги).

## 6. Что проверено и что нет

**Проверено:**
- **Android-агент на Qwen3.8-27B:** кейс №1 с нуля проходит со 2-й попытки (26 тестов, 7 снимков,
  ~62 тыс. токенов).
- **Образ песочницы** на vm5 в условиях песочницы.
- **Контракт на стенде:** 60 проверок, 57 прошли.
- **e2e на стенде** через запускатель CI (`artifacts/07_tests/e2e/route_e2e.py`): 14 из 16 шагов. Оба провала —
  находки backend (нет пользователя в событиях сессии — SEC-04, нет уведомления `session_paid`).
- **Security-скан нашего кода:** PASSED, вердикты LLM сверены вручную.
- Все автотесты моей части; совместимость `security.json` с модулем участника 2.

**Не проверено (нужны части коллег):**
- агент под `ToolAgent` платформы участника 1;
- CI-файлы в настоящем GitLab;
- ZAP и Trivy по образам (только в CI);
- агент на чужом ТЗ;
- предупреждение «за 10 минут» в e2e — таймеры стенда не ускорены;
- лимит на `/positions` (нужна тестовая машина).

**Ограничения:**
- эмулятор Android на ноутбуке не запускается: SVM выключен в BIOS;
- в коде инструментов нет доменных слов — всё доменное лежит в `scenario.json` и спеках.
