# specforge/devops:1 — песочница DevOps и безопасности: docker CLI + compose (валидация
# `docker compose config`, демона в песочнице нет), Trivy (зависимости, CVE, конфиги), hadolint,
# git. База уязвимостей Trivy скачивается при сборке в /cache/trivy (прогрев).
# Сборка: infra/images/build.sh devops
ARG DEBIAN_TAG=bookworm-slim
FROM debian:${DEBIAN_TAG}

ARG DOCKER_VERSION=27.3.1
ARG COMPOSE_VERSION=2.30.3
ARG TRIVY_VERSION=0.58.1
ARG HADOLINT_VERSION=2.12.0
ARG PREWARM=true
ENV TRIVY_CACHE_DIR=/cache/trivy \
    TRIVY_NO_PROGRESS=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git coreutils ca-certificates curl tar gzip bash jq python3 \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system user.email "sandbox@specforge.local" \
    && git config --system user.name "specforge" \
    && mkdir -p /cache/nuget /cache/npm /cache/gradle /cache/pip /cache/trivy /work \
    && chmod -R 1777 /cache

RUN set -e; arch="$(uname -m)"; \
    curl -fsSL "https://download.docker.com/linux/static/stable/${arch}/docker-${DOCKER_VERSION}.tgz" \
      | tar -xz -C /usr/local/bin --strip-components=1 docker/docker; \
    mkdir -p /usr/local/lib/docker/cli-plugins; \
    curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
      "https://github.com/docker/compose/releases/download/v${COMPOSE_VERSION}/docker-compose-linux-${arch}"; \
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose; \
    case "$arch" in x86_64) tarch=64bit; harch=x86_64 ;; aarch64) tarch=ARM64; harch=arm64 ;; esac; \
    curl -fsSL "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-${tarch}.tar.gz" \
      | tar -xz -C /usr/local/bin trivy; \
    curl -fsSL -o /usr/local/bin/hadolint \
      "https://github.com/hadolint/hadolint/releases/download/v${HADOLINT_VERSION}/hadolint-Linux-${harch}"; \
    chmod +x /usr/local/bin/hadolint; \
    docker --version && docker compose version && trivy --version && hadolint --version

RUN if [ "$PREWARM" = "true" ]; then trivy fs --download-db-only; fi \
    && chmod -R 1777 /cache

WORKDIR /work
