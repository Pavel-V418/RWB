# Стенд (docker compose)

Собирается `generators/gen-infra.sh` (или `run-all.sh`), проверяется `generators/stand-smoke.sh`.

```bash
docker compose up -d --build --wait            # postgres + kafka + backend
docker compose --profile web up -d --build     # + админка (WEB_CONTEXT, WEB_DOCKERFILE в .env)
docker compose down -v                         # остановить и удалить данные
```

| Сервис | Адрес | Примечание |
|---|---|---|
| backend | http://localhost:8080 | имя `backend:8080` ждёт nginx админки; Swagger — `/swagger`, OpenAPI — `/swagger/v1/swagger.json` |
| postgres | 127.0.0.1:5432 | PostGIS; схема и сиды из `db-init/` применяются один раз, на пустом томе |
| kafka | kafka:9092 (внутри сети) | KRaft, один брокер, автосоздание топиков |
| web | http://localhost:4200 | профиль `web` |

`.env` создаётся автоматически (пароль БД, ключ JWT) и в git не попадает. Дополнительно можно задать:
`TIME_SCALE` (ускорение времени для e2e), `BACKEND_PORT`, `POSTGIS_IMAGE`, `KAFKA_TOPIC_PREFIX`, `WEB_CONTEXT`, `WEB_DOCKERFILE`.
После изменения сидов или схемы нужен `docker compose down -v`: init-скрипты Postgres выполняются только на пустом томе.
