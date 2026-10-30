"""Примеры прогонов в examples/: ТЗ кейса 1 и приложенные данные; run --data кладёт их во вход."""

import json
from pathlib import Path

import pytest

from specforge.core.artifacts import INPUT_DATA_DIR
from specforge.orchestrator.graph import OrchestratorError, build_graph, start_run
from specforge.orchestrator.registry import Registry
from tests.test_orchestrator import FakeAgent, Harness, spec

CASE1 = Path(__file__).resolve().parent.parent / "examples" / "case1"


def test_case1_requirements_and_data() -> None:
    tz = (CASE1 / "tz.md").read_text(encoding="utf-8")
    section = tz.split("## 3. Функциональные требования")[1].split("## 4.")[0]
    must, notifications = section.split("Примеры уведомлений:")
    assert must.count("\n- ") == 12
    assert notifications.count("\n- ") == 5
    assert "## 6. Демонстрационный сценарий" in tz
    assert "data/spb.json" in tz
    zones = json.loads((CASE1 / "data" / "spb.json").read_text(encoding="utf-8"))["data"]["zones"]
    assert len(zones) == 6867
    assert {"externalZoneId", "geometry", "tariff"} <= set(zones[0])


def test_run_with_data_copies_directory_into_input(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "geo").mkdir(parents=True)
    (data / "geo" / "zones.json").write_text("[]", encoding="utf-8")
    seen: dict[str, bool] = {}

    def check(ctx: object) -> None:
        root = ctx.input_dir / "input" / INPUT_DATA_DIR  # type: ignore[attr-defined]
        seen["data"] = (root / "geo" / "zones.json").is_file()

    h = Harness(tmp_path)
    registry = Registry([FakeAgent(spec("a"), hook=check)])
    config = h.config()
    graph = build_graph(registry, config)
    start_run(graph, registry, config, run_id="r1", requirements=h.requirements, data=data)
    assert seen["data"]
    assert h.kinds("run_started")[0].data["data"] == "data"

    with pytest.raises(OrchestratorError, match="не каталог"):
        start_run(
            graph,
            registry,
            config,
            run_id="r2",
            requirements=h.requirements,
            data=data / "geo" / "zones.json",
        )
