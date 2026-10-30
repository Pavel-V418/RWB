# specforge/postgis:17 — песочница этапа БД: PostgreSQL 17 + PostGIS, psql, git и pg-up.
# Песочница запускает контейнер командой `sleep infinity`, сервер БД сам не стартует — шлюз
# поднимает его скриптом: gate_commands=["pg-up && psql -v ON_ERROR_STOP=1 -f db/schema.sql"].
# pg-up идемпотентен; база $PGDATABASE с расширением postgis.
# Сборка: infra/images/build.sh postgis
ARG POSTGIS_TAG=17-3.5
FROM postgis/postgis:${POSTGIS_TAG}

ENV PGHOST=/tmp \
    PGPORT=5432 \
    PGUSER=postgres \
    PGDATABASE=app \
    PGDATA=/tmp/pgdata

RUN apt-get update \
    && apt-get install -y --no-install-recommends git coreutils \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system user.email "sandbox@specforge.local" \
    && git config --system user.name "specforge" \
    && mkdir -p /cache/nuget /cache/npm /cache/gradle /cache/pip /cache/trivy /work \
    && chmod -R 1777 /cache

COPY pg-up /usr/local/bin/pg-up
RUN chmod 0755 /usr/local/bin/pg-up

WORKDIR /work
ENTRYPOINT []
CMD ["sleep", "infinity"]
