# Участник 5 — Android, тесты и безопасность: пакет для интеграции

Этот архив — вся моя часть SpecForge. По нему её можно встроить в общий репозиторий без меня.

**Порядок чтения:**
1. этот файл — что поставить, куда разложить, как проверить;
2. [`participant-5-integration.md`](participant-5-integration.md) — как всё устроено, какой файл за что отвечает,
   что нужно от каждого участника;
3. `participant5_report.pdf` — история работы, решения, находки по стенду (по желанию).

## 1. Что в архиве

```
README.md                         ← вы здесь
participant-5-integration.md      архитектура, файлы, связи с участниками 1–4
participant5_report.pdf           отчёт о работе
requirements-participant5.txt     Python-зависимости
android/                          каркас Android (Kotlin, Compose), эталон экранов кейса №1, AGENT_CONTRACT.md
agents/android_agent/             Android-агент (план → экраны → шлюз → ремонт), клиент LLM
tools/android_gate/               шлюз Gradle + генератор скриншот-тестов из UX-спеки (screens.py)
tools/route_player/               генератор mock-маршрута и эталона событий, плеер
tools/contract/                   контрактные тесты API из OpenAPI
tools/e2e/                        e2e демо-сценарий на стенде
tools/security/                   security-контур: сканеры → SEC-требования → триаж → отчёт
ci/                               tests.gitlab-ci.yml, security.gitlab-ci.yml
artifacts/07_tests/e2e/           route_e2e.py — запускатель e2e для стадии e2e CI
infra/images/android/             Dockerfile образа песочницы, smoke.sh
reference/case1/                  эталон кейса №1: сценарий маршрута, openapi (копия участника 3), черновик UX-спеки
messages/svodka_uchastnik5.txt    короткая сводка
README_stage2…7.md                подробности по этапам
```

## 2. Что поставить

| Что | Зачем | Примечание |
|---|---|---|
| Python 3.10+ | все инструменты | почти всё на стандартной библиотеке |
| `pip install -r requirements-participant5.txt` | `pyyaml` для openapi.yaml | без него контракт можно запускать по `swagger.json` стенда |
| JDK 17+ (рекомендую 21) | сборка Android | **JDK 25 из Android Studio не подходит** для Gradle 8.14.3 |
| Android SDK: platform 36, build-tools 35 и 36 | сборка Android | `ANDROID_HOME` или `android/local.properties` с `sdk.dir=…` |
| Docker | образ песочницы, сканеры в CI | на vm5 есть; образ там уже собран |
| semgrep, gitleaks, trivy | security локально | необязательно: в CI запускаются через `docker run`; отсутствующий сканер пропускается |

## 3. Переменные окружения (секреты — только так, в файлах их нет)

| Переменная | Для чего |
|---|---|
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | Android-агент и LLM-триаж находок (OpenAI-совместимый `/chat/completions`) |
| `CONTRACT_PASSWORD_DRIVER`, `CONTRACT_PASSWORD_ADMIN` | контракт и e2e на стенде (учётки `driver.demo`, `admin`) |
| `STAND_URL`, `API_IMAGE`, `WEB_IMAGE` | CI (задаёт участник 3) |
| `JAVA_HOME` | если `java` нет в PATH |

С ноутбука LLM (10.10.10.10:4000) и стенд доступны только через SSH-туннель на vm5:
`ssh -p 10005 -N -L 4000:10.10.10.10:4000 -L 8080:127.0.0.1:8080 team5@85.143.113.230`.

## 4. Куда разложить в общем репозитории

Все модули импортируются как `tools.…` и `agents.…`, поэтому **запускать их из корня репозитория**, а `tools/`
и `agents/` класть в корень — туда же, где лежат `ci/` и `07_tests/` (CI выполняет `python3 -m tools…`).

| Из архива | Куда | Как будет после push участника 1 |
|---|---|---|
| `tools/`, `agents/` | корень репозитория (с `__init__.py`) | агент переезжает в `agents/android_coder/` под `ToolAgent` |
| `android/` | корень (`android/`) | каркас → `agents/android_coder/template/` (сборка образа: `--build-arg TEMPLATE_DIR=…`) |
| `ci/*.gitlab-ci.yml` | `ci/` в корне — участник 3 подключает их через include + exists | без изменений |
| `artifacts/07_tests/e2e/route_e2e.py` | `07_tests/e2e/` репозитория артефактов | сам ищет `tools/e2e/` в родительских папках |
| `infra/images/android/` | `infra/images/android/` | без изменений |
| `reference/case1/` | `reference/case1/` (эталон кейса №1, не продукт) | без изменений |

Пути `01_requirements/`, `07_tests/` — старая схема. Когда участник 1 зафиксирует таблицу «этап → папка»
в `AGENT_SDK.md`, их нужно поменять в `ci/*.gitlab-ci.yml` (переменные `SECURITY_OUT`, `TESTS_OUT`,
`REQUIREMENTS_JSON`, `OPENAPI_YAML`) и в аргументах запуска. В коде инструментов пути не зашиты.

## 5. Проверка после интеграции (по порядку)

| # | Команда (из корня) | Ожидаемо |
|---|---|---|
| 1 | `py -m unittest agents.android_agent.test_android_agent tools.security.test_security tools.contract.test_contract tools.e2e.test_e2e` | `Ran 22 tests … OK` (`skipped=1` — тест совместимости с модулем участника 2, если его `specforge_analysis` не лежит в `team_inputs/`) |
| 2 | `py tools/android_gate/test_android_gate.py` и `py tools/android_gate/test_screens.py` | `9 тестов пройдено`, `4 тестов пройдено` |
| 3 | `cd android` → `gradlew assembleDebug testDebugUnitTest recordRoborazziDebug` | `BUILD SUCCESSFUL`, 7 PNG в `android/screenshots/` |
| 4 | `py -m agents.android_agent.run --in reference/case1 --out build/agent_smoke --replay` | `Android-агент: ПРОЙДЕН` (без LLM, ~1–2 мин) |
| 5 | `py -m tools.security.run --target . --out build/sec --requirements <requirements.json участника 2> --no-llm --no-fail` | `build/sec/report.md` со статусом и таблицей по SEC |
| 6 | на vm5: `docker build -f infra/images/android/Dockerfile -t specforge/android:jdk21-api36 .` → `bash infra/images/android/smoke.sh` | `ГОТОВО … снимков: 7` (образ уже есть на vm5) |
| 7 | с туннелем: `py -m tools.contract.run --openapi reference/case1/02_system/openapi.yaml --base http://localhost:8080 --out build/contract --user driver=driver.demo --user admin=admin` | 60 проверок, 3 известных провала |
| 8 | с туннелем: `py artifacts/07_tests/e2e/route_e2e.py --base http://localhost:8080` | 16 шагов, 2 известных провала (находки backend) |
| 9 | с LLM: `py -m agents.android_agent.run --in reference/case1 --out build/agent_case1` | `ПРОЙДЕН` (обычно со 2-й попытки, ~60 тыс. токенов) |

Шаги 1, 2, 4 и 5 проверены на чистой распакованной копии этого архива: без `JAVA_HOME` и `local.properties`
агент сам находит JDK и Android SDK, шаг 4 проходит за ~35 с.

**Windows:** распаковывайте в папку с коротким путём (например `C:\work\participant5`). Внутри Android-проекта
пути длинные, и при глубокой папке упрётесь в лимит Windows 260 символов (`WinError 3`). Альтернатива —
включить длинные пути в Windows.

## 6. Правила, которые нельзя нарушать

- **Секреты** только в переменных окружения и masked-переменных CI. Пароли учёток стенда — у участника 3.
- **Ядро Android заморожено** (`android/frozen.sha256`). После осознанной правки `core/` или Gradle-файлов:
  `py tools/android_gate/android_gate.py android --freeze`. Агенту туда писать нельзя: шлюз вернёт ошибку `contract`.
- В шлюзе — **`recordRoborazziDebug`, а не `verify`**: у новых экранов нет эталонов. Скриншот-тесты пишет не агент,
  а `screens.py` из UX-спеки.
- **e2e и `demo_scenario.py` участника 3 нельзя запускать одновременно**: у них одна демо-машина. Пауза между
  прогонами (~5 мин) выдерживается автоматически.
- В образе не перезаписывать `JAVA_TOOL_OPTIONS=-Duser.home=/opt/home`: там кэш Robolectric.
- Код инструментов универсальный: всё доменное — только в `reference/` (сценарий, спеки).

## 7. Что осталось и от кого зависит

Подробно — `participant-5-integration.md`, раздел 5. Коротко:
1. Перенести агента на `ToolAgent` — после `git push main` участника 1.
2. Первый прогон CI — когда выдадут GitLab.
3. Агент на чужом ТЗ — когда будут UX-спека (участник 4) и Kotlin-клиент (участник 3).
4. Повторить контракт и e2e после исправлений участника 3 (лимит входа, аудит, `session_paid`).
