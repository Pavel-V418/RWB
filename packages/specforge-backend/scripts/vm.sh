#!/usr/bin/env bash
# Синхронизация с командной VM (запускать на Mac).
#   scripts/vm.sh push          — отправить проект на VM в ~/RWB_back (без .git, .venv, сборок и секретов)
#   scripts/vm.sh pull [case1]  — забрать результаты агента с VM в .vm-runs/<время>/ (ответы модели, отчёт, код)
#   scripts/vm.sh ssh           — открыть терминал VM
# Адрес: VM_HOST (по умолчанию team5@85.143.113.230), VM_PORT (10005).
set -euo pipefail

HOST="${VM_HOST:-team5@85.143.113.230}"
PORT="${VM_PORT:-10005}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH=(ssh -p "$PORT" "$HOST")

case "${1:-}" in
  push)
    # --no-xattrs/--no-mac-metadata: без служебных атрибутов macOS (иначе GNU tar на VM сыплет предупреждениями)
    (cd "$ROOT" && COPYFILE_DISABLE=1 tar czf - --no-xattrs --no-mac-metadata --no-fflags \
      --exclude .venv --exclude .git --exclude .tools --exclude node_modules --exclude bin --exclude obj \
      --exclude TestResults --exclude .agent --exclude .env --exclude .vm-runs --exclude gate.json .) \
      | "${SSH[@]}" 'mkdir -p ~/RWB_back && tar xzf - -C ~/RWB_back && echo "pushed to $(hostname):~/RWB_back"'
    ;;
  pull)
    CASE="${2:-case1}"
    DEST="$ROOT/.vm-runs/$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$DEST"
    "${SSH[@]}" "cd ~/RWB_back/reference/$CASE/artifacts/04_backend && tar czf - --ignore-failed-read .agent backend-agent.json src/App.Application/Features src/App.Application/DependencyInjection.Features.cs 2>/dev/null" \
      | tar xzf - -C "$DEST"
    echo "pulled to $DEST"
    ;;
  ssh)
    exec "${SSH[@]}"
    ;;
  *)
    sed -n '2,7p' "$0"; exit 1
    ;;
esac
