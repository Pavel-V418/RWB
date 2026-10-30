"""API консоли (TestClient, фейковый прогон): запуск, граф, артефакты, отчёт, SSE, approve."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from specforge.api.server import create_app, openapi_yaml
from specforge.core.contracts import RunConfig
from specforge.core.events import EventBus, SqliteEventStore
from specforge.llm import FakeLLM
from specforge.orchestrator.graph import SERDE, GraphConfig
from specforge.orchestrator.registry import Registry
from specforge.orchestrator.service import RunService
from tests.test_orchestrator import FakeAgent, five_agents

CONTRACT = Path(__file__).parent.parent / "docs" / "console-api.yaml"


@pytest.fixture
def service(tmp_path: Path) -> RunService:
    bus = EventBus(SqliteEventStore(tmp_path / "events.sqlite"))
    agents: dict[str, FakeAgent] = five_agents()
    config = GraphConfig(
        run=RunConfig(llm_base_url="http://fake", model="fake", max_concurrency=4),
        llm=FakeLLM([]),
        runs_dir=tmp_path / "runs",
        emit=bus.emit,
        checkpointer=InMemorySaver(serde=SERDE),
        use_cache=False,
    )
    return RunService(Registry(agents.values()), config, bus)


@pytest.fixture
def client(service: RunService) -> TestClient:
    return TestClient(create_app(service))


def start(client: TestClient, service: RunService, **form: str) -> str:
    response = client.post(
        "/runs", files={"file": ("req.md", b"# TZ\n", "text/markdown")}, data=form
    )
    assert response.status_code == 202, response.text
    run_id: str = response.json()["run_id"]
    service.wait(run_id, 30)
    return run_id


def sse(client: TestClient, url: str) -> list[dict[str, Any]]:
    events = []
    with client.stream("GET", url) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:") :].strip()))
    return events


def test_run_lifecycle_graph_artifacts_report(client: TestClient, service: RunService) -> None:
    run_id = start(client, service, flags="has_mobile")

    info = client.get(f"/runs/{run_id}").json()
    assert info["status"] == "finished"
    assert {n: s["status"] for n, s in info["stages"].items()} == dict.fromkeys("abcde", "passed")
    assert [r["run_id"] for r in client.get("/runs").json()] == [run_id]

    graph = client.get(f"/runs/{run_id}/graph").json()
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert nodes["b"]["parallel_group"] == "gen"
    assert nodes["d"]["status"] == "passed"
    assert nodes["finalize"]["status"] == "passed"
    edges = {(e["source"], e["target"]) for e in graph["edges"]}
    assert {("profile", "a"), ("a", "b"), ("b", "d"), ("c", "d"), ("d", "finalize")} <= edges
    assert graph["steps"][2] == ["b", "c"]

    listing = client.get(f"/runs/{run_id}/artifacts").json()
    assert {"input", "a", "d"} <= {e["path"] for e in listing["entries"]}
    assert client.get(f"/runs/{run_id}/artifacts", params={"path": "d/d.txt"}).text == "d"
    text = client.get(f"/runs/{run_id}/artifacts", params={"path": "input/requirements.md"}).text
    assert text == "# TZ\n"

    report = client.get(f"/runs/{run_id}/report").text
    assert f"# Запуск {run_id}" in report
    assert "| d | ✅ passed |" in report


def test_events_sse_replay_and_after(client: TestClient, service: RunService) -> None:
    run_id = start(client, service)
    events = sse(client, f"/runs/{run_id}/events")
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "run_started"
    assert kinds[-1] == "run_finished"
    assert {"stage_started", "stage_passed", "stage_skipped"} <= set(kinds)
    skipped = next(e for e in events if e["kind"] == "stage_skipped")
    assert (skipped["stage"], skipped["agent"]) == ("e", "e")
    assert [e["id"] for e in events] == sorted(e["id"] for e in events)

    tail = sse(client, f"/runs/{run_id}/events?after={events[-3]['id']}")
    assert [e["id"] for e in tail] == [e["id"] for e in events[-2:]]


def test_approve_after_stage_with_artifact_edits(client: TestClient, service: RunService) -> None:
    run_id = start(client, service, approve_after="a")

    info = client.get(f"/runs/{run_id}").json()
    assert info["status"] == "waiting_approval"
    assert info["pending_approval"]["after"] == "a"
    assert info["pending_approval"]["artifacts"] == ["a/a.txt"]
    assert set(info["stages"]) == {"a"}
    nodes = {n["id"]: n for n in client.get(f"/runs/{run_id}/graph").json()["nodes"]}
    assert nodes["a"]["status"] == "waiting_approval"
    waiting = sse(client, f"/runs/{run_id}/events?follow=false")
    assert waiting[-1]["kind"] == "approval_required"

    bad = client.post(f"/runs/{run_id}/approve", json={"edits": {"../../x.txt": "hack"}})
    assert bad.status_code == 400
    response = client.post(f"/runs/{run_id}/approve", json={"edits": {"a/a.txt": "правка"}})
    assert response.status_code == 202, response.text
    service.wait(run_id, 30)

    info = client.get(f"/runs/{run_id}").json()
    assert info["status"] == "finished"
    assert info["stages"]["d"]["status"] == "passed"
    assert client.get(f"/runs/{run_id}/artifacts", params={"path": "a/a.txt"}).text == "правка"
    manifest = client.get(f"/runs/{run_id}/artifacts", params={"path": "a/manifest.json"}).json()
    assert "approval" in manifest["agents"]
    kinds = [e["kind"] for e in sse(client, f"/runs/{run_id}/events")]
    assert "artifacts_edited" in kinds
    assert kinds[-1] == "run_finished"
    assert client.post(f"/runs/{run_id}/approve").status_code == 409


def test_only_runs_selected_stages_and_dependencies(
    client: TestClient, service: RunService
) -> None:
    run_id = start(client, service, only="b")
    stages = client.get(f"/runs/{run_id}").json()["stages"]
    assert stages["a"]["status"] == "passed"
    assert stages["b"]["status"] == "passed"
    assert stages["c"]["stopped_reason"] == "only"
    assert stages["d"]["status"] == "skipped"


def test_api_errors(client: TestClient, service: RunService) -> None:
    upload = {"file": ("req.exe", b"MZ", "application/octet-stream")}
    assert client.post("/runs", files=upload).status_code == 415
    assert client.get("/runs/nope").status_code == 404
    run_id = start(client, service)
    url = f"/runs/{run_id}/artifacts"
    assert client.get(url, params={"path": "../../.."}).status_code == 400
    assert client.get(url, params={"path": "zzz"}).status_code == 404
    bad = client.post(
        "/runs", files={"file": ("req.md", b"x", "text/markdown")}, data={"only": "ghost"}
    )
    assert bad.status_code == 202  # ошибка выбора видна в статусе запуска
    service.wait(bad.json()["run_id"], 10)
    assert client.get(f"/runs/{bad.json()['run_id']}").json()["status"] == "failed"
    cors = client.options(
        "/runs",
        headers={"Origin": "http://localhost:4200", "Access-Control-Request-Method": "GET"},
    )
    assert cors.headers["access-control-allow-origin"] == "http://localhost:4200"


def test_openapi_contract_is_exported() -> None:
    assert CONTRACT.read_text("utf-8") == openapi_yaml(), (
        "docs/console-api.yaml устарел: uv run python -m specforge.api.server openapi "
        "docs/console-api.yaml"
    )
