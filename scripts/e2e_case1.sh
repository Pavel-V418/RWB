#!/usr/bin/env bash
# E2E кейса №1: doctor → run examples/case1/tz.md --autopilot --data examples/case1/data → report.
#
#   scripts/e2e_case1.sh [путь к ТЗ]      (по умолчанию examples/case1/tz.md; каталог data/
#                                          рядом с ТЗ, если есть, передаётся как --data)
#   E2E_SKIP_DOCTOR=1 — запуск, даже если doctor нашёл FAIL
#   E2E_SAVE_BASELINE=1 — сохранить отчёт как bench/baseline/ (точка отсчёта «до/после»);
#                         без него отчёт сравнивается с существующим baseline
#
# Код выхода: 2 — стенд не готов (doctor) или нет ТЗ; иначе — код `specforge run`
# (0 — без failed-этапов, 1 — есть failed). Отчёт строится в любом случае.
set -uo pipefail

cd "$(dirname "$0")/.."
CASE="${1:-examples/case1/tz.md}"
DATA_DIR="$(dirname "$CASE")/data"
RUN_ID="${RUN_ID:-e2e-case1-$(date +%Y%m%d-%H%M%S)}"
RUNS_DIR="${SPECFORGE_RUNS_DIR:-runs}"

if [[ ! -f "$CASE" ]]; then
  echo "Нет файла ТЗ: $CASE (ТЗ кейса №1 — examples/case1/tz.md, или передайте путь)" >&2
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

run_args=("$CASE" --autopilot --run-id "$RUN_ID")
if [[ -d "$DATA_DIR" ]]; then
  run_args+=(--data "$DATA_DIR")
fi
echo "== 2/3 run ${run_args[*]}"
uv run specforge run "${run_args[@]}"
run_code=$?

echo "== 3/3 report"
report_args=("$RUN_ID" --runs-dir "$RUNS_DIR")
if [[ "${E2E_SAVE_BASELINE:-0}" == "1" ]]; then
  report_args+=(--save-baseline)
fi
if ! uv run specforge report "${report_args[@]}"; then
  echo "отчёт не построен (нет $RUNS_DIR/$RUN_ID/pipeline.json?)" >&2
  exit 2
fi
echo "Готово: $RUNS_DIR/$RUN_ID/report/report.md (код прогона: $run_code)"
exit "$run_code"
