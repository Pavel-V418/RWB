# Этап 5 — контрактные тесты API (участник 5)

Проверки строятся из OpenAPI без LLM, поэтому подходят для любого кейса. Код в `tools/contract/`:
`spec.py` (разбор спеки, запросы по схеме), `checks.py` (проверки), `run.py` (запуск, JUnit, SARIF),
`test_contract.py` (фейковый API с заложенными дефектами).

| Проверка | Категория SEC |
|---|---|
| защищённая операция без токена и с поддельным токеном → 401 | authn |
| роль не из `x-roles` → 403 | authz |
| несуществующий или чужой id в пути → 403/404, а не 2xx | authz |
| `x-idempotent` без `Idempotency-Key` → 400/422; повтор с тем же ключом → тот же ответ | input_validation |
| всплеск запросов → 429 с `Retry-After` (операции с лимитом в описании или `--rate-op`) | rate_limiting |
| ни одного 5xx | input_validation |

## Запуск
```
$env:CONTRACT_PASSWORD_DRIVER="…"; $env:CONTRACT_PASSWORD_ADMIN="…"   # пароли только в окружении
py -m tools.contract.run --openapi reference/case1/02_system/openapi.yaml --base http://localhost:8080 `
   --out artifacts/07_tests --user driver=driver.demo --user admin=admin `
   --requirements artifacts/01_requirements/requirements.json
```
Стенд с ноутбука доступен через туннель: `ssh -p 10005 -N -L 8080:127.0.0.1:8080 team5@85.143.113.230`.
Выходы: `contract-junit.xml` (участник 2, теги SEC/FR в имени теста), `raw/contract.sarif` (security-контур:
провал = подтверждённая находка без LLM), `contract.json`. CI: `ci/tests.gitlab-ci.yml`.

## Результат на стенде (31.10)
60 проверок, 57 прошли.
- **Нет лимита на `POST /auth/tokens`:** 150 неверных паролей подряд, ни одного 429 (SEC-01, SEC-06).
- **`active-session` для несуществующей машины отвечает 204, а не 404:** нет проверки существования и владельца.
  Настоящий IDOR не проверить: на стенде одна машина, и она водителя. Нужна вторая машина в сиде.
- В отчёте безопасности: SEC-01 и SEC-05 ✅, SEC-02 и SEC-06 ❌ (см. выше), SEC-04 ждёт e2e.

## Ограничения
- Лимит на `POST /positions` всплеском не проверяется, чтобы не засорить трек демо-машины.
- Schemathesis (фаззинг схем) не подключён; проверки ответов на соответствие схеме — следующий шаг.
