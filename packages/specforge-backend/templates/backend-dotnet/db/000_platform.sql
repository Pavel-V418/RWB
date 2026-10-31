-- =============================================================================
-- Платформенные таблицы золотого каркаса. Применяются ПЕРЕД доменной схемой.
-- Доменный DDL (artifacts/02_system/schema.sql) не должен их дублировать.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Transactional outbox: события пишутся в одной транзакции с данными,
-- фоновый OutboxPublisher отправляет их в Kafka и SignalR.
CREATE TABLE IF NOT EXISTS outbox_messages (
    id               uuid PRIMARY KEY,                 -- = eventId в Kafka
    event_type       varchar(64)  NOT NULL,
    event_version    smallint     NOT NULL,
    topic            varchar(128) NOT NULL,
    partition_key    varchar(128) NOT NULL,
    occurred_at      timestamptz  NOT NULL,
    envelope         jsonb        NOT NULL,            -- полное сообщение (оболочка + payload)
    created_at       timestamptz  NOT NULL DEFAULT now(),
    published_at     timestamptz,
    attempts         smallint     NOT NULL DEFAULT 0,
    last_error       varchar(1000),
    dead_lettered_at timestamptz                       -- после max попыток уходит в <topic>.dlq
);
CREATE INDEX IF NOT EXISTS ix_outbox_messages_pending
    ON outbox_messages (created_at) WHERE published_at IS NULL AND dead_lettered_at IS NULL;

-- Идемпотентность критичных HTTP-операций (заголовок Idempotency-Key).
CREATE TABLE IF NOT EXISTS idempotency_records (
    user_id          uuid         NOT NULL,
    key              varchar(64)  NOT NULL,
    request_hash     char(64)     NOT NULL,            -- sha256(метод + путь + тело)
    response_status  smallint     NOT NULL,
    response_body    text,
    response_content_type varchar(100),
    created_at       timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, key)
);
CREATE INDEX IF NOT EXISTS ix_idempotency_records_created ON idempotency_records (created_at);
