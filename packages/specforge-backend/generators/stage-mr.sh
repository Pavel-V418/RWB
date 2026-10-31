#!/usr/bin/env bash
# MR на каждый этап конвейера: изменения одного каталога этапа — отдельной веткой и merge request в GitLab.
#   generators/stage-mr.sh <artifacts_repo> <stage_dir> "<заголовок>"
# Пример: generators/stage-mr.sh artifacts 04_backend "Backend: каркас, контракт, EF-модели, реализация агентом"
# <artifacts_repo> — git-репозиторий продукта с remote origin в GitLab. MR создаётся push-опциями GitLab
# (merge_request.create), без API-токена. В описание MR попадает результат шлюза этапа (<stage_dir>/gate.json).
# TARGET_BRANCH (по умолчанию main), MR_LABELS (по умолчанию pipeline-stage).
set -euo pipefail

REPO="$(cd "$1" && pwd)"
STAGE="$2"
TITLE="$3"
TARGET="${TARGET_BRANCH:-main}"
BRANCH="stage/${STAGE}"
cd "$REPO"

git remote get-url origin >/dev/null 2>&1 || { echo "stage-mr: у $REPO нет remote origin (GitLab)" >&2; exit 2; }
[[ -d "$STAGE" ]] || { echo "stage-mr: нет каталога этапа $STAGE" >&2; exit 2; }

git fetch -q origin "$TARGET" 2>/dev/null || true
BASE="$(git rev-parse -q --verify "origin/$TARGET" || true)"

# Коммит этапа собирается во временном индексе поверх origin/<target>: рабочая копия и текущая ветка не меняются
export GIT_INDEX_FILE="$(mktemp)"
trap 'rm -f "$GIT_INDEX_FILE"' EXIT
if [[ -n "$BASE" ]]; then git read-tree "$BASE"; else git read-tree --empty; fi
git add -A -- "$STAGE"
[[ "$STAGE" == "08_infra" && -f .gitlab-ci.yml ]] && git add .gitlab-ci.yml   # этап инфраструктуры несёт пайплайн
TREE="$(git write-tree)"
if [[ -n "$BASE" && "$TREE" == "$(git rev-parse "$BASE^{tree}")" ]]; then
  echo "stage-mr: в $STAGE нет изменений относительно $TARGET — MR не нужен"
  exit 0
fi

GATE="нет gate.json"
if [[ -f "$STAGE/gate.json" ]]; then
  GATE="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(("пройден" if d.get("passed") else "НЕ пройден: " + "; ".join((e.get("message") or "")[:120] for e in d.get("errors", [])[:3])))' "$STAGE/gate.json")"
fi
DESCRIPTION="Этап конвейера: ${STAGE}. Шлюз качества: ${GATE}. Сгенерировано SpecForge."

COMMIT="$(GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-SpecForge pipeline}" GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-pipeline@specforge.local}" \
  GIT_COMMITTER_NAME="${GIT_AUTHOR_NAME:-SpecForge pipeline}" GIT_COMMITTER_EMAIL="${GIT_AUTHOR_EMAIL:-pipeline@specforge.local}" \
  git commit-tree "$TREE" ${BASE:+-p "$BASE"} -m "${STAGE}: ${TITLE}" -m "${DESCRIPTION}")"
git push -q --force origin "$COMMIT:refs/heads/$BRANCH" \
  -o merge_request.create \
  -o merge_request.target="$TARGET" \
  -o merge_request.title="${STAGE}: ${TITLE}" \
  -o merge_request.description="${DESCRIPTION}" \
  -o merge_request.label="${MR_LABELS:-pipeline-stage}" \
  -o merge_request.remove_source_branch
echo "stage-mr: $BRANCH → MR в $TARGET (шлюз: $GATE)"
