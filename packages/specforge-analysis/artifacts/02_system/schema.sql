-- Сгенерировано specforge-analysis из доменной модели. Не редактировать вручную:
-- изменения вносятся в доменную модель, затем этап 2 перезапускается.
-- Миграции EF Core (участник 3) строятся по этой схеме.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TYPE user_account_role AS ENUM ('driver', 'dispatcher');
CREATE TYPE parking_zone_data_source AS ENUM ('open_data', 'demo');
CREATE TYPE vehicle_position_source AS ENUM ('gps', 'mock');
CREATE TYPE parking_session_start_mode AS ENUM ('started', 'confirmed');
CREATE TYPE parking_session_status AS ENUM ('active', 'expiring', 'expired', 'closed');
CREATE TYPE parking_event_type AS ENUM ('zone_entered', 'zone_exited', 'zone_changed', 'session_started', 'session_extended', 'session_expiring', 'session_expired', 'session_closed');
CREATE TYPE notification_type AS ENUM ('zone_entered', 'zone_exited', 'zone_changed', 'session_paid_until', 'session_expiring', 'payment_required');

CREATE TABLE parking_zones (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    zone_number varchar(4) NOT NULL UNIQUE,
    name varchar(200),
    boundary geometry(Polygon, 4326) NOT NULL,
    hourly_rate numeric(12,2) CHECK (hourly_rate >= 0) CHECK (hourly_rate <= 10000),
    paid_hours varchar(100),
    max_duration_minutes integer CHECK (max_duration_minutes >= 1) CHECK (max_duration_minutes <= 1440),
    data_source parking_zone_data_source NOT NULL DEFAULT 'open_data',
    data_version varchar(50),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE demo_routes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name varchar(100) NOT NULL,
    path geometry(LineString, 4326) NOT NULL,
    point_interval_sec integer NOT NULL DEFAULT 10 CHECK (point_interval_sec >= 1) CHECK (point_interval_sec <= 60),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE vehicles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plate_number varchar(9) NOT NULL UNIQUE,
    model varchar(100),
    is_active boolean NOT NULL DEFAULT true,
    current_zone_id uuid REFERENCES parking_zones(id) ON DELETE RESTRICT,
    last_position_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE vehicle_positions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    vehicle_id uuid NOT NULL REFERENCES vehicles(id) ON DELETE RESTRICT,
    point geometry(Point, 4326) NOT NULL,
    recorded_at timestamptz NOT NULL,
    accuracy_m numeric(12,2) CHECK (accuracy_m >= 0) CHECK (accuracy_m <= 10000),
    speed_kmh numeric(12,2) CHECK (speed_kmh >= 0) CHECK (speed_kmh <= 300),
    source vehicle_position_source NOT NULL DEFAULT 'gps',
    zone_id uuid REFERENCES parking_zones(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE drivers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name varchar(150) NOT NULL,
    phone varchar(12),
    vehicle_id uuid REFERENCES vehicles(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE parking_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    vehicle_id uuid NOT NULL REFERENCES vehicles(id) ON DELETE RESTRICT,
    driver_id uuid NOT NULL REFERENCES drivers(id) ON DELETE RESTRICT,
    zone_id uuid NOT NULL REFERENCES parking_zones(id) ON DELETE RESTRICT,
    start_mode parking_session_start_mode NOT NULL,
    status parking_session_status NOT NULL DEFAULT 'active',
    started_at timestamptz NOT NULL,
    paid_until timestamptz NOT NULL,
    ended_at timestamptz,
    provider_session_id varchar(100),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE parking_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type parking_event_type NOT NULL,
    vehicle_id uuid NOT NULL REFERENCES vehicles(id) ON DELETE RESTRICT,
    zone_id uuid REFERENCES parking_zones(id) ON DELETE RESTRICT,
    previous_zone_id uuid REFERENCES parking_zones(id) ON DELETE RESTRICT,
    session_id uuid REFERENCES parking_sessions(id) ON DELETE RESTRICT,
    occurred_at timestamptz NOT NULL,
    payload jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_id uuid NOT NULL REFERENCES drivers(id) ON DELETE RESTRICT,
    event_id uuid UNIQUE REFERENCES parking_events(id) ON DELETE RESTRICT,
    type notification_type NOT NULL,
    text varchar(300) NOT NULL,
    read_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    login varchar(50) NOT NULL UNIQUE,
    password_hash varchar(200) NOT NULL,
    role user_account_role NOT NULL,
    driver_id uuid REFERENCES drivers(id) ON DELETE RESTRICT,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_demo_routes_path_gist ON demo_routes USING GIST (path);
CREATE INDEX ix_drivers_vehicle_id ON drivers (vehicle_id);
CREATE INDEX ix_notifications_driver_id ON notifications (driver_id);
CREATE INDEX ix_notifications_driver_id_read_at ON notifications (driver_id, read_at);
CREATE INDEX ix_notifications_event_id ON notifications (event_id);
CREATE INDEX ix_parking_events_previous_zone_id ON parking_events (previous_zone_id);
CREATE INDEX ix_parking_events_session_id ON parking_events (session_id);
CREATE INDEX ix_parking_events_type ON parking_events (type);
CREATE INDEX ix_parking_events_vehicle_id ON parking_events (vehicle_id);
CREATE INDEX ix_parking_events_vehicle_id_occurred_at ON parking_events (vehicle_id, occurred_at);
CREATE INDEX ix_parking_events_zone_id ON parking_events (zone_id);
CREATE INDEX ix_parking_sessions_driver_id ON parking_sessions (driver_id);
CREATE INDEX ix_parking_sessions_vehicle_id ON parking_sessions (vehicle_id);
CREATE INDEX ix_parking_sessions_vehicle_id_status ON parking_sessions (vehicle_id, status);
CREATE INDEX ix_parking_sessions_zone_id ON parking_sessions (zone_id);
CREATE INDEX ix_parking_zones_boundary_gist ON parking_zones USING GIST (boundary);
CREATE INDEX ix_user_accounts_driver_id ON user_accounts (driver_id);
CREATE INDEX ix_vehicle_positions_point_gist ON vehicle_positions USING GIST (point);
CREATE INDEX ix_vehicle_positions_vehicle_id ON vehicle_positions (vehicle_id);
CREATE INDEX ix_vehicle_positions_vehicle_id_recorded_at ON vehicle_positions (vehicle_id, recorded_at);
CREATE INDEX ix_vehicle_positions_zone_id ON vehicle_positions (zone_id);
CREATE INDEX ix_vehicles_current_zone_id ON vehicles (current_zone_id);

COMMENT ON TABLE user_accounts IS 'Учётная запись пользователя системы';
COMMENT ON TABLE notifications IS 'Уведомление водителю';
COMMENT ON TABLE parking_events IS 'Событие жизненного цикла: въезд, выход, смена зоны, этапы сессии';
COMMENT ON TABLE parking_sessions IS 'Парковочная сессия автомобиля в зоне';
COMMENT ON TABLE drivers IS 'Водитель служебного автомобиля';
COMMENT ON TABLE vehicle_positions IS 'Отметка местоположения автомобиля';
COMMENT ON TABLE vehicles IS 'Служебный автомобиль';
COMMENT ON TABLE demo_routes IS 'Демонстрационный mock-маршрут';
COMMENT ON TABLE parking_zones IS 'Зона платной парковки';
COMMENT ON COLUMN parking_zones.zone_number IS 'номер зоны';
COMMENT ON COLUMN parking_zones.name IS 'название или адресный ориентир';
COMMENT ON COLUMN parking_zones.boundary IS 'границы зоны';
COMMENT ON COLUMN parking_zones.hourly_rate IS 'стоимость часа, руб.';
COMMENT ON COLUMN parking_zones.paid_hours IS 'часы платного режима, например «пн–пт 08:00–20:00»';
COMMENT ON COLUMN parking_zones.max_duration_minutes IS 'ограничение длительности стоянки';
COMMENT ON COLUMN parking_zones.data_source IS 'источник геоданных';
COMMENT ON COLUMN parking_zones.data_version IS 'версия или дата снимка данных';
COMMENT ON COLUMN parking_zones.created_at IS 'время создания';
COMMENT ON COLUMN parking_zones.updated_at IS 'время изменения';
COMMENT ON COLUMN demo_routes.name IS 'название маршрута';
COMMENT ON COLUMN demo_routes.path IS 'точки маршрута';
COMMENT ON COLUMN demo_routes.point_interval_sec IS 'интервал между точками в реальном времени, с';
COMMENT ON COLUMN demo_routes.created_at IS 'время создания';
COMMENT ON COLUMN demo_routes.updated_at IS 'время изменения';
COMMENT ON COLUMN vehicles.plate_number IS 'госномер, например А123ВС178';
COMMENT ON COLUMN vehicles.model IS 'марка и модель';
COMMENT ON COLUMN vehicles.is_active IS 'в эксплуатации';
COMMENT ON COLUMN vehicles.current_zone_id IS 'текущая платная зона по последним координатам';
COMMENT ON COLUMN vehicles.last_position_at IS 'время последней координаты';
COMMENT ON COLUMN vehicles.created_at IS 'время создания';
COMMENT ON COLUMN vehicles.updated_at IS 'время изменения';
COMMENT ON COLUMN vehicle_positions.vehicle_id IS 'автомобиль (из пути запроса)';
COMMENT ON COLUMN vehicle_positions.point IS 'координата';
COMMENT ON COLUMN vehicle_positions.recorded_at IS 'время фиксации на устройстве';
COMMENT ON COLUMN vehicle_positions.accuracy_m IS 'точность, м';
COMMENT ON COLUMN vehicle_positions.speed_kmh IS 'скорость, км/ч';
COMMENT ON COLUMN vehicle_positions.source IS 'источник координаты';
COMMENT ON COLUMN vehicle_positions.zone_id IS 'зона, вычисленная для точки';
COMMENT ON COLUMN vehicle_positions.created_at IS 'время создания';
COMMENT ON COLUMN vehicle_positions.updated_at IS 'время изменения';
COMMENT ON COLUMN drivers.full_name IS 'ФИО';
COMMENT ON COLUMN drivers.phone IS 'телефон в формате +7XXXXXXXXXX';
COMMENT ON COLUMN drivers.vehicle_id IS 'закреплённый автомобиль';
COMMENT ON COLUMN drivers.created_at IS 'время создания';
COMMENT ON COLUMN drivers.updated_at IS 'время изменения';
COMMENT ON COLUMN parking_sessions.vehicle_id IS 'автомобиль';
COMMENT ON COLUMN parking_sessions.driver_id IS 'водитель (из токена)';
COMMENT ON COLUMN parking_sessions.zone_id IS 'зона';
COMMENT ON COLUMN parking_sessions.start_mode IS 'запущена в системе или подтверждена оплаченная';
COMMENT ON COLUMN parking_sessions.status IS 'статус';
COMMENT ON COLUMN parking_sessions.started_at IS 'начало';
COMMENT ON COLUMN parking_sessions.paid_until IS 'оплачено до';
COMMENT ON COLUMN parking_sessions.ended_at IS 'фактическое завершение';
COMMENT ON COLUMN parking_sessions.provider_session_id IS 'идентификатор сессии у поставщика';
COMMENT ON COLUMN parking_sessions.created_at IS 'время создания';
COMMENT ON COLUMN parking_sessions.updated_at IS 'время изменения';
COMMENT ON COLUMN parking_events.type IS 'тип события';
COMMENT ON COLUMN parking_events.vehicle_id IS 'автомобиль';
COMMENT ON COLUMN parking_events.zone_id IS 'зона';
COMMENT ON COLUMN parking_events.previous_zone_id IS 'предыдущая зона для zone_changed';
COMMENT ON COLUMN parking_events.session_id IS 'сессия';
COMMENT ON COLUMN parking_events.occurred_at IS 'время события';
COMMENT ON COLUMN parking_events.payload IS 'детали';
COMMENT ON COLUMN parking_events.created_at IS 'время создания';
COMMENT ON COLUMN parking_events.updated_at IS 'время изменения';
COMMENT ON COLUMN notifications.driver_id IS 'получатель';
COMMENT ON COLUMN notifications.event_id IS 'событие-основание, одно уведомление на событие';
COMMENT ON COLUMN notifications.type IS 'тип уведомления';
COMMENT ON COLUMN notifications.text IS 'текст, например «Вы въехали в зону платной парковки»';
COMMENT ON COLUMN notifications.read_at IS 'прочитано';
COMMENT ON COLUMN notifications.created_at IS 'время создания';
COMMENT ON COLUMN notifications.updated_at IS 'время изменения';
COMMENT ON COLUMN user_accounts.login IS 'логин';
COMMENT ON COLUMN user_accounts.password_hash IS 'хэш пароля (PBKDF2/bcrypt)';
COMMENT ON COLUMN user_accounts.role IS 'роль';
COMMENT ON COLUMN user_accounts.driver_id IS 'профиль водителя для роли driver';
COMMENT ON COLUMN user_accounts.is_active IS 'учётная запись активна';
COMMENT ON COLUMN user_accounts.created_at IS 'время создания';
COMMENT ON COLUMN user_accounts.updated_at IS 'время изменения';
