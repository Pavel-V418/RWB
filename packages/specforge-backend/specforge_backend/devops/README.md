# DevOps-агент

Поднимает стенд продукта и доводит его до зелёного шлюза «compose up + healthcheck».

```bash
python -m specforge_backend.devops.agent <artifacts> [--seed reference/case1/seed] [--no-generate] [--check /health/ready ...]
                                          [--max-attempts 3] [--down] [--no-llm]
```

| Шаг | Что происходит |
|---|---|
| генерация | детерминированно, `generators/gen-infra.sh`: `08_infra/docker-compose.yml`, `deploy.sh`, `db-init`, моки кейса, `.gitlab-ci.yml` |
| шлюз | `docker compose up --build --wait` (healthcheck каждого сервиса) + HTTP снаружи: `/health/ready`, `/swagger/v1/swagger.json`, `/metrics` → 200 |
| диагностика | статусы сервисов, хвост `compose up`, логи упавших/нездоровых сервисов; значения секретов из `.env` заменяются на `***` |
| ремонт `infra` | модель возвращает полные новые версии разрешённых файлов (`08_infra/docker-compose*.yml`, Dockerfile) и/или несекретные переменные `.env` |
| `app` | причина в коде или DDL: диагноз с логом → `08_infra/devops-feedback.txt` для `specforge_backend.backend.agent --feedback` |
| политика | SEC003: без `privileged`, `docker.sock`, `network_mode: host`, `pid: host`, `cap_add`; секреты и файлы вне списка не меняются |

Итог — `08_infra/gate.json` (`{gate: "stand", passed, errors}`), `08_infra/devops-agent.json` (попытки, диагнозы, токены),
ответы модели — `08_infra/.agent/`. Контракт команды: `run(input_dir, output_dir, llm) -> GateResult`.

CI продукта и MR на каждый этап — `templates/ci/gitlab-ci.yml`, `generators/stage-mr.sh` (см. `generators/README.md`).

Офлайн-тест (фейковая LLM, настоящий Docker, маленький стенд вместо продукта):
```bash
.venv/bin/python -m unittest tests.test_devops_agent
```
