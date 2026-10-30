#!/usr/bin/env bash
# Скачать корпус документации для RAG (заранее, до хакатона/прогона), веса локальной модели
# эмбеддингов (data/models — на площадке может не быть интернета) и проиндексировать всё.
#
#   infra/fetch_docs.sh            — скачать всё и собрать data/docs.db
#   infra/fetch_docs.sh --index    — только пересобрать индекс по уже скачанному
#
# Корпус: infra/docs-corpus/<tech>/*.md|*.html (в .gitignore). Тексты шаблонов организаторов
# положите вручную в infra/docs-corpus/org-standards/ — они индексируются как tech=org-standards.
# Скрипт идемпотентен: повторный запуск обновляет репозитории (git pull) и индекс целиком.
set -euo pipefail

cd "$(dirname "$0")/.."
CORPUS="infra/docs-corpus"
CACHE="$CORPUS/.src"
mkdir -p "$CORPUS/org-standards" "$CACHE"

# tech | git-репозиторий | каталог документации внутри репозитория
SOURCES=(
  "angular|https://github.com/angular/angular.git|adev/src/content"
  "dotnet|https://github.com/dotnet/AspNetCore.Docs.git|aspnetcore/fundamentals"
  "efcore|https://github.com/dotnet/EntityFramework.Docs.git|entity-framework/core"
  "postgres|https://github.com/postgres/postgres.git|doc/src/sgml"
  "postgis|https://github.com/postgis/postgis.git|doc"
  "kafka|https://github.com/apache/kafka.git|docs"
  "android|https://github.com/android/architecture-samples.git|."
  "kotlin|https://github.com/JetBrains/kotlin-web-site.git|docs/topics"
  "docker|https://github.com/docker/docs.git|content/manuals/compose"
  "gitlab-ci|https://gitlab.com/gitlab-org/gitlab.git|doc/ci/yaml"
)

fetch() {
  local tech="$1" repo="$2" subdir="$3"
  local src="$CACHE/$tech"
  if [[ -d "$src/.git" ]]; then
    git -C "$src" pull --quiet --depth 1 || echo "  $tech: обновить не удалось, беру старую копию" >&2
  else
    # sparse checkout: только каталог документации, без истории
    git clone --quiet --depth 1 --filter=blob:none --sparse "$repo" "$src"
    git -C "$src" sparse-checkout set "$subdir"
  fi
  local dest="$CORPUS/$tech"
  rm -rf "$dest" && mkdir -p "$dest"
  # только .md/.html (+ .sgml postgres как html), структура каталогов сохраняется
  (cd "$src/$subdir" && find . -type f \( -name '*.md' -o -name '*.mdx' -o -name '*.html' -o -name '*.sgml' \) -print0) |
    while IFS= read -r -d '' file; do
      target="$dest/${file#./}"
      case "$target" in
        *.mdx) target="${target%.mdx}.md" ;;
        *.sgml) target="${target%.sgml}.html" ;;
      esac
      mkdir -p "$(dirname "$target")"
      cp "$src/$subdir/$file" "$target"
    done
  echo "  $tech: $(find "$dest" -type f | wc -l) файлов"
}

if [[ "${1:-}" != "--index" ]]; then
  echo "== скачивание корпуса в $CORPUS"
  for entry in "${SOURCES[@]}"; do
    IFS='|' read -r tech repo subdir <<<"$entry"
    fetch "$tech" "$repo" "$subdir" || echo "  $tech: ошибка скачивания, пропускаю" >&2
  done
fi

if [[ -z "$(ls -A "$CORPUS/org-standards")" ]]; then
  echo "  org-standards пуст: положите шаблоны организаторов (.md/.html) в $CORPUS/org-standards/" >&2
fi

if [[ -z "${EMBED_BASE_URL:-}" ]]; then
  echo "== веса локальной модели эмбеддингов (data/models)"
  uv run python -m specforge.rag.embeddings download ||
    echo "  не удалось скачать веса — поиск будет только FTS5" >&2
fi

echo "== индекс"
uv run python -m specforge.rag.ingest "$CORPUS"
