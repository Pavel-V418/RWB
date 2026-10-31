# Этап 7 — security-контур (участник 5)

Сканеры → единые находки → привязка к SEC-требованиям → триаж (правила + LLM) → повторный скан →
отчёт с итоговым статусом. Только стандартная библиотека Python, сканеры — внешние программы.

```
tools/security/
  findings.py   модель находки, разбор SARIF (Semgrep, gitleaks, Trivy) и JSON OWASP ZAP
  classify.py   категория (сканер / CWE / слова) → SEC-n из requirements.json; политика блокировки
  triage.py     правила + LLM пачками; секреты в LLM не уходят; кэш вердиктов
  report.py     security.json (формат участника 2), findings.json, report.md; baseline → «исправлено»
  scan.py       запуск сканеров (отсутствующий — пропускается с пометкой)
  run.py        одна команда
  semgrep-rules/specforge.yaml  свои правила: секреты в логах, TLS отключён, cleartext HTTP, BODY-логи OkHttp
  gitleaks.toml исключения: .venv, build, team_inputs …
  test_security.py  7 тестов (без сканеров и LLM)
ci/security.gitlab-ci.yml   job'ы GitLab CI
```

## Запуск
```
py -m tools.security.run --target . --out artifacts/07_tests --requirements artifacts/01_requirements/requirements.json
```
- `--no-scan` — только разобрать готовые отчёты из `<out>/raw/` (так работает финальная задача в CI);
- `--tools semgrep,gitleaks,trivy`, `--images img1,img2` — Trivy по образам;
- LLM-триаж включается переменными `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` (с ноутбука — через SSH-туннель на vm5);
- код выхода 1, если есть блокирующие находки (`--no-fail` — всегда 0).

Выходы в `--out`: `security.json` (читает участник 2), `findings.json`, `report.md`, `triage_cache.json`, `raw/*.sarif|zap.json`.

## Политика
- **Блокируют сразу:** gitleaks (любой секрет), Trivy образа HIGH/CRITICAL.
- **Информируют:** Semgrep, Trivy fs, ZAP. После LLM-триажа подтверждённые high+ тоже блокируют.
- SEC-требование сопоставляется **по категории**, номер берётся из `requirements.json` (договорённость с участником 2).
- «Сканер ничего не нашёл» считается доказательством только для основных проверок категории:
  authn/authz/rate_limiting требуют контрактных тестов, audit — e2e. Иначе в отчёте ⚠️ или ⚪, а не ✅.

## Проверено (31.10, ноутбук)
- 7 тестов контура и совместимость с `team_inputs.py` участника 2: он читает `security.json` с SEC и статусами.
- Реальный скан проекта: Semgrep 1.183, gitleaks, Trivy. Находок 10. LLM-триаж (Qwen): 1 вызов, 3,7 тыс. токенов,
  ложных 8 из 10. Вердикты сверены с кодом вручную, все разумные. Повторный скан взял вердикты из кэша, 0 токенов.
- Реальная находка: сгенерированный `ApiClient.kt` (клиент участника 3) по умолчанию логирует `Level.BODY`.
  В приложении не активно (мы передаём свой OkHttp с BASIC), но шаблон генератора нужно поправить.

## Не проверено / ограничения
- `ci/security.gitlab-ci.yml` не запускался: GitLab ещё не выдан. Теги образов сканеров (gitleaks v8.28.0,
  trivy 0.67.2, semgrep 1.183.0) нужно проверить при первом прогоне.
- ZAP и Trivy по образам работают только в CI: нужны стенд `$STAND_URL` и образы `$API_IMAGE`, `$WEB_IMAGE`.
- Trivy fs не видит Android-зависимости без `gradle.lockfile`. Зависимости backend проверяются Trivy по образу.
- Backend-кода в репозитории пока нет: скан покрывал Android и Python-инструменты.
- SEC-01, 02, 06 (authn, authz, 429) закроют контрактные тесты (этап 5), SEC-04 (audit) — e2e (этап 6).
