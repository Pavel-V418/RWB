#!/usr/bin/env bash
# E2E кейса №1: doctor → run examples/case1.md --autopilot → report.
#
#   scripts/e2e_case1.sh [путь к ТЗ]      (по умолчанию examples/case1.md)
#   E2E_SKIP_DOCTOR=1 — запуск, даже если doctor нашёл FAIL
#
# Код выхода: 2 — стенд не готов (doctor) или нет ТЗ; иначе — код `specforge run`
# (0 — без failed-этапов, 1 — есть failed). Отчёт строится в любом случае.
set -uo pipefail

cd "$(dirname "$0")/.."
CASE="${1:-examples/case1.md}"
RUN_ID="${RUN_ID:-e2e-case1-$(date +%Y%m%d-%H%M%S)}"
RUNS_DIR="${SPECFORGE_RUNS_DIR:-runs}"

if [[ ! -f "$CASE" ]]; then
  echo "Нет файла ТЗ: $CASE (положите ТЗ кейса №1 в examples/case1.md или передайте путь)" >&2
  exit 2
fi

echo "== 1/3 doctor"
if ! uv run specforge doctor; then
  if [[ "${E2E_SKIP_DOCTOR:-0}" != "1" ]]; then
    echo "doctor нашёл FAIL — почините стенд или запустите с E2E_SKIP_DOCTOR=1" >&2
    exit 2
  fi
  echo "doctor нашёл FAIL, продолжаю (E2E_SKIP_DOCTOR=1)" >&2
fi

echo "== 2/3 run $CASE (run_id=$RUN_ID)"
uv run specforge run "$CASE" --autopilot --run-id "$RUN_ID"
run_code=$?

echo "== 3/3 report"
if ! uv run specforge report "$RUN_ID" --runs-dir "$RUNS_DIR"; then
  echo "отчёт не построен (нет $RUNS_DIR/$RUN_ID/pipeline.json?)" >&2
  exit 2
fi
echo "Готово: $RUNS_DIR/$RUN_ID/report/report.md (код прогона: $run_code)"
exit "$run_code"
