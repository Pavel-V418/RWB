#!/usr/bin/env bash
# Детерминированная сборка стенда (без LLM): templates/infra → <artifacts>/08_infra.
#   generators/gen-infra.sh <artifacts_dir> [<seed_dir>]
# db-init = <artifacts>/04_backend/db/*.sql (каркас + доменный DDL) + <seed_dir>/*.sql[.gz] (сиды кейса),
# порядок применения — по имени файла (000_platform, 100_domain, 200_*, 300_*).
# .env с паролем БД и ключом JWT создаётся один раз и в git не попадает.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
ART="$(cd "$1" && pwd)"
SEED="${2:-}"
OUT="$ART/08_infra"

mkdir -p "$OUT"
cp "$ROOT/templates/infra/README.md" "$ROOT/templates/ci/deploy.sh" "$OUT/"
# Свой compose-проект на кейс (specforge-case1, specforge-case3…): иначе стенд нового кейса на той же машине
# подхватит том БД прежнего — со старой схемой (db-init применяется только к пустому тому) и чужим паролем.
# Имя — STAND_NAME или каталог над artifacts; оно попадает в закоммиченный compose, так что CI-деплой совпадает.
STAND_NAME="${STAND_NAME:-$(basename "$(dirname "$ART")")}"
STAND_NAME="$(printf '%s' "$STAND_NAME" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9-' '-' | sed 's/^-*//; s/-*$//')"
# Web-часть (участник 4) в конвейере платформы — 05_web/admin, в опубликованном репозитории — web/admin
WEB_DEFAULT="../05_web"
[[ "${SPECFORGE_PLATFORM:-}" == "1" ]] && WEB_DEFAULT="../web/admin"
sed -e "s/^name: specforge\$/name: specforge-${STAND_NAME:-app}/" -e "s#\${WEB_CONTEXT:-../05_web}#\${WEB_CONTEXT:-$WEB_DEFAULT}#" \
  "$ROOT/templates/infra/docker-compose.yml" > "$OUT/docker-compose.yml"
# Пайплайн продукта. Отдельно (артефакты = репозиторий продукта) — в корень; в конвейере платформы
# (SPECFORGE_PLATFORM=1) — в 08_infra: публикатор кладёт 08_infra/.gitlab-ci.yml в корень (config/repo_layout.yaml)
if [[ "${SPECFORGE_PLATFORM:-}" == "1" ]]; then
  cp "$ROOT/templates/ci/gitlab-ci.yml" "$OUT/.gitlab-ci.yml"
else
  cp "$ROOT/templates/ci/gitlab-ci.yml" "$ART/.gitlab-ci.yml"
fi
# Для документации (участник 2): инструкция запуска и список переменных без значений
cp "$ROOT/templates/infra/README.md" "$OUT/README-run.md"
rm -rf "$OUT/db-init" && mkdir -p "$OUT/db-init"
cp "$ART"/04_backend/db/*.sql "$OUT/db-init/"
if [[ -n "$SEED" ]]; then
  find "$SEED" -maxdepth 1 \( -name '*.sql' -o -name '*.sql.gz' \) -exec cp {} "$OUT/db-init/" \;
fi
# Дополнения кейса (мок провайдера и т. п.): <seed_dir>/../infra/*.yml → docker-compose.override.yml,
# моки — в 08_infra/mocks, чтобы репозиторий артефактов был самодостаточным для CI
rm -rf "$OUT/mocks"
if [[ -n "$SEED" && -f "$SEED/../infra/docker-compose.override.yml" ]]; then
  cp "$SEED/../infra/docker-compose.override.yml" "$OUT/docker-compose.override.yml"
  [[ -d "$SEED/../mocks" ]] && cp -R "$SEED/../mocks" "$OUT/mocks"
else
  rm -f "$OUT/docker-compose.override.yml"
fi
# Init-скрипты читает пользователь postgres внутри контейнера: на Linux права файлов имеют значение
chmod -R a+rX "$OUT/db-init"
# db-init (схема + сиды) коммитится: деплой из CI берёт его из репозитория; секреты — только в .env
printf '.env\n' > "$OUT/.gitignore"

if [[ ! -f "$OUT/.env" ]]; then
  {
    echo "# Секреты стенда, сгенерированы gen-infra.sh. Не коммитить."
    echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
    echo "JWT_SIGNING_KEY=$(openssl rand -hex 32)"
  } > "$OUT/.env"
fi

printf '# Переменные стенда; значения секретов генерирует gen-infra.sh (или deploy.sh на VM)\nPOSTGRES_PASSWORD=\nJWT_SIGNING_KEY=\nPOSTGRES_PORT=5432\nBACKEND_PORT=8080\n' > "$OUT/.env.example"

(cd "$OUT" && docker compose --env-file .env config -q)
# Паспорт сервиса (шаблон организаторов) и ADR платформы — из того же compose, OpenAPI и .gitlab-ci.yml
"${PYTHON:-python3}" "$ROOT/generators/docs/gen_docs.py" "$ART"
echo "infra generated: $OUT ($(ls "$OUT/db-init" | tr '\n' ' '))"
