#!/usr/bin/env bash
# Шлюз DevOps: поднять стенд и проверить его снаружи.
#   generators/stand-smoke.sh <artifacts_dir> [--down]
# Проверки: compose up --wait (healthcheck всех сервисов), /health/ready, /swagger/v1/swagger.json, /metrics;
# печатает число строк в доменных таблицах (сиды кейса). С ремонтом по диагностике — agents/devops (DevOps-агент). Результат — <artifacts>/08_infra/gate.json.
# --down: после проверки остановить стенд и удалить том БД.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
ART="$(cd "$1" && pwd)"
INFRA="$ART/08_infra"
PYTHON="${PYTHON:-python3}"
# --project-directory: compose сам подхватит docker-compose.override.yml кейса (мок провайдера и т. п.)
DC=(docker compose --project-directory "$INFRA" --env-file "$INFRA/.env")
PORT="$(grep -E '^BACKEND_PORT=' "$INFRA/.env" 2>/dev/null | cut -d= -f2)"
BASE="http://127.0.0.1:${PORT:-8080}"
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

fail() { echo "SMOKE FAIL: $*" | tee -a "$LOG" >&2; }
code=0
{
  "${DC[@]}" up -d --build --wait --wait-timeout 300 || { fail "compose up --wait"; "${DC[@]}" ps; "${DC[@]}" logs --tail 80 backend; code=1; }
  if [[ $code -eq 0 ]]; then
    ready="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/health/ready")"
    [[ "$ready" == "200" ]] || { fail "/health/ready → $ready"; code=1; }
    curl -sf "$BASE/swagger/v1/swagger.json" -o /dev/null || { fail "/swagger/v1/swagger.json недоступен"; code=1; }
    curl -sf "$BASE/metrics" -o /dev/null || { fail "/metrics недоступен"; code=1; }
    # Сиды кейса загружены: хотя бы одна доменная таблица (кроме платформенных) не пуста
    rows="$("${DC[@]}" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -qtAc "select coalesce(sum(n_live_tup), 0) from pg_stat_user_tables where relname not like '"'"'outbox%'"'"' and relname not like '"'"'idempotency%'"'"'"' 2>&1)"
    echo "stand: $BASE ready=$ready rows=$rows"
  fi
} >"$LOG" 2>&1
cat "$LOG"
"$PYTHON" "$HERE/gate_report.py" stand "$code" "$LOG" "$INFRA/gate.json" "$ART"
[[ "${2:-}" == "--down" ]] && "${DC[@]}" down -v >/dev/null 2>&1
exit $code
