-- =============================================================================
-- Эталонная схема БД. Кейс №1: помощник управления платными парковками СПб.
-- СУБД: PostgreSQL 17.9 + PostGIS 3.5. Все координаты: WGS84 (SRID 4326), lon/lat.
-- Соглашения: snake_case, PK uuid (кроме высокочастотных точек трека),
-- время в timestamptz (UTC), деньги в копейках (integer), перечисления через CHECK.
-- Платформенные таблицы (outbox_messages, idempotency_records) задаёт каркас, здесь их нет.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto; -- gen_random_uuid()

-- -----------------------------------------------------------------------------
-- Пользователи и роли (JWT: driver, admin)
-- -----------------------------------------------------------------------------
CREATE TABLE users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    login           varchar(64)  NOT NULL UNIQUE
                    CHECK (login ~ '^[a-z0-9._-]{3,64}$'),
    password_hash   varchar(255) NOT NULL,
    role            varchar(16)  NOT NULL CHECK (role IN ('driver', 'admin')),
    full_name       varchar(200) NOT NULL,
    phone           varchar(16)  CHECK (phone ~ '^\+7[0-9]{10}$'),
    is_active       boolean      NOT NULL DEFAULT true,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- Служебные автомобили
-- -----------------------------------------------------------------------------
CREATE TABLE vehicles (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Госномер РФ: буквы, совпадающие с латиницей, хранятся кириллицей в верхнем регистре
    plate_number    varchar(9)   NOT NULL UNIQUE
                    CHECK (plate_number ~ '^[АВЕКМНОРСТУХ][0-9]{3}[АВЕКМНОРСТУХ]{2}[0-9]{2,3}$'),
    vehicle_type    varchar(8)   NOT NULL DEFAULT 'car'
                    CHECK (vehicle_type IN ('car', 'truck', 'bus')),
    model           varchar(100),
    driver_id       uuid         REFERENCES users (id) ON DELETE SET NULL,
    is_active       boolean      NOT NULL DEFAULT true,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_vehicles_driver ON vehicles (driver_id);

-- -----------------------------------------------------------------------------
-- Парковочные зоны (справочник, синхронизируется из провайдера «Паркоматика»).
-- Геометрия в данных провайдера — в основном LINESTRING/MULTILINESTRING вдоль улиц,
-- поэтому «нахождение в зоне» = расстояние до геометрии <= detection_radius_m.
-- -----------------------------------------------------------------------------
CREATE TABLE parking_zones (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider            varchar(32)  NOT NULL DEFAULT 'parkomatika',
    external_zone_id    varchar(32)  NOT NULL,          -- номер зоны, показывается водителю
    city                varchar(32)  NOT NULL,          -- spb | moscow_partner | ...
    display_name        varchar(300) NOT NULL,          -- адрес
    geometry            geometry(Geometry, 4326) NOT NULL,
    detection_radius_m  smallint     NOT NULL DEFAULT 20 CHECK (detection_radius_m BETWEEN 1 AND 100),
    base_price_kopecks  integer      NOT NULL CHECK (base_price_kopecks >= 0), -- за час
    price_description   varchar(500),
    min_time_minutes    smallint     NOT NULL CHECK (min_time_minutes > 0),
    time_step_minutes   smallint     NOT NULL CHECK (time_step_minutes > 0),
    has_holidays        boolean      NOT NULL DEFAULT true, -- в праздники бесплатно
    entity_state        varchar(16)  NOT NULL DEFAULT 'Active'
                        CHECK (entity_state IN ('Active', 'Inactive')),
    provider_init_date  timestamptz,
    last_synced_at      timestamptz  NOT NULL DEFAULT now(),
    created_at          timestamptz  NOT NULL DEFAULT now(),
    updated_at          timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uq_parking_zones_provider_ext UNIQUE (provider, external_zone_id)
);
-- Индекс по geography: ST_DWithin в метрах без перепроецирования
CREATE INDEX ix_parking_zones_geog ON parking_zones USING gist ((geometry::geography));
-- Индекс по geometry: выборка по bbox для карты
CREATE INDEX ix_parking_zones_geom ON parking_zones USING gist (geometry);
CREATE INDEX ix_parking_zones_city ON parking_zones (city) WHERE entity_state = 'Active';

-- Тарифные периоды зоны (по типу ТС и времени суток)
CREATE TABLE zone_tariff_periods (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    zone_id             uuid        NOT NULL REFERENCES parking_zones (id) ON DELETE CASCADE,
    vehicle_type        varchar(8)  NOT NULL CHECK (vehicle_type IN ('car', 'truck', 'bus')),
    from_minute         smallint    CHECK (from_minute BETWEEN 0 AND 1440),  -- минуты от полуночи; null = круглосуточно
    to_minute           smallint    CHECK (to_minute BETWEEN 0 AND 1440),
    duration_minutes    smallint    CHECK (duration_minutes > 0),            -- null = тариф без ограничения длительности
    price_kopecks       integer     NOT NULL CHECK (price_kopecks >= 0),     -- за час
    CONSTRAINT ck_tariff_period_bounds CHECK ((from_minute IS NULL) = (to_minute IS NULL))
);
CREATE INDEX ix_zone_tariff_periods_zone ON zone_tariff_periods (zone_id, vehicle_type);

-- -----------------------------------------------------------------------------
-- GPS-трек автомобиля (маршрут). Высокочастотная таблица → bigint identity.
-- -----------------------------------------------------------------------------
CREATE TABLE vehicle_positions (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vehicle_id      uuid          NOT NULL REFERENCES vehicles (id) ON DELETE CASCADE,
    recorded_at     timestamptz   NOT NULL,             -- время фиксации на устройстве
    received_at     timestamptz   NOT NULL DEFAULT now(),
    location        geometry(Point, 4326) NOT NULL,
    accuracy_m      real          CHECK (accuracy_m >= 0),
    speed_kmh       real          CHECK (speed_kmh >= 0),
    heading_deg     real          CHECK (heading_deg >= 0 AND heading_deg < 360),
    zone_id         uuid          REFERENCES parking_zones (id) ON DELETE SET NULL, -- зона в момент точки
    source          varchar(16)   NOT NULL DEFAULT 'gps' CHECK (source IN ('gps', 'mock')),
    CONSTRAINT uq_vehicle_positions_point UNIQUE (vehicle_id, recorded_at) -- идемпотентность повторной отправки
);
CREATE INDEX ix_vehicle_positions_vehicle_time ON vehicle_positions (vehicle_id, recorded_at DESC);

-- Текущее состояние автомобиля относительно зон (для определения переходов)
CREATE TABLE vehicle_zone_states (
    vehicle_id          uuid PRIMARY KEY REFERENCES vehicles (id) ON DELETE CASCADE,
    current_zone_id     uuid        REFERENCES parking_zones (id) ON DELETE SET NULL, -- null = вне платных зон
    entered_at          timestamptz,
    last_location       geometry(Point, 4326),
    last_position_at    timestamptz,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- Парковочные сессии
-- -----------------------------------------------------------------------------
CREATE TABLE parking_sessions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    vehicle_id          uuid        NOT NULL REFERENCES vehicles (id),
    driver_id           uuid        REFERENCES users (id) ON DELETE SET NULL,
    zone_id             uuid        NOT NULL REFERENCES parking_zones (id),
    status              varchar(16) NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'expired', 'closed', 'cancelled')),
    started_at          timestamptz NOT NULL,
    paid_until          timestamptz NOT NULL,
    ended_at            timestamptz,
    close_reason        varchar(16) CHECK (close_reason IN ('manual', 'zone_exit', 'zone_changed', 'expired', 'cancelled')),
    duration_minutes    smallint    NOT NULL CHECK (duration_minutes > 0),
    amount_kopecks      integer     NOT NULL DEFAULT 0 CHECK (amount_kopecks >= 0),
    payment_provider    varchar(32) NOT NULL DEFAULT 'parkomatika',
    external_payment_id varchar(64),                   -- идентификатор оплаты у провайдера (mock)
    expiry_warned_at    timestamptz,                   -- когда отправлено предупреждение «осталось N минут»
    idempotency_key     varchar(64) UNIQUE,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_sessions_time CHECK (paid_until > started_at),
    CONSTRAINT ck_sessions_end  CHECK ((status = 'active') = (ended_at IS NULL))
);
-- Не более одной активной сессии на автомобиль
CREATE UNIQUE INDEX uq_parking_sessions_active_vehicle ON parking_sessions (vehicle_id) WHERE status = 'active';
-- Для таймера предупреждений/истечения
CREATE INDEX ix_parking_sessions_active_paid_until ON parking_sessions (paid_until) WHERE status = 'active';
CREATE INDEX ix_parking_sessions_vehicle_started ON parking_sessions (vehicle_id, started_at DESC);

-- -----------------------------------------------------------------------------
-- Журнал доменных событий для истории в админке. id совпадает с eventId в Kafka.
-- Доставка в Kafka идёт через платформенную таблицу outbox_messages (db/000_platform.sql каркаса).
-- -----------------------------------------------------------------------------
CREATE TABLE domain_events (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      varchar(64)  NOT NULL CHECK (event_type ~ '^[A-Z][A-Za-z]{2,63}$'),
    event_version   smallint     NOT NULL DEFAULT 1,
    occurred_at     timestamptz  NOT NULL,
    vehicle_id      uuid         REFERENCES vehicles (id) ON DELETE SET NULL,
    session_id      uuid         REFERENCES parking_sessions (id) ON DELETE SET NULL,
    zone_id         uuid         REFERENCES parking_zones (id) ON DELETE SET NULL,
    payload         jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_domain_events_vehicle_time ON domain_events (vehicle_id, occurred_at DESC);
CREATE INDEX ix_domain_events_type_time ON domain_events (event_type, occurred_at DESC);

-- Уведомления водителю (тексты из кейса)
CREATE TABLE notifications (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid         REFERENCES users (id) ON DELETE CASCADE,
    vehicle_id      uuid         NOT NULL REFERENCES vehicles (id) ON DELETE CASCADE,
    event_id        uuid         REFERENCES domain_events (id) ON DELETE SET NULL,
    kind            varchar(32)  NOT NULL CHECK (kind IN (
                        'zone_entered', 'session_paid', 'session_expiring',
                        'session_expired', 'zone_exited', 'zone_changed')),
    severity        varchar(8)   NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warning')),
    title           varchar(120) NOT NULL,
    body            varchar(500) NOT NULL,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    read_at         timestamptz
);
CREATE INDEX ix_notifications_vehicle_time ON notifications (vehicle_id, created_at DESC);
CREATE INDEX ix_notifications_unread ON notifications (user_id) WHERE read_at IS NULL;

-- -----------------------------------------------------------------------------
-- Эталонный геозапрос: зона по точке (используется backend-ом).
-- Ближайшая активная зона в пределах её detection_radius_m.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION find_zone_at(p_lon double precision, p_lat double precision)
RETURNS TABLE (zone_id uuid, distance_m double precision)
LANGUAGE sql STABLE AS $$
    SELECT z.id, ST_Distance(z.geometry::geography, ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::geography)
    FROM parking_zones z
    WHERE z.entity_state = 'Active'
      -- постоянный радиус = максимум detection_radius_m, чтобы работал GiST-индекс
      AND ST_DWithin(z.geometry::geography, ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::geography, 100)
      AND ST_Distance(z.geometry::geography, ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::geography) <= z.detection_radius_m
    ORDER BY 2
    LIMIT 1
$$;
