# specforge-mobile — часть участника 5 (Android, тесты, безопасность)

Встроена в платформу так же, как части участников 2, 3 и 4 (`docs/AGENT_SDK.md` §13): пакет uv-workspace и
тонкие адаптеры в `agents/`. Исходное описание части — `docs/participant-5-integration.md`,
`docs/README-participant5.md`, отчёт `docs/participant5_report.pdf`.

| Агент платформы | Этап | Что вызывает |
|---|---|---|
| `android_coder` | `06_android` | `specforge_mobile.android_agent.run` (план → данные → экраны → навигация → шлюз Gradle → ремонт) |
| `qa` | `07_tests` | `specforge_mobile.qa.run` (security-контур, контракт и e2e на стенде, файлы CI продукта) |

## Состав

```
specforge_mobile/
  android_agent/   Android-агент; toolchain.py — где собирать (Docker-образ / локальный Gradle / нечем)
  tools/           android_gate (шлюз, скриншот-тесты из UX-спеки), contract, e2e, route_player, security
  qa.py            этап 07_tests целиком
android/           каркас Android (Kotlin 2.2, Compose, Material 3), AGENT_CONTRACT.md, frozen.sha256
reference/case1/   эталон кейса №1: сценарий маршрута, OpenAPI, черновик Android-спеки (не продукт)
ci/                шаблоны CI продукта для 07_tests (→ reports/tests/ опубликованного репозитория)
infra/android/     Dockerfile образа сборки specforge/android:jdk21-api36, smoke.sh
docs/              документы участника 5; original-ci/ — его CI-файлы до переноса в монорепозиторий
```

Python-пакет переименован `agents.android_agent` → `specforge_mobile.android_agent`, `tools.*` →
`specforge_mobile.tools.*` (иначе конфликт с `agents/` и `tools` платформы). Модель в конвейере — только
через `LLMClient` платформы (мост `agents/_team/mobile.py`); собственный клиент `android_agent/llm.py` —
для автономного CLI и CI продукта.

## Настройки хоста конвейера

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `SPECFORGE_ANDROID_GATE` | `auto` | `docker` / `local` / `none`; `auto`: образ есть → Docker, иначе JDK + `ANDROID_HOME` → локально, иначе код без сборки |
| `SPECFORGE_ANDROID_IMAGE` | `specforge/android:jdk21-api36` | образ сборки |
| `ANDROID_APP_ID` | `com.specforge.case1` | пакет Kotlin-клиента, который генерирует часть участника 3 |
| `SPECFORGE_QA_STAND` | `auto` | поднимать ли стенд из `08_infra` для контракта и e2e (`on` / `off`) |
| `STAND_URL` | `http://127.0.0.1:8080` | адрес стенда |
| `SPECFORGE_QA_USERS` | — | учётки контракта `роль=логин,…`; пароли — `CONTRACT_PASSWORD_<РОЛЬ>` |
| `E2E_SCENARIO` | — | сценарий e2e (`scenario.json`); для кейса №1 — `reference/case1/mock_route/scenario.json` |

Мобильное приложение строится, только если `01_requirements/profile.json → needs_mobile_app`; иначе этап
`android_coder` проходит без работы (отметка в `06_android/README.md`), документация не блокируется.

## Проверка

```bash
uv run pytest -q packages/specforge-mobile           # 42 теста, без Gradle, Docker и стенда
uv run python -m specforge_mobile.android_agent.run --in packages/specforge-mobile/reference/case1 \
    --out build/agent_smoke --replay                  # агент без LLM, нужен JDK + Android SDK или образ
docker build -f packages/specforge-mobile/infra/android/Dockerfile -t specforge/android:jdk21-api36 \
    packages/specforge-mobile && bash packages/specforge-mobile/infra/android/smoke.sh
```
