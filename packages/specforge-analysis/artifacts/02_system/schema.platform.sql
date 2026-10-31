-- Служебные таблицы каркаса backend (справочно; в schema.sql не входят)
-- Transactional outbox: события пишутся в одной транзакции с данными,
-- фоновый публикатор отправляет их в Kafka (at-least-once).
CREATE TABLE outbox_events (
    id uuid PRIMARY KEY,
    type varchar(100) NOT NULL,
    version integer NOT NULL,
    key varchar(100) NOT NULL,
    payload jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz
);
CREATE INDEX ix_outbox_events_unpublished ON outbox_events (occurred_at) WHERE published_at IS NULL;

-- Идемпотентность критичных операций (заголовок Idempotency-Key).
CREATE TABLE idempotency_keys (
    key uuid PRIMARY KEY,
    operation varchar(100) NOT NULL,
    response_status integer NOT NULL,
    response_body jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

