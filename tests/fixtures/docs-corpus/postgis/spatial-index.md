# Spatial indexing

PostGIS stores geometries in columns of type `geometry`. Without an index every spatial
predicate scans the whole table.

## Creating a GiST index

A GiST index speeds up bounding-box operators such as `&&` and functions like `ST_Intersects`
and `ST_DWithin`.

```sql
CREATE INDEX parking_zones_geom_idx
    ON parking_zones
    USING GIST (geom);

-- после загрузки данных обновите статистику
ANALYZE parking_zones;
```

## Nearest neighbour search

Use the `<->` distance operator in `ORDER BY` together with `LIMIT` for KNN queries.

```sql
SELECT id FROM parking_zones
ORDER BY geom <-> ST_SetSRID(ST_MakePoint(37.61, 55.75), 4326)
LIMIT 5;
```
