#!/usr/bin/env bash
# Детерминированная генерация backend-проекта из OpenAPI (без LLM).
#   generators/gen-server.sh <openapi.yaml> <backend_dir> [--verify]
# Если <backend_dir> пуст — копирует туда золотой каркас templates/backend-dotnet.
# Затем перегенерирует DTO, интерфейсы I<Tag>Api и контроллеры (папки Generated/ полностью перезаписываются).
# --verify: шлюз — dotnet build (warnings = errors).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
SPEC="$1"
OUT="$2"
VERIFY="${3:-}"
PYTHON="${PYTHON:-python3}"

if [[ ! -f "$OUT/App.slnx" ]]; then
  mkdir -p "$OUT"
  rsync -a --exclude bin --exclude obj --exclude TestResults "$ROOT/templates/backend-dotnet/" "$OUT/"
  echo "skeleton copied to $OUT"
fi

"$PYTHON" "$HERE/server/gen_aspnet.py" "$SPEC" "$OUT"

if [[ "$VERIFY" == "--verify" ]]; then
  dotnet build "$OUT/App.slnx" -warnaserror -nologo -v q
  echo "gate dotnet: build OK"
fi
