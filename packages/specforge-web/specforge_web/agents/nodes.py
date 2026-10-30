"""Узлы веб-ветки для графа оркестратора (участник 1). Формат LangGraph: node(state) -> partial state.

  from specforge_web.agents.nodes import ux_node, web_node, ui_review_node, ui_docs_node, register
  register(graph)   # если graph — langgraph.graph.StateGraph: добавит узлы и рёбра 02_system → 03_ux → 05_web → 07_ui → 09_docs_ui

Ожидаемое состояние (dict):
  artifacts: str            корень артефактов прогона (artifacts/01_requirements, 02_system, ...)
  llm: LLM | None           клиент с методом complete() (contracts.agent.agent_contract.LLM); None — без LLM
  build: bool               запускать ng build/jest (по умолчанию True)
  ui_base_url: str | None   адрес поднятой админки для UI-ревью; None — сервер с mock-API поднимается сам
  stages: dict              результаты этапов {stage_id: GateResult-as-dict} — узлы дополняют
Каждый узел возвращает {"stages": {<id>: {...}}} и не бросает исключений: сбой → passed=False с ошибкой.
"""
from __future__ import annotations

import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

from specforge_web.contracts.agent.agent_contract import GateError, GateResult


def _wrap(stage: str, fn) -> dict:
    try:
        res: GateResult = fn()
    except Exception as e:  # noqa: BLE001 — узел графа не должен ронять оркестратор
        res = GateResult(passed=False, errors=[GateError(message=f"{type(e).__name__}: {e}", code="NODE_CRASH")],
                         metrics={"traceback": traceback.format_exc()[-2000:]})
    return {"stages": {stage: asdict(res)}}


def ux_node(state: dict[str, Any]) -> dict:
    from specforge_web.agents.ux_designer import agent as ux
    art = Path(state["artifacts"])
    return _wrap("03_ux", lambda: ux.run(art, art / "03_ux", state.get("llm"), platforms=state.get("platforms")))


def web_node(state: dict[str, Any]) -> dict:
    from specforge_web.agents.frontend import agent as fe
    art = Path(state["artifacts"])
    feedback = state.get("ui_feedback") or {}
    retried = "05_web" in (state.get("stages") or {})  # второй заход после UI-ревью

    def go() -> GateResult:
        res = fe.run(art, art / "05_web", state.get("llm"), build=state.get("build", True), feedback=feedback)
        res.metrics["retried"] = retried
        return res
    return _wrap("05_web", go)


def ui_review_node(state: dict[str, Any]) -> dict:
    from specforge_web.agents.vlm_reviewer import autofix, review
    art = Path(state["artifacts"])

    def go() -> GateResult:
        base = state.get("ui_base_url")
        httpd = None
        if not base:
            dist = art / "05_web" / "admin" / "dist" / "app" / "browser"
            if not dist.exists():
                return GateResult(passed=True, errors=[GateError(message="нет собранной админки — UI-ревью пропущено", severity="warning", code="UI_SKIPPED")])
            from specforge_web.tools.spa_server import serve
            httpd = serve(dist, port=0, background=True, mock_openapi=art / "02_system" / "openapi.yaml")
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            res = review.run(art, art / "07_tests" / "ui", state.get("llm"), base_url=base)
        finally:
            if httpd:
                httpd.shutdown()
        if not res.passed:
            res.metrics["autofix"] = autofix.apply(art, art / "07_tests" / "ui")
        return res
    return _wrap("07_ui", go)


def ui_docs_node(state: dict[str, Any]) -> dict:
    from specforge_web.tools.ui_docs import generate_docs
    art = Path(state["artifacts"])

    def go() -> GateResult:
        files = generate_docs(art, art / "09_docs" / "ui", art / "05_web" / "admin", art / "07_tests" / "ui" / "screenshots")
        return GateResult(passed=True, artifacts=[str(f) for f in files])
    return _wrap("09_docs_ui", go)


def needs_web_retry(state: dict[str, Any]) -> str:
    """Условное ребро после UI-ревью: один повтор генерации, если autofix что-то поправил."""
    ui = (state.get("stages") or {}).get("07_ui") or {}
    patched = (ui.get("metrics") or {}).get("autofix", {}).get("specPatches")
    retried = (state.get("stages") or {}).get("05_web", {}).get("metrics", {}).get("retried")
    return "05_web" if patched and not retried else "09_docs_ui"


def merge_stages(a: dict | None, b: dict | None) -> dict:
    """Редьюсер для поля stages в схеме состояния LangGraph."""
    return {**(a or {}), **(b or {})}


def register(graph, after: str = "02_system") -> None:
    """Добавить веб-ветку в StateGraph. Узел `after` должен существовать (системный анализ)."""
    graph.add_node("03_ux", ux_node)
    graph.add_node("05_web", web_node)
    graph.add_node("07_ui", ui_review_node)
    graph.add_node("09_docs_ui", ui_docs_node)
    graph.add_edge(after, "03_ux")
    graph.add_edge("03_ux", "05_web")
    graph.add_edge("05_web", "07_ui")
    graph.add_conditional_edges("07_ui", needs_web_retry, {"05_web": "05_web", "09_docs_ui": "09_docs_ui"})
