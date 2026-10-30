# specforge/dotnet:10 — песочница backend: .NET 10 SDK (C# 14), git (снимки ремонта), dotnet-ef.
# Сборка: infra/images/build.sh dotnet   (PREWARM=false — без прогрева кэша NuGet)
ARG DOTNET_TAG=10.0
FROM mcr.microsoft.com/dotnet/sdk:${DOTNET_TAG}

ARG PREWARM=true
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
    NUGET_PACKAGES=/cache/nuget \
    PATH="${PATH}:/opt/dotnet-tools"

RUN apt-get update \
    && apt-get install -y --no-install-recommends git coreutils ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system user.email "sandbox@specforge.local" \
    && git config --system user.name "specforge" \
    && mkdir -p /cache/nuget /cache/npm /cache/gradle /cache/pip /cache/trivy /work \
    && chmod -R 1777 /cache

RUN dotnet tool install --tool-path /opt/dotnet-tools dotnet-ef --version "10.*"

# Прогрев: пакеты типового backend стека кейса — в /cache/nuget. Пустой volume specforge-cache-nuget
# при первом монтировании получит это содержимое, и restore без сети пройдёт быстрее.
RUN if [ "$PREWARM" = "true" ]; then \
      set -e; cd /tmp && dotnet new sln -n Prewarm \
      && dotnet new webapi -n Api --no-https && dotnet new xunit -n Api.Tests \
      && dotnet sln Prewarm.sln add Api Api.Tests \
      && cd Api \
      && dotnet add package Npgsql.EntityFrameworkCore.PostgreSQL \
      && dotnet add package Npgsql.EntityFrameworkCore.PostgreSQL.NetTopologySuite \
      && dotnet add package Microsoft.EntityFrameworkCore.Design \
      && dotnet add package Microsoft.AspNetCore.Authentication.JwtBearer \
      && dotnet add package Confluent.Kafka \
      && dotnet add package prometheus-net.AspNetCore \
      && dotnet add package AspNetCore.HealthChecks.NpgSql \
      && cd ../Api.Tests \
      && dotnet add package Microsoft.AspNetCore.Mvc.Testing \
      && dotnet add package Testcontainers.PostgreSql \
      && cd /tmp && dotnet build Prewarm.sln \
      && rm -rf /tmp/Api /tmp/Api.Tests /tmp/Prewarm.sln; \
    fi \
    && chmod -R 1777 /cache

WORKDIR /work
