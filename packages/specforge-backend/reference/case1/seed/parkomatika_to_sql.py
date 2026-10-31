#!/usr/bin/env python3
"""Конвертирует выгрузку зон «Паркоматики» (response.json / spb.json) в SQL-сид.

Использование:
    python3 parkomatika_to_sql.py spb.json > zones_spb.sql
    psql -f zones_spb.sql

Формат входа: {"result": "Success", "data": {"city": "...", "zones": [...]}}.
Цены провайдера уже в копейках, from/to тарифа в минутах от полуночи.
"""
import json
import sys


def q(value):
    """SQL-литерал для COPY (text format)."""
    if value is None:
        return r"\N"
    s = str(value)
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def main(path):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if doc.get("result") != "Success":
        sys.exit(f"{path}: result={doc.get('result')!r}, message={doc.get('message')!r}")

    city = doc["data"]["city"]
    zones = doc["data"]["zones"]
    out = sys.stdout

    out.write("BEGIN;\n")
    out.write("CREATE TEMP TABLE stg_zones (external_zone_id text, display_name text, wkt text, price int,"
              " price_description text, min_time int, time_step int, has_holidays boolean, entity_state text,"
              " init_date timestamptz, last_synced_at timestamptz) ON COMMIT DROP;\n")
    out.write("CREATE TEMP TABLE stg_periods (external_zone_id text, vehicle_type text, from_minute int,"
              " to_minute int, duration int, price int) ON COMMIT DROP;\n")

    out.write("COPY stg_zones FROM stdin;\n")
    for z in zones:
        tariff = z.get("tariff") or {}
        out.write("\t".join(q(v) for v in (
            z["externalZoneId"], z["displayName"], z["geometry"], z["price"], z.get("priceDescription"),
            z["minTime"], z["timeStep"], "t" if tariff.get("hasHolidays", True) else "f",
            z.get("entityState", "Active"), z.get("initDate"), z.get("lastSyncedAt"),
        )) + "\n")
    out.write("\\.\n")

    out.write("COPY stg_periods FROM stdin;\n")
    for z in zones:
        for p in (z.get("tariff") or {}).get("periods", []):
            out.write("\t".join(q(v) for v in (
                z["externalZoneId"], p["type"], p.get("from"), p.get("to"), p.get("duration"), p["price"],
            )) + "\n")
    out.write("\\.\n")

    # Upsert: повторный запуск обновляет справочник, не дублируя зоны
    out.write(f"""
INSERT INTO parking_zones (provider, external_zone_id, city, display_name, geometry, base_price_kopecks,
    price_description, min_time_minutes, time_step_minutes, has_holidays, entity_state,
    provider_init_date, last_synced_at)
SELECT 'parkomatika', external_zone_id, '{city}', display_name, ST_GeomFromText(wkt, 4326), price,
    price_description, min_time, time_step, has_holidays, entity_state, init_date, last_synced_at
FROM stg_zones
ON CONFLICT (provider, external_zone_id) DO UPDATE SET
    display_name = EXCLUDED.display_name, geometry = EXCLUDED.geometry,
    base_price_kopecks = EXCLUDED.base_price_kopecks, price_description = EXCLUDED.price_description,
    min_time_minutes = EXCLUDED.min_time_minutes, time_step_minutes = EXCLUDED.time_step_minutes,
    has_holidays = EXCLUDED.has_holidays, entity_state = EXCLUDED.entity_state,
    last_synced_at = EXCLUDED.last_synced_at, updated_at = now();

DELETE FROM zone_tariff_periods t USING parking_zones z, stg_zones s
WHERE t.zone_id = z.id AND z.provider = 'parkomatika' AND z.external_zone_id = s.external_zone_id;

INSERT INTO zone_tariff_periods (zone_id, vehicle_type, from_minute, to_minute, duration_minutes, price_kopecks)
SELECT z.id, p.vehicle_type, p.from_minute, p.to_minute, p.duration, p.price
FROM stg_periods p JOIN parking_zones z ON z.provider = 'parkomatika' AND z.external_zone_id = p.external_zone_id;
COMMIT;
""")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
