#!/usr/bin/env bash
# Все детерминированные шаги (без LLM) одной командой — вызывается оркестратором после агента-архитектора.
#   generators/run-all.sh <artifacts_dir> [--verify]
# Вход:  <artifacts>/02_system/openapi.yaml, <artifacts>/02_system/schema.sql
# Выход: <artifacts>/04_backend (каркас + контракт + EF-модели), 06_android/.../core/network/generated
#        и для каждого шлюза gate.json: {"gate", "passed", "errors": [{"file", "line", "message"}]}
#          04_backend/gate.json — генерация backend и dotnet build
#          08_infra/gate.json   — сборка стенда (docker compose config); подъём — generators/stand-smoke.sh
#          06_android/gate.json — генерация Kotlin-клиента и его компиляция
# Код выхода ≠ 0 — шлюз не пройден; полный лог в stderr, кратко — в gate.json (вход для repair-агента).
# TS-клиент не генерируется: у web-ветки свой генератор из того же OpenAPI (CLIENTS=ts,kotlin — включить).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
ART="$(cd "$1" && pwd)"
VERIFY="${2:-}"
PYTHON="${PYTHON:-python3}"
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

gate() { # <имя> <каталог для gate.json> <команда...>
  local name="$1" dir="$2"; shift 2
  local code=0
  "$@" >"$LOG" 2>&1 || code=$?
  "$PYTHON" "$HERE/gate_report.py" "$name" "$code" "$LOG" "$dir/gate.json" "$ART"
  if [[ $code -ne 0 ]]; then
    cat "$LOG" >&2
    echo "gate $name: FAILED → $dir/gate.json" >&2
    exit "$code"
  fi
  echo "gate $name: OK"
}

# Внутри функции, вызванной из `cmd || code=$?`, bash игнорирует set -e — поэтому явные `|| return`
backend() {
  "$HERE/gen-server.sh" "$ART/02_system/openapi.yaml" "$ART/04_backend" || return $?
  "$HERE/gen-ef.sh" "$ART/02_system/schema.sql" "$ART/04_backend" || return $?
  if [[ "$VERIFY" == "--verify" ]]; then
    dotnet build "$ART/04_backend/App.slnx" -warnaserror -nologo -v q -clp:NoSummary || return $?
  fi
}

mkdir -p "$ART/04_backend" "$ART/06_android" "$ART/08_infra"
gate backend "$ART/04_backend" backend
# Стенд: compose + db-init из 04_backend/db и сидов кейса (SEED_DIR, по умолчанию <artifacts>/../seed)
SEED_DIR="${SEED_DIR:-$ART/../seed}"
[[ -d "$SEED_DIR" ]] || SEED_DIR=""
gate infra "$ART/08_infra" "$HERE/gen-infra.sh" "$ART" $SEED_DIR
# CLIENTS= (пусто) — без клиентов: на vm5 закрыт Maven Central, Kotlin-клиент собирается на машине разработчика
CLIENTS="${CLIENTS-kotlin}"
if [[ -n "$CLIENTS" ]]; then
  CLIENTS="$CLIENTS" gate android-client "$ART/06_android" "$HERE/gen-clients.sh" "$ART/02_system/openapi.yaml" "$ART" "$VERIFY"
else
  echo "gate android-client: SKIPPED (CLIENTS пусто)"
fi
