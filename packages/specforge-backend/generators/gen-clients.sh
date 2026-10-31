#!/usr/bin/env bash
# Детерминированная генерация API-клиентов из OpenAPI (без LLM).
#   generators/gen-clients.sh <openapi.yaml> <artifacts_dir> [--verify]
# Результат:
#   <artifacts_dir>/05_web/api-client      — Angular 20 (typescript-angular), для участника 4
#   <artifacts_dir>/06_android/<ANDROID_SRC_ROOT>/<appId>/core/network/generated — Kotlin + Retrofit
#     + coroutines + kotlinx.serialization, для участника 5 (перезаписывается только папка generated/)
# Переменные: ANDROID_APP_ID (по умолчанию com.specforge.case1),
#             ANDROID_SRC_ROOT (по умолчанию app/src/main/kotlin, относительно 06_android)
# CLIENTS: какие клиенты генерировать, через запятую (по умолчанию ts,kotlin; run-all передаёт kotlin)
# --verify: шлюз — сборка ng-packagr и gradle compileKotlin. Код выхода ≠ 0 при любой ошибке.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SPEC="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
OUT="$(mkdir -p "$2" && cd "$2" && pwd)"
VERIFY="${3:-}"

GEN_VERSION="$(cat "$HERE/openapi-generator.version")"
JAR="$HERE/.tools/openapi-generator-cli-$GEN_VERSION.jar"
# На macOS /usr/bin/java — заглушка без JDK, поэтому проверяем, что java реально запускается
JAVA="${JAVA:-${JAVA_HOME:+$JAVA_HOME/bin/java}}"
if [[ -z "$JAVA" ]] || ! "$JAVA" -version >/dev/null 2>&1; then
  JAVA=java
  "$JAVA" -version >/dev/null 2>&1 || JAVA=/opt/homebrew/opt/openjdk/bin/java
fi

if [[ ! -f "$JAR" ]]; then
  mkdir -p "$HERE/.tools"
  url="https://repo1.maven.org/maven2/org/openapitools/openapi-generator-cli/$GEN_VERSION/openapi-generator-cli-$GEN_VERSION.jar"
  curl -sfL -o "$JAR.tmp" "$url"
  echo "$(curl -sfL "$url.sha1")  $JAR.tmp" | shasum -a 1 -c --status
  mv "$JAR.tmp" "$JAR"
fi

generate() { # <config> <out> [доп. -p свойства]
  rm -rf "$2" && mkdir -p "$(dirname "$2")"
  "$JAVA" -jar "$JAR" generate -c "$1" -i "$SPEC" -o "$2" ${3:+-p "$3"} \
    --skip-validate-spec >/dev/null 2>"$2.log" \
    || { cat "$2.log" >&2; exit 1; }
  rm -f "$2.log"
}

APP_ID="${ANDROID_APP_ID:-com.specforge.case1}"
PKG="$APP_ID.core.network.generated"
PKG_PATH="${PKG//.//}"
TS="$OUT/05_web/api-client"
KT_DEST="$OUT/06_android/${ANDROID_SRC_ROOT:-app/src/main/kotlin}/$PKG_PATH"

# Kotlin генерируется во временный JVM-проект (шлюз компиляции), в модуль Android копируются только исходники
KT="$(mktemp -d)/kotlin-client"
trap 'rm -rf "$(dirname "$KT")"' EXIT
CLIENTS=",${CLIENTS:-ts,kotlin},"
if [[ "$CLIENTS" == *,ts,* ]]; then
  generate "$HERE/clients/typescript-angular.yaml" "$TS"
  rm -f "$TS/git_push.sh"
  echo "client generated: $TS"
fi
if [[ "$CLIENTS" != *,kotlin,* ]]; then
  KT_DEST=""
fi
[[ -z "$KT_DEST" ]] || generate "$HERE/clients/kotlin-retrofit.yaml" "$KT" "packageName=$PKG"
if [[ -n "$KT_DEST" ]]; then
  # Заглушки тестов генератора (kotlintest) не нужны и не собираются
  rm -rf "$KT/src/test" "$KT/build.gradle" "$KT/settings.gradle" "$KT/git_push.sh"
  # number → Double (см. kotlin-retrofit.yaml), но шаблон значений по умолчанию рассчитан на BigDecimal("10")
  find "$KT/src/main/kotlin" -name '*.kt' -exec perl -pi -e 's/kotlin\.Double\("(-?\d+)"\)/$1.0/g; s/kotlin\.Double\("(-?\d+\.\d+)"\)/$1/g' {} +
  cp "$HERE/clients/kotlin-build.gradle.kts" "$KT/build.gradle.kts"
  echo 'rootProject.name = "api-client"' > "$KT/settings.gradle.kts"
  rm -rf "$KT_DEST" && mkdir -p "$KT_DEST"
  cp -R "$KT/src/main/kotlin/$PKG_PATH/." "$KT_DEST/"
  echo "client generated: $KT_DEST"
fi

if [[ "$VERIFY" == "--verify" ]]; then
  if [[ "$CLIENTS" == *,ts,* ]]; then
    (cd "$TS" && npm install --no-audit --no-fund --loglevel=error >/dev/null && npm run build --silent >/dev/null)
    echo "gate ts: ng-packagr build OK"
  fi
  if [[ -n "$KT_DEST" ]]; then
    # пути ошибок — на файлы в модуле Android, а не во временный проект (их читает repair-агент)
    (cd "$KT" && JAVA_HOME="${GRADLE_JAVA_HOME:-/opt/homebrew/opt/openjdk@21}" gradle -q --no-daemon compileKotlin 2>&1 \
      | sed "s|$KT/src/main/kotlin/$PKG_PATH|$KT_DEST|g"; exit "${PIPESTATUS[0]}")
    echo "gate kotlin: compileKotlin OK"
  fi
fi
