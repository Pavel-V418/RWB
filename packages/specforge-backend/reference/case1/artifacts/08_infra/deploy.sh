#!/usr/bin/env bash
# Деплой стенда на командную VM (вызывается задачей stand:deploy или вручную из 08_infra).
# Секреты стенда живут вне репозитория: ~/.specforge-stand.env (создаётся один раз, БД остаётся с тем же паролем).
# Можно задать в переменных CI: STAND_POSTGRES_PASSWORD, STAND_JWT_SIGNING_KEY, STAND_POSTGRES_PORT (по умолчанию 15432),
# STAND_EXTRA_ENV (доп. строки .env).
set -euo pipefail

cd "$(dirname "$0")"
SECRETS="${STAND_SECRETS_FILE:-$HOME/.specforge-stand.env}"
FIRST_DEPLOY=0
if [[ ! -f "$SECRETS" ]]; then
  FIRST_DEPLOY=1
  (umask 077 && {
    echo "POSTGRES_PASSWORD=${STAND_POSTGRES_PASSWORD:-$(openssl rand -hex 16)}"
    echo "JWT_SIGNING_KEY=${STAND_JWT_SIGNING_KEY:-$(openssl rand -hex 32)}"
  } > "$SECRETS")
fi
(umask 077 && {
  cat "$SECRETS"
  # 5432 на VM занят системным PostgreSQL; порт БД на хосте нужен только для отладки
  echo "POSTGRES_PORT=${STAND_POSTGRES_PORT:-15432}"
  [[ -z "${STAND_EXTRA_ENV:-}" ]] || printf '%s\n' "$STAND_EXTRA_ENV"
} > .env.tmp) && mv .env.tmp .env
chmod -R a+rX db-init

if [[ $FIRST_DEPLOY == 1 ]]; then
  # Новые секреты: том БД от прежнего ручного запуска создан с другим паролем — пересоздаём (сиды загрузятся заново)
  docker compose --env-file .env down -v --remove-orphans
fi
# На VM одновременно живёт один стенд: другие проекты specforge-* держат те же порты (8080, БД) — останавливаем
# их без -v, данные сохраняются и вернутся при их следующем деплое
PROJECT="$(docker compose --env-file .env config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
for other in $(docker compose ls -q | grep '^specforge' | grep -vx "$PROJECT" || true); do
  echo "deploy: останавливаю стенд $other (порты нужны $PROJECT)"
  docker compose -p "$other" stop
done
docker compose --env-file .env up -d --build --wait --wait-timeout 300
docker compose ps
