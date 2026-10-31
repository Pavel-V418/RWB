# syntax=docker/dockerfile:1.7
# Многостадийная сборка API. Контекст сборки — корень каркаса (где App.slnx).

FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src

# Сначала только файлы проектов — слой restore кэшируется, пока не меняются зависимости
COPY global.json Directory.Build.props Directory.Packages.props .editorconfig ./
COPY src/App.Domain/App.Domain.csproj src/App.Domain/
COPY src/App.Application/App.Application.csproj src/App.Application/
COPY src/App.Infrastructure/App.Infrastructure.csproj src/App.Infrastructure/
COPY src/App.Api/App.Api.csproj src/App.Api/
RUN --mount=type=cache,id=nuget,target=/root/.nuget/packages \
    dotnet restore src/App.Api/App.Api.csproj

COPY src/ src/
ARG APP_VERSION=0.0.0-local
RUN --mount=type=cache,id=nuget,target=/root/.nuget/packages \
    dotnet publish src/App.Api/App.Api.csproj -c Release -o /app --no-restore \
      -p:InformationalVersion=${APP_VERSION} -p:UseAppHost=false

FROM mcr.microsoft.com/dotnet/aspnet:10.0 AS runtime
WORKDIR /app
ENV ASPNETCORE_HTTP_PORTS=8080 \
    DOTNET_RUNNING_IN_CONTAINER=true
COPY --from=build /app .
# Платформенный SQL кладётся в образ: его применяет init-контейнер БД в docker-compose
COPY db/ /app/db/
USER $APP_UID
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD ["dotnet", "App.Api.dll", "--healthcheck"]
ENTRYPOINT ["dotnet", "App.Api.dll"]
