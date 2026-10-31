#!/usr/bin/env bash
# Проверка образа: шлюз Android-этапа на каркасе внутри контейнера, без сети и с лимитами песочницы.
#   bash packages/specforge-mobile/infra/android/smoke.sh [образ] [память]
# Пример: bash infra/images/android/smoke.sh specforge/android:jdk21-api36 3g
set -euo pipefail
IMAGE=${1:-specforge/android:jdk21-api36}
MEM=${2:-3g}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)  # корень пакета packages/specforge-mobile
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

cp -r "$ROOT/android/." "$WORK/"
rm -rf "$WORK/build" "$WORK/app/build" "$WORK/.gradle" "$WORK/screenshots" "$WORK/local.properties"
SPEC="$ROOT/reference/case1/03_ux/screen-spec.android.json"
cp "$SPEC" "$WORK/ux-spec.json"

echo "== образ $IMAGE, память $MEM, сеть выключена, pids-limit 512"
start=$(date +%s)
docker run --rm --network none --memory "$MEM" --pids-limit 512 -u "$(id -u):$(id -g)" \
  -v "$WORK:/work" "$IMAGE" bash -c '
    set -e
    python3 /opt/specforge/screens.py . --spec ux-spec.json generate
    python3 /opt/specforge/screens.py . --spec ux-spec.json check
    ./gradlew --no-daemon --offline assembleDebug testDebugUnitTest recordRoborazziDebug
    python3 /opt/specforge/screens.py . --spec ux-spec.json verify
  '
echo "== ГОТОВО за $(( $(date +%s) - start )) с; снимков: $(ls "$WORK"/screenshots/*.png | wc -l)"
