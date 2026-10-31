-- Демо-данные стенда кейса 1 (только для dev/стенда, не для prod).
-- Учётки (тестовые пароли стенда):
--   admin       / Admin-demo-2026!   — администратор
--   driver.demo / Driver-demo-2026!  — водитель демо-машины
-- Машина veh-demo-001: id 0192f000-0000-7000-8000-00000000de01, госномер А001АА178, водитель driver.demo.
-- Маршрут e2e (участник 5) проходит через реальные зоны из сида:
--   514725 «Казанская пл. дом 2» (у Казанского собора) и 514660 «Садовая ул. дом 17» (у Гостиного двора).
BEGIN;

INSERT INTO users (id, login, password_hash, role, full_name, phone) VALUES
    ('0192f000-0000-7000-8000-00000000ad01', 'admin', 'pbkdf2$100000$Fbvpz2oerhQr9DpJKYIzOw==$RJeWZGPYIHZ+m+g/oj1t3SZKuht2AotfTDI5U3Bmrmw=', 'admin', 'Администратор стенда', NULL),
    ('0192f000-0000-7000-8000-00000000d201', 'driver.demo', 'pbkdf2$100000$HNCYWte/MyFsVTVpmpc61A==$B+oPUUlQKF6XDo0kb/axl8zmO7hLUynjY8p6PJxeRAw=', 'driver', 'Демо Водитель', '+79990000001')
ON CONFLICT (login) DO NOTHING;

INSERT INTO vehicles (id, plate_number, vehicle_type, model, driver_id) VALUES
    ('0192f000-0000-7000-8000-00000000de01', 'А001АА178', 'car', 'veh-demo-001 (Lada Vesta)', '0192f000-0000-7000-8000-00000000d201')
ON CONFLICT (plate_number) DO NOTHING;

COMMIT;
