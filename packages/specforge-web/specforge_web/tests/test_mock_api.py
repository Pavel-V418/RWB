import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.tools.mock_api import MockApi, _bump_digits  # noqa: E402

DOC = {
    "openapi": "3.1.0", "info": {"title": "t", "version": "1"},
    "paths": {"/areas": {"get": {"operationId": "listAreas", "responses": {"200": {"description": "OK", "content": {
        "application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/Area"}}}}}}}}},
    "components": {"schemas": {
        "P": {"type": "object", "required": ["type", "coordinates"], "properties": {
            "type": {"const": "Point"}, "coordinates": {"type": "array", "items": {"type": "number"}, "examples": [[12.5, 41.9]]}}},
        "G": {"type": "object", "properties": {"type": {"type": "string", "enum": ["Polygon", "MultiPolygon"]},
                                                "coordinates": {"type": "array", "items": {}}}},
        "Area": {"type": "object", "properties": {
            "id": {"type": "string"}, "code": {"type": "string", "pattern": "^[A-Z]{2}[0-9]{3}$", "examples": ["AB123"]},
            "state": {"type": "string", "enum": ["on", "off"]}, "endsAt": {"type": "string", "format": "date-time"},
            "startedAt": {"type": "string", "format": "date-time"},
            "center": {"$ref": "#/components/schemas/P"}, "shape": {"$ref": "#/components/schemas/G"}}}}},
}


def test_bump_digits_keeps_shape():
    assert _bump_digits("X123YZ45", 2) == "X125YZ45"
    assert _bump_digits("AB-0099", 3) == "AB-0102"
    assert _bump_digits("abc", 1) == "abc"


def test_list_items_differ_and_geometry_is_drawable():
    code, items = MockApi(DOC).handle("GET", "/api/v1/areas")
    assert code == 200 and len(items) == 5
    assert len({x["id"] for x in items}) == 5
    assert len({x["code"] for x in items}) == 5 and all(re.match(r"^[A-Z]{2}[0-9]{3}$", x["code"]) for x in items)
    assert {x["state"] for x in items} == {"on", "off"}
    assert len({tuple(x["center"]["coordinates"]) for x in items}) == 5
    assert all(x["endsAt"] > x["startedAt"] for x in items)
    ring = items[0]["shape"]["coordinates"][0] if items[0]["shape"]["type"] == "Polygon" else items[0]["shape"]["coordinates"][0][0]
    assert len(ring) == 5 and ring[0] == ring[-1]
    assert abs(ring[0][0] - 12.5) < 0.05 and abs(ring[0][1] - 41.9) < 0.05  # вокруг точки-примера из спеки


def test_synthetic_cases_have_no_empty_geometry():
    for case in ("synthetic-inspection",):
        api = yaml.safe_load((ROOT / "fixtures" / case / "02_system" / "openapi.yaml").read_text(encoding="utf-8"))
        m = MockApi(api)
        for meth, _, op in m.routes:
            if meth == "GET" and not op.path_params and op.response_kind in ("page", "array"):
                code, body = m.handle("GET", "/api/v1" + op.path)
                items = body["items"] if isinstance(body, dict) else body
                for it in items:
                    for v in it.values():
                        if isinstance(v, dict) and "coordinates" in v:
                            assert v["coordinates"], (op.op_id, v)


def test_created_item_is_found_in_list_by_search():
    api = yaml.safe_load((ROOT / "fixtures" / "synthetic-taxi" / "02_system" / "openapi.yaml").read_text(encoding="utf-8"))
    m = MockApi(api)
    body = '{"passengerPhone": "+79990001122", "pickupAddress": "E2E-abc", "tariff": "comfort"}'.encode()
    code, created = m.handle("POST", "/api/v1/orders", body)
    assert code == 201 and created["pickupAddress"] == "E2E-abc" and created["tariff"] == "comfort"
    code, page = m.handle("GET", "/api/v1/orders?q=e2e-abc&page=1&size=20")
    assert page["total"] == 1 and page["items"][0]["id"] == created["id"]
    code, one = m.handle("GET", f"/api/v1/orders/{created['id']}")
    assert one["pickupAddress"] == "E2E-abc"
    code, page = m.handle("GET", "/api/v1/orders?status=completed")
    assert page["items"] and all(x["status"] == "completed" for x in page["items"])
