# specforge/node:22 — песочница web: Node 22, Angular CLI 20.1.6, headless Chromium (Karma /
# тесты компонентов), git. UI Kit команды — EXTRA_NPM_PACKAGES (прогрев кэша npm).
# Сборка: infra/images/build.sh node
ARG NODE_TAG=22-bookworm
FROM node:${NODE_TAG}

ARG ANGULAR_CLI=20.1.6
ARG EXTRA_NPM_PACKAGES=""
ARG PREWARM=true
ENV npm_config_cache=/cache/npm \
    npm_config_fund=false \
    npm_config_audit=false \
    NG_CLI_ANALYTICS=false \
    CHROME_BIN=/usr/bin/chromium \
    CI=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends git coreutils chromium ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system user.email "sandbox@specforge.local" \
    && git config --system user.name "specforge" \
    && mkdir -p /cache/nuget /cache/npm /cache/gradle /cache/pip /cache/trivy /work \
    && chmod -R 1777 /cache

RUN npm install -g "@angular/cli@${ANGULAR_CLI}"

# Прогрев: зависимости шаблона Angular той же версии (и UI Kit) — в /cache/npm.
RUN if [ "$PREWARM" = "true" ]; then \
      set -e; cd /tmp \
      && ng new prewarm --defaults --skip-git --style=scss --ssr=false \
      && cd prewarm \
      && if [ -n "$EXTRA_NPM_PACKAGES" ]; then npm install $EXTRA_NPM_PACKAGES; fi \
      && cd /tmp && rm -rf /tmp/prewarm; \
    fi \
    && chmod -R 1777 /cache

WORKDIR /work
