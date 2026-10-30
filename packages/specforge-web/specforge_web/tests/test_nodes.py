import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.agents import nodes  # noqa: E402


class FakeGraph:
    def __init__(self):
        self.nodes, self.edges, self.cond = {}, [], []

    def add_node(self, name, fn):
        self.nodes[name] = fn

    def add_edge(self, a, b):
        self.edges.append((a, b))

    def add_conditional_edges(self, a, fn, mapping):
        self.cond.append((a, fn, mapping))


def test_nodes_offline_chain(tmp_path):
    for d in ("01_requirements", "02_system"):
        shutil.copytree(ROOT / "fixtures" / "synthetic-inventory" / d, tmp_path / d)
    state = {"artifacts": str(tmp_path), "llm": None, "build": False, "stages": {}}
    for node in (nodes.ux_node, nodes.web_node, nodes.ui_review_node, nodes.ui_docs_node):
        upd = node(state)
        state["stages"] = nodes.merge_stages(state["stages"], upd["stages"])
    st = state["stages"]
    assert st["03_ux"]["passed"] and st["05_web"]["passed"] and st["09_docs_ui"]["passed"]
    assert any(e["code"] == "UI_SKIPPED" for e in st["07_ui"]["errors"])  # без сборки ревью пропускается
    assert (tmp_path / "09_docs" / "ui" / "user-guide.web.md").exists()
    assert nodes.needs_web_retry(state) == "09_docs_ui"


def test_node_never_raises(tmp_path):
    upd = nodes.ux_node({"artifacts": str(tmp_path / "nope"), "llm": None})
    assert upd["stages"]["03_ux"]["passed"] is False and upd["stages"]["03_ux"]["errors"][0]["code"] == "NODE_CRASH"


def test_register():
    g = FakeGraph()
    nodes.register(g)
    assert set(g.nodes) == {"03_ux", "05_web", "07_ui", "09_docs_ui"}
    assert ("02_system", "03_ux") in g.edges and g.cond[0][0] == "07_ui"


def test_retry_only_once():
    st = {"stages": {"07_ui": {"metrics": {"autofix": {"specPatches": ["x"]}}}, "05_web": {"metrics": {"retried": False}}}}
    assert nodes.needs_web_retry(st) == "05_web"
    st["stages"]["05_web"]["metrics"]["retried"] = True
    assert nodes.needs_web_retry(st) == "09_docs_ui"
