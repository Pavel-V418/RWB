"""Паспорт сервиса backend по шаблону организаторов «Описание сервисов» и ADR платформы — без LLM.

    python generators/docs/gen_docs.py <artifacts_dir>

Факты берутся из артефактов продукта: 02_system/openapi.yaml (назначение, роли, операции), 08_infra/docker-compose*.yml
(сервисы, лимиты памяти, порты, переменные окружения), .gitlab-ci.yml (стадии), 04_backend (топики событий, интеграции).
Выход: 08_infra/services/backend-api.md и 08_infra/adr/*.md (копия templates/docs/adr). Одинаковый вход — одинаковый выход.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ADR_TEMPLATES = ROOT / "templates/docs/adr"

# Что делает каркас (templates/backend-dotnet) — одинаково для всех кейсов
HEALTH_CHECKS = [
    ("postgres", "ready", "Unhealthy", "нет соединения с PostgreSQL"),
    ("kafka", "ready", "Degraded", "брокер недоступен: API работает, события копятся в outbox"),
    ("outbox", "business", "Degraded", "события ждут публикации дольше порога или уходят в DLQ"),
    ("jobs", "business", "Degraded", "фоновая задача (таймер предметной области) падает 3 прохода подряд"),
]
APP_METRICS = [
    ("app_outbox_published", "события, опубликованные в Kafka"),
    ("app_outbox_publish_failures", "неудачные попытки публикации"),
    ("app_outbox_dead_lettered", "события, отправленные в DLQ"),
    ("app_job_runs", "проходы фоновых задач"),
    ("app_job_failures", "упавшие проходы фоновых задач"),
    ("http_requests_received_total, http_request_duration_seconds", "запросы и время ответа по маршрутам (prometheus-net)"),
    ("healthcheck (ForwardToPrometheus)", "состояние каждой health-проверки"),
]
# Описание переменных backend (ключи берутся из compose, описание — отсюда)
ENV_DOCS = {
    "ASPNETCORE_ENVIRONMENT": "окружение ASP.NET (Production на стенде)",
    "ConnectionStrings__Database": "строка соединения с PostgreSQL (пароль — из секрета POSTGRES_PASSWORD)",
    "Kafka__Enabled": "публикация событий в Kafka",
    "Kafka__BootstrapServers": "адрес брокера Kafka",
    "Kafka__TopicPrefix": "префикс топиков (разделение окружений на одном брокере)",
    "Jwt__SigningKey": "ключ подписи JWT (секрет JWT_SIGNING_KEY, ≥ 32 байт)",
    "OpenApi__Enabled": "Swagger UI и /swagger/v1/swagger.json (contract_diff)",
    "TIME_SCALE": "ускорение времени для демо таймеров (1 — реальное время)",
    "Cors__AllowedOrigins__0": "разрешённый origin web-админки",
    "DOTNET_gcServer": "0 — workstation GC, меньше памяти для одного экземпляра",
}
CI_VARS = [
    ("STAND_POSTGRES_PASSWORD", "секрет", "пароль БД стенда (иначе генерируется один раз в ~/.specforge-stand.env на VM)"),
    ("STAND_JWT_SIGNING_KEY", "секрет", "ключ подписи JWT стенда (аналогично)"),
    ("STAND_POSTGRES_PORT", "15432", "порт PostgreSQL на хосте VM (5432 занят системным PostgreSQL)"),
    ("STAND_EXTRA_ENV", "—", "дополнительные строки .env стенда, например TIME_SCALE=120"),
    ("API_IMAGE", "specforge/backend:$CI_COMMIT_SHORT_SHA", "образ backend; задаётся в .gitlab-ci.yml"),
    ("WEB_IMAGE", "specforge/web:$CI_COMMIT_SHORT_SHA", "образ web-админки"),
    ("STAND_URL", "http://127.0.0.1:8080", "адрес стенда для e2e (раннер на той же VM)"),
]


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


def mem_mb(value: str) -> int:
    m = re.search(r"(\d+)\s*([mMgG])", str(value))
    if not m:
        return 0
    return int(m.group(1)) * (1024 if m.group(2).lower() == "g" else 1)


def resolve(value) -> str:
    """'${BACKEND_MEM:-384m}' → '384m' (значение по умолчанию из compose)."""
    return re.sub(r"\$\{\w+:-([^}]*)\}", r"\1", str(value))


def compose_services(infra: Path) -> dict:
    services: dict = {}
    for name in ("docker-compose.yml", "docker-compose.override.yml"):
        for svc, spec in (load_yaml(infra / name).get("services") or {}).items():
            merged = services.setdefault(svc, {"environment": {}, "ports": [], "profiles": []})
            env = spec.get("environment") or {}
            if isinstance(env, list):
                env = dict(e.split("=", 1) for e in env)
            merged["environment"].update(env)
            merged["ports"] += spec.get("ports") or []
            merged["profiles"] += spec.get("profiles") or []
            for key in ("image", "build"):
                if key in spec:
                    merged[key] = spec[key]
            limit = (((spec.get("deploy") or {}).get("resources") or {}).get("limits") or {}).get("memory")
            if limit:
                merged["memory"] = resolve(limit)
    return services


def env_value(key: str, value) -> str:
    if re.search(r"password|key|secret|token", key, re.IGNORECASE) or "PASSWORD" in str(value):
        return "секрет"
    value = resolve(value)
    return f"`{value}`" if value else "пусто"


def provider_doc(key: str) -> str:
    m = re.fullmatch(r"Providers__(\w+)__BaseUrl", key)
    return f"адрес внешнего провайдера {m.group(1)} (на стенде — мок)" if m else ""


def table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in rows]
    return "\n".join(lines)


def operations(spec: dict) -> list[dict]:
    ops = []
    for path, item in (spec.get("paths") or {}).items():
        for method, op in item.items():
            if method in ("get", "post", "put", "patch", "delete") and isinstance(op, dict):
                ops.append({"method": method.upper(), "path": path, "roles": op.get("x-roles"),
                            "tag": (op.get("tags") or ["—"])[0], "summary": op.get("summary", "")})
    return ops


def kafka_topics(backend: Path) -> list[str]:
    topics = set()
    for p in (backend / "src/App.Application/Features").rglob("*.cs"):
        topics |= set(re.findall(r'Topic\s*=>\s*"([^"]+)"', p.read_text(encoding="utf-8")))
    return sorted(topics)


def providers(services: dict) -> list[tuple[str, str]]:
    env = services.get("backend", {}).get("environment", {})
    return sorted((m.group(1), str(v)) for k, v in env.items() if (m := re.fullmatch(r"Providers__(\w+)__BaseUrl", k)))


def ci_stages(ci: dict) -> list[tuple[str, list[str]]]:
    jobs: dict[str, list[str]] = {s: [] for s in ci.get("stages", [])}
    for name, job in ci.items():
        if isinstance(job, dict) and "stage" in job and not name.startswith("."):
            jobs.setdefault(job["stage"], []).append(name)
    return list(jobs.items())


def render(artifacts: Path) -> str:
    spec = load_yaml(artifacts / "02_system/openapi.yaml")
    info = spec.get("info") or {}
    infra = artifacts / "08_infra"
    services = compose_services(infra)
    ci = load_yaml(artifacts / ".gitlab-ci.yml")
    ops = operations(spec)
    tags = sorted({o["tag"] for o in ops})
    roles = sorted({r for o in ops for r in (o["roles"] or []) if r not in ("anonymous", "public", "guest", "*", "all")})
    anonymous = [f"`{o['method']} {o['path']}`" for o in ops if o["roles"] is not None and
                 (not o["roles"] or set(o["roles"]) & {"anonymous", "public", "guest"})]
    stand = {k: v for k, v in services.items() if "web" not in v["profiles"]}
    total = sum(mem_mb(s.get("memory", "0")) for s in stand.values())
    backend_env = services.get("backend", {}).get("environment", {})
    topics = kafka_topics(artifacts / "04_backend")
    provs = providers(services)
    title = info.get("title", "Backend API")

    out = [f"# Паспорт сервиса: {title} (backend)",
           "",
           "> Сгенерирован `generators/docs/gen_docs.py` из артефактов продукта (OpenAPI, docker-compose, .gitlab-ci.yml). "
           "Не редактировать вручную — изменения вносятся в источники.",
           "",
           "## Описание",
           (info.get("description") or "").strip() or "Серверная часть продукта: REST API, события, фоновые задачи.",
           "",
           table(["Название", "Описание"], [
               ["Тип приложения", "ASP.NET Core REST API + SignalR (`/hubs/events`) + фоновые задачи"],
               ["Прикладное ПО / Frameworks", ".NET 10 (C# 14), ASP.NET Core, EF Core 10 + Npgsql + NetTopologySuite, Confluent.Kafka, prometheus-net"],
               ["Системное ПО", "Linux (контейнер `mcr.microsoft.com/dotnet/aspnet:10.0`), PostgreSQL 17 + PostGIS 3.5, Apache Kafka 4.1 (KRaft)"],
               ["Swagger", "`/swagger` (UI), `/swagger/v1/swagger.json` при `OpenApi__Enabled=true`; контракт — `02_system/openapi.yaml`"],
               ["Архитектура", "сервисная: один backend (Clean Architecture: Domain / Application / Infrastructure / Api), "
                               "transactional outbox → Kafka → SignalR"],
               ["Операции API", f"{len(ops)} операций в {len(tags)} группах: {', '.join(tags)}"],
           ]),
           "",
           "## Ссылки",
           table(["", "Production", "Stage", "Test"], [
               ["Сервис", "отсутствует", "стенд на командной VM: `http://<vm>:8080` (`STAND_URL`)", "локально: `http://localhost:8080`"],
               ["Swagger", "—", "`http://<vm>:8080/swagger`", "`http://localhost:8080/swagger`"],
               ["Логи", "—", "`docker compose logs backend` в `08_infra` на VM", "то же локально"],
           ]),
           "",
           "## Требования к ресурсам",
           "Сервис и зависимости запускаются в контейнерах, лимиты памяти заданы в compose (переопределяются `*_MEM` в `.env`):",
           "",
           table(["Сервис", "Образ / сборка", "Лимит памяти"],
                 [[name, resolve(s.get("image") or "сборка из исходников"), s.get("memory", "—")] for name, s in stand.items()]),
           "",
           f"Итого лимит стенда ≈ {total} МБ; фактическое потребление на кейсе 1 — около 0,5 ГБ. "
           "Целевая VM: 4 CPU, 4 ГБ RAM; диск — ~3 ГБ под образы и том БД.",
           "",
           "## Отказоустойчивость",
           "Стенд хакатона: один экземпляр каждого сервиса, рабочее время, без резервирования. Контейнеры перезапускаются "
           "политикой `restart: unless-stopped` (в том числе после `POST /api/v1/system/restarts`). Backend без состояния "
           "в памяти: горизонтальное масштабирование возможно, если вынести фоновые задачи на один экземпляр (лидер) — не реализовано.",
           "",
           "## Предельные ограничения работы сервиса",
           "- Ограничение частоты запросов: 600 запросов в минуту на пользователя (анонимные — на IP), `RateLimiting:PermitPerMinute`; сверх — 429.",
           "- Нагрузочное тестирование не проводилось; функциональная проверка — e2e-сценарий стенда (`07_tests/e2e`).",
           "",
           "## Работа сервиса",
           "Принимает REST-запросы (JSON, ошибки — RFC 9457 `application/problem+json` с полем `code`), пишет в PostgreSQL, "
           "публикует доменные события через transactional outbox в Kafka и ретранслирует их клиентам по SignalR. "
           "Изменяющие операции поддерживают `Idempotency-Key`. Фоновые задачи (таймеры предметной области) работают на `TimeProvider`.",
           "",
       ]
    if topics:
        out += [f"Топики Kafka: {', '.join(f'`{t}`' for t in topics)} (+ DLQ); ключ партиции — идентификатор агрегата.", ""]
    if provs:
        out += ["Внешние провайдеры (адаптер с повторами, таймаутом и circuit breaker — Microsoft.Extensions.Http.Resilience):", "",
                table(["Провайдер", "Адрес на стенде"], [[n, f"`{u}`"] for n, u in provs]), ""]
    out += ["## Авторизация",
            "JWT (HS256): `POST /api/v1/auth/tokens` выдаёт токен, далее заголовок `Authorization: Bearer <token>`; "
            "для SignalR — `access_token` в строке запроса. Роли — claim `role`, проверяются на каждой операции "
            "по `x-roles` контракта.",
            ""]
    if roles:
        out.append(f"Роли: {', '.join(f'`{r}`' for r in roles)}.")
    if anonymous:
        out.append(f"Без токена доступны: {', '.join(anonymous)}, а также `/health*`, `/metrics`, `/swagger`.")
    out += ["",
            "## Описание окружений",
            "### Prod окружение",
            "Отсутствует.",
            "### Stage окружение",
            "Стенд на командной VM, разворачивается задачей `stand:deploy` из основной ветки:",
            "",
            table(["Направление", "Куда", "Протокол / порт"], [
                ["К сервису", "клиенты (web-админка, Android), e2e", "HTTP 8080"],
                ["От сервиса", "PostgreSQL (`postgres`)", "TCP 5432 (внутри сети compose)"],
                ["От сервиса", "Kafka (`kafka`)", "TCP 9092 (внутри сети compose)"],
            ] + [["От сервиса", f"провайдер {n}", "HTTP"] for n, _ in provs]),
            "",
            "### Test окружение",
            "Локальный запуск того же compose (`docker compose up -d --build --wait` в `08_infra`) и Testcontainers в интеграционных тестах.",
            "",
            "## Сборка",
            "Многостадийный `04_backend/Dockerfile`: restore (кэшируемый слой) → `dotnet publish -c Release` → образ `aspnet:10.0`, "
            "пользователь без root, порт 8080. Версия приложения — аргумент `APP_VERSION` (в CI — короткий SHA коммита), "
            "видна в `GET /api/v1/system/info`.",
            "",
            "Стадии `.gitlab-ci.yml`:",
            "",
            table(["Стадия", "Задачи"], [[s, ", ".join(f"`{j}`" for j in jobs) or "из `ci/*.gitlab-ci.yml` (QA)"] for s, jobs in ci_stages(ci)]),
            "",
            "## Деплой",
            "Shell-runner на VM (тег `vm5`): образ собирается в локальный Docker VM (registry не нужен) → сканирование образа "
            "(Trivy, задачи QA в стадии security) → `08_infra/deploy.sh`: секреты из `~/.specforge-stand.env` на VM, "
            "`docker compose up -d --build --wait` с healthcheck каждого сервиса → e2e против стенда. "
            "Деплой только из основной ветки, параллельные деплои исключены `resource_group`.",
            "",
            "## Конфигурация",
            "`appsettings.json` + переменные окружения (приоритет у переменных; вложенность через `__`). На стенде всё задаётся "
            "в compose и `.env` (создаётся `deploy.sh`, права 600, в git не попадает).",
            "",
            table(["Переменная", "Значение на стенде", "Описание"],
                  [[f"`{k}`", env_value(k, v), ENV_DOCS.get(k) or provider_doc(k)] for k, v in backend_env.items()]),
            "",
            "## Переменные окружения для CI/CD",
            table(["Имя переменной", "Stage", "Описание"], [[f"`{n}`", v, d] for n, v, d in CI_VARS]),
            "",
            "## Мониторинг",
            "### Мониторинг OS Linux и Docker сервиса",
            "zabbix-agent2 на VM по стандартным шаблонам организаторов (Linux by Zabbix agent, Docker by Zabbix agent 2); "
            "контейнеры стенда — `specforge-<сервис>-1`.",
            "### Мониторинг Application",
            "Технический мониторинг — Prometheus, `GET /metrics`:",
            "",
            table(["Метрика", "Что показывает"], [[f"`{m}`", d] for m, d in APP_METRICS]),
            "",
            "Бизнес-мониторинг — `GET /health` (Healthy / Degraded — 200, Unhealthy — 503), `GET /health/ready` "
            "(готовность: БД и брокер), `GET /health/live` (процесс жив). Перезапуск: `POST /api/v1/system/restarts` (роль `admin`).",
            "",
            table(["Проверка", "Группа", "Состояние при сбое", "Что значит"], [[f"`{n}`", g, s, d] for n, g, s, d in HEALTH_CHECKS]),
            "",
            "## Логирование",
            "Структурированные JSON-логи (время в UTC) в STDOUT/STDERR контейнера: сбор в Elasticsearch через `docker logs`, просмотр в Kibana. "
            "Ошибка запроса возвращает `traceId` в теле problem+json — по нему запись находится в логах; заголовки "
            "`X-Request-Id` и `X-Correlation-Id` принимаются и возвращаются в ответе.",
            "",
            "## Сетевая связность",
            "Наружу публикуется только порт backend 8080; PostgreSQL и моки провайдеров слушают 127.0.0.1 на VM, Kafka — только внутри сети compose. "
            "Исходящие соединения — только к провайдерам из таблицы выше.",
            "",
            "## Доступы к сервису",
            "Клиенты web-админки и мобильного приложения по HTTP 8080; административные операции — роль `admin`. "
            "Доступ к VM — SSH команды.",
            ""]
    return "\n".join(out)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    artifacts = Path(sys.argv[1]).resolve()
    services_dir = artifacts / "08_infra/services"
    services_dir.mkdir(parents=True, exist_ok=True)
    (services_dir / "backend-api.md").write_text(render(artifacts), encoding="utf-8")
    adr_dir = artifacts / "08_infra/adr"
    adr_dir.mkdir(parents=True, exist_ok=True)
    for adr in sorted(ADR_TEMPLATES.glob("*.md")):
        shutil.copyfile(adr, adr_dir / adr.name)
    print(f"паспорт: {services_dir / 'backend-api.md'}; ADR: {len(list(ADR_TEMPLATES.glob('*.md')))} в {adr_dir}")


if __name__ == "__main__":
    main()
