#!/usr/bin/env bash
# Сборка образов песочниц SpecForge (AgentSpec.sandbox_image).
#
#   infra/images/build.sh                 — все образы
#   infra/images/build.sh dotnet node     — только перечисленные
#   PREWARM=false infra/images/build.sh   — без прогрева кэшей (быстрее, но первый restore — из сети)
#   EXTRA_NPM_PACKAGES="@taiga-ui/core" infra/images/build.sh node   — прогреть UI Kit команды
#
# После сборки: `uv run specforge doctor` проверит, что у агентов есть образы и программы шлюзов.
# Кэши пакетов (specforge-cache-*) заполняются из образа при первом монтировании пустого volume;
# чтобы прогреть заново: docker volume rm specforge-cache-<имя>.
set -euo pipefail

cd "$(dirname "$0")"

declare -A TAGS=(
  [dotnet]="specforge/dotnet:10"
  [node]="specforge/node:22"
  [postgis]="specforge/postgis:17"
  [devops]="specforge/devops:1"
  [android]="specforge/android:21"
)
ORDER=(dotnet node postgis devops android)

targets=("$@")
if [[ ${#targets[@]} -eq 0 ]]; then
  targets=("${ORDER[@]}")
fi

args=(--build-arg "PREWARM=${PREWARM:-true}")
if [[ -n "${EXTRA_NPM_PACKAGES:-}" ]]; then
  args+=(--build-arg "EXTRA_NPM_PACKAGES=${EXTRA_NPM_PACKAGES}")
fi

failed=()
for name in "${targets[@]}"; do
  tag="${TAGS[$name]:-}"
  if [[ -z "$tag" ]]; then
    echo "неизвестный образ: $name (есть: ${ORDER[*]})" >&2
    exit 2
  fi
  echo "== $name → $tag"
  if ! docker build "${args[@]}" -t "$tag" "$name"; then
    failed+=("$name")
  fi
done

if [[ ${#failed[@]} -gt 0 ]]; then
  echo "не собрались: ${failed[*]}" >&2
  exit 1
fi
echo "готово: ${targets[*]}"
