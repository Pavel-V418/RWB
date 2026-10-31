#!/usr/bin/env bash
# GitLab Runner на командной VM без sudo (запускать НА VM от team5).
#   scripts/vm-runner.sh install                       — бинарник из официального образа gitlab/gitlab-runner в ~/.local/bin
#   GITLAB_URL=... RUNNER_TOKEN=glrt-... scripts/vm-runner.sh register   — регистрация shell-раннера (теги задаются в GitLab: vm5)
#   scripts/vm-runner.sh start | stop | status | logs
# Shell-executor: задачи идут от team5 с уже установленными .NET SDK (~/.dotnet), Docker и Python.
# concurrent = 1 — на VM 3.8 ГБ, параллельные сборки .NET и стенд не поместятся.
set -euo pipefail

BIN="$HOME/.local/bin/gitlab-runner"
CONFIG="$HOME/.gitlab-runner/config.toml"
WORK="$HOME/gitlab-runner"
PID="$WORK/runner.pid"
IMAGE="${RUNNER_IMAGE:-gitlab/gitlab-runner:alpine}"
mkdir -p "$(dirname "$BIN")" "$(dirname "$CONFIG")" "$WORK"

case "${1:-}" in
  install)
    docker pull -q "$IMAGE" >/dev/null
    id="$(docker create "$IMAGE")"
    docker cp "$id:/usr/bin/gitlab-runner" "$BIN"
    docker rm "$id" >/dev/null
    chmod +x "$BIN"
    "$BIN" --version | head -2
    ;;
  register)
    : "${GITLAB_URL:?задайте GITLAB_URL}" "${RUNNER_TOKEN:?задайте RUNNER_TOKEN (glrt-..., создаётся в GitLab: Settings → CI/CD → Runners)}"
    "$BIN" register --non-interactive --config "$CONFIG" --url "$GITLAB_URL" --token "$RUNNER_TOKEN" \
      --executor shell --name "$(hostname)-shell"
    sed -i 's/^concurrent = .*/concurrent = 1/' "$CONFIG"
    echo "зарегистрирован: $CONFIG (concurrent = 1)"
    ;;
  start)
    [[ -f "$PID" ]] && kill -0 "$(cat "$PID")" 2>/dev/null && { echo "уже запущен (pid $(cat "$PID"))"; exit 0; }
    nohup "$BIN" run --config "$CONFIG" --working-directory "$WORK" >"$WORK/runner.log" 2>&1 &
    echo $! > "$PID"
    sleep 2 && kill -0 "$(cat "$PID")" && echo "запущен (pid $(cat "$PID")), лог: $WORK/runner.log"
    ;;
  stop)
    [[ -f "$PID" ]] && kill "$(cat "$PID")" 2>/dev/null && rm -f "$PID" && echo "остановлен" || echo "не запущен"
    ;;
  status)
    [[ -f "$PID" ]] && kill -0 "$(cat "$PID")" 2>/dev/null && echo "работает (pid $(cat "$PID"))" || echo "не запущен"
    [[ -f "$CONFIG" ]] && "$BIN" list --config "$CONFIG" 2>&1 | tail -n +2 || echo "не зарегистрирован"
    ;;
  logs)
    tail -n 50 "$WORK/runner.log"
    ;;
  *)
    sed -n '2,8p' "$0"; exit 1
    ;;
esac
