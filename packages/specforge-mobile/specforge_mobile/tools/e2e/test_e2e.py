"""Тесты оракула e2e без стенда: python -m unittest specforge_mobile.tools.e2e.test_e2e -v"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from specforge_mobile.tools.e2e import oracle
from specforge_mobile.tools.route_player import geo_scenario as geo

SCENARIO = Path(__file__).resolve().parents[3] / "reference/case1/mock_route/scenario.json"


def load():
    sc = json.loads(SCENARIO.read_text(encoding="utf-8"))
    return sc, geo.load_zones(sc, SCENARIO.parent)


class Oracle(unittest.TestCase):
    def test_backend_rules_order(self):
        sc, zones = load()
        _, _, events, notes = oracle.build(sc, zones, {})
        self.assertEqual(notes, [])
        self.assertEqual([e["type"] for e in events],
                         ["ZoneEntered", "SessionStarted", "SessionExtended", "SessionExpiringSoon",
                          "ZoneExited", "SessionClosed", "ZoneChanged", "ZoneExited"])

    def test_fit_minutes(self):
        self.assertEqual(oracle.fit_minutes(15, 60, 15), 60)
        self.assertEqual(oracle.fit_minutes(70, 60, 15), 75)
        self.assertEqual(oracle.fit_minutes(10, None, 15), 15)

    def test_zone_min_time_extends_dwell_so_warning_still_happens(self):
        sc, zones = load()
        params = {"514725": {"minTimeMinutes": 60, "timeStepMinutes": 15}}
        sc2, _, events, notes = oracle.build(sc, zones, params)
        start = next(a for a in sc2["actions"] if a["type"] == "startSession")
        self.assertEqual(start["paidMinutes"], 60)
        self.assertTrue(any("стоянка" in n for n in notes), notes)
        types = [e["type"] for e in events]
        self.assertLess(types.index("SessionExpiringSoon"), types.index("ZoneExited"))

    def test_restamp_is_increasing_and_scaled(self):
        pts = [{"_tSec": 0.0, "recordedAt": "x"}, {"_tSec": 10.0, "recordedAt": "x"}]
        start = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
        out = oracle.restamp(pts, start, 10)
        self.assertEqual(out[0]["recordedAt"], "2026-10-31T12:00:00.000Z")
        self.assertEqual(out[1]["recordedAt"], "2026-10-31T12:00:01.000Z")

    def test_match_timing_order_and_same_time_group(self):
        start = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
        ts = lambda s: (start + timedelta(seconds=s)).isoformat().replace("+00:00", "Z")  # noqa: E731
        expected = [
            {"logical": "zoneEntered", "type": "ZoneEntered", "atSec": 100, "toleranceSec": 5, "data": {"zoneId": "A"}},
            {"logical": "zoneExited", "type": "ZoneExited", "atSec": 500, "toleranceSec": 5, "data": {"zoneId": "A"}},
            {"logical": "sessionClosed", "type": "SessionClosed", "atSec": 500, "toleranceSec": 5, "data": {}},
            {"logical": "sessionExpiring", "type": "SessionExpiringSoon", "atSec": 400, "toleranceSec": 30, "data": {}},
        ]
        observed = [  # закрытие записано раньше выезда — допустимо: одно ожидаемое время
            {"type": "ZoneEntered", "zoneId": "uA", "occurredAt": ts(10.5)},
            {"type": "SessionClosed", "occurredAt": ts(50.2)},
            {"type": "ZoneExited", "zoneId": "uA", "occurredAt": ts(50.4)},
        ]
        res = oracle.match(expected, observed, start, 10, {"A": "uA"}, skip_logical={"sessionExpiring"})
        self.assertEqual([m["ok"] for m in res], [True, True, True, None])
        late = [{"type": "ZoneEntered", "zoneId": "uA", "occurredAt": ts(30)}]
        res = oracle.match(expected[:1], late, start, 10, {"A": "uA"})
        self.assertFalse(res[0]["ok"])
        self.assertIn("ожидали 10.0", res[0]["why"])
        wrong_zone = [{"type": "ZoneEntered", "zoneId": "uB", "occurredAt": ts(10)}]
        self.assertIsNone(oracle.match(expected[:1], wrong_zone, start, 10, {"A": "uA"})[0]["found"])


if __name__ == "__main__":
    unittest.main()
