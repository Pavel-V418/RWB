-- Сгенерировано specforge-analysis из доменной модели. Не редактировать вручную:
-- изменения вносятся в доменную модель, затем этап 2 перезапускается.
-- Миграции EF Core (участник 3) строятся по этой схеме.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE employees (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name varchar(200) NOT NULL,
    pass_number varchar(32) NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tools (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    inventory_number varchar(50) NOT NULL UNIQUE,
    name varchar(200) NOT NULL,
    category varchar(100) NOT NULL,
    warehouse varchar(100) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'available' CHECK (status IN ('available', 'issued', 'reserved', 'repair', 'lost', 'retired')),
    qr_code varchar(255) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE reservations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_id uuid NOT NULL REFERENCES tools(id) ON DELETE RESTRICT,
    employee_id uuid NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    date date NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled', 'fulfilled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE issue_records (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_id uuid NOT NULL REFERENCES tools(id) ON DELETE RESTRICT,
    employee_id uuid NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    issue_date timestamptz NOT NULL,
    return_deadline timestamptz NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'returned', 'overdue')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE return_records (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_record_id uuid NOT NULL REFERENCES issue_records(id) ON DELETE RESTRICT,
    tool_id uuid NOT NULL REFERENCES tools(id) ON DELETE RESTRICT,
    employee_id uuid NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    return_date timestamptz NOT NULL,
    condition varchar(16) NOT NULL CHECK (condition IN ('working', 'needs_repair', 'lost')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sync_queues (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_type varchar(18) NOT NULL CHECK (operation_type IN ('issue', 'return', 'reservation', 'reservation_cancel')),
    payload jsonb NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'synced', 'rejected')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE system_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key varchar(100) NOT NULL UNIQUE,
    value varchar(500) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username varchar(50) NOT NULL UNIQUE,
    password_hash varchar(255) NOT NULL,
    role varchar(17) NOT NULL DEFAULT 'employee' CHECK (role IN ('warehouse_keeper', 'warehouse_manager', 'employee', 'system_admin')),
    employee_id uuid REFERENCES employees(id) ON DELETE RESTRICT,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE movement_histories (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_id uuid NOT NULL REFERENCES tools(id) ON DELETE RESTRICT,
    event_type varchar(21) NOT NULL CHECK (event_type IN ('created', 'issued', 'returned', 'reserved', 'reservation_cancelled', 'retired')),
    timestamp timestamptz NOT NULL,
    user_id uuid REFERENCES users(id) ON DELETE RESTRICT,
    payload jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_logs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid REFERENCES users(id) ON DELETE RESTRICT,
    action varchar(100) NOT NULL,
    entity_type varchar(50) NOT NULL,
    entity_id uuid,
    timestamp timestamptz NOT NULL,
    details jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

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

CREATE INDEX ix_audit_logs_entity_type_entity_id ON audit_logs (entity_type, entity_id);
CREATE INDEX ix_audit_logs_user_id ON audit_logs (user_id);
CREATE INDEX ix_audit_logs_user_id_timestamp ON audit_logs (user_id, timestamp);
CREATE INDEX ix_employees_pass_number ON employees (pass_number);
CREATE INDEX ix_issue_records_employee_id ON issue_records (employee_id);
CREATE INDEX ix_issue_records_return_deadline_status ON issue_records (return_deadline, status);
CREATE INDEX ix_issue_records_tool_id ON issue_records (tool_id);
CREATE INDEX ix_issue_records_tool_id_status ON issue_records (tool_id, status);
CREATE INDEX ix_movement_histories_tool_id ON movement_histories (tool_id);
CREATE INDEX ix_movement_histories_tool_id_timestamp ON movement_histories (tool_id, timestamp);
CREATE INDEX ix_movement_histories_user_id ON movement_histories (user_id);
CREATE INDEX ix_reservations_employee_id ON reservations (employee_id);
CREATE INDEX ix_reservations_tool_id ON reservations (tool_id);
CREATE INDEX ix_reservations_tool_id_date_status ON reservations (tool_id, date, status);
CREATE INDEX ix_return_records_employee_id ON return_records (employee_id);
CREATE INDEX ix_return_records_issue_record_id ON return_records (issue_record_id);
CREATE INDEX ix_return_records_tool_id ON return_records (tool_id);
CREATE INDEX ix_sync_queues_status ON sync_queues (status);
CREATE INDEX ix_system_settings_key ON system_settings (key);
CREATE INDEX ix_tools_category ON tools (category);
CREATE INDEX ix_tools_inventory_number ON tools (inventory_number);
CREATE INDEX ix_tools_status ON tools (status);
CREATE INDEX ix_users_employee_id ON users (employee_id);
CREATE INDEX ix_users_username ON users (username);

COMMENT ON TABLE audit_logs IS 'Журнал аудита действий пользователей';
COMMENT ON TABLE movement_histories IS 'История движения каждой единицы инструмента';
COMMENT ON TABLE users IS 'Учётная запись пользователя системы с ролью и привязкой к сотруднику';
COMMENT ON TABLE system_settings IS 'Настройки системы, включая срок возврата';
COMMENT ON TABLE sync_queues IS 'Очередь локальных операций для синхронизации после восстановления связи';
COMMENT ON TABLE return_records IS 'Запись о возврате инструмента с оценкой состояния';
COMMENT ON TABLE issue_records IS 'Запись о выдаче инструмента сотруднику';
COMMENT ON TABLE reservations IS 'Бронирование инструмента на дату';
COMMENT ON TABLE tools IS 'Единица инструмента или оборудования в каталоге';
COMMENT ON TABLE employees IS 'Сотрудник, получающий инструмент; данные загружаются из CSV-файла';
COMMENT ON COLUMN employees.created_at IS 'время создания';
COMMENT ON COLUMN employees.updated_at IS 'время изменения';
COMMENT ON COLUMN tools.created_at IS 'время создания';
COMMENT ON COLUMN tools.updated_at IS 'время изменения';
COMMENT ON COLUMN reservations.created_at IS 'время создания';
COMMENT ON COLUMN reservations.updated_at IS 'время изменения';
COMMENT ON COLUMN issue_records.created_at IS 'время создания';
COMMENT ON COLUMN issue_records.updated_at IS 'время изменения';
COMMENT ON COLUMN return_records.created_at IS 'время создания';
COMMENT ON COLUMN return_records.updated_at IS 'время изменения';
COMMENT ON COLUMN sync_queues.updated_at IS 'время изменения';
COMMENT ON COLUMN system_settings.created_at IS 'время создания';
COMMENT ON COLUMN system_settings.updated_at IS 'время изменения';
COMMENT ON COLUMN users.created_at IS 'время создания';
COMMENT ON COLUMN users.updated_at IS 'время изменения';
COMMENT ON COLUMN movement_histories.created_at IS 'время создания';
COMMENT ON COLUMN movement_histories.updated_at IS 'время изменения';
COMMENT ON COLUMN audit_logs.created_at IS 'время создания';
COMMENT ON COLUMN audit_logs.updated_at IS 'время изменения';
