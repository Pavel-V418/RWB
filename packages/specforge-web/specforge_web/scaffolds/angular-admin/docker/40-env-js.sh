#!/bin/sh
# Генерирует /assets/env.js из переменных окружения (без пересборки образа).
set -eu
STYLE="null"
[ -n "${APP_MAP_STYLE_URL:-}" ] && STYLE="\"${APP_MAP_STYLE_URL}\""
EXTRA=""
# APP_MAP_CENTER="lon,lat", APP_MAP_ZOOM=10 — центр карты для развёртывания в конкретном регионе
[ -n "${APP_MAP_CENTER:-}" ] && EXTRA="${EXTRA}, mapCenter: [${APP_MAP_CENTER}]"
[ -n "${APP_MAP_ZOOM:-}" ] && EXTRA="${EXTRA}, mapZoom: ${APP_MAP_ZOOM}"
cat > /usr/share/nginx/html/assets/env.js <<JS
window.__env = { realtime: "${APP_REALTIME:-signalr}", mapStyleUrl: ${STYLE}${EXTRA} };
JS
