"""Пользовательские потоки (03_ux/user-flows.<platform>.md) — из той же спеки, что и интерфейс."""
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.agents.ux_designer import agent as ux  # noqa: E402
from specforge_web.agents.ux_designer.baseline import baseline_spec  # noqa: E402
from specforge_web.agents.ux_designer.flows import user_flows  # noqa: E402

FX = ROOT / "fixtures" / "synthetic-taxi"


def _taxi():
    req = json.loads((FX / "01_requirements" / "requirements.json").read_text(encoding="utf-8"))
    api = yaml.safe_load((FX / "02_system" / "openapi.yaml").read_text(encoding="utf-8"))
    return baseline_spec(api, req), req


def test_flows_per_role_with_main_and_alternative_scenarios():
    spec, req = _taxi()
    md = user_flows(spec, req)
    disp = md.split("## Диспетчер")[1].split("\n## ")[0]
    admin = md.split("## Администратор")[1].split("\n## ")[0]
    # создавать заказы может только диспетчер (x-roles), администратору форма недоступна
    assert "**Новый заказ** (FR-01)" in disp and "**Новый заказ**" not in admin
    assert "«Новый заказ» недоступен роли" in admin
    assert "подтверждение" in disp and "отказ в подтверждении" in disp
    assert "неверный формат поля «Телефон пассажира»" in disp and "сервер не сохранил" in disp


def test_mermaid_edges_have_labels_and_known_nodes():
    spec, req = _taxi()
    md = user_flows(spec, req)
    for block in re.findall(r"```mermaid\n(.*?)```", md, re.S):
        nodes = set(re.findall(r"^\s+(\w+)\[", block, re.M))
        for a, label, b in re.findall(r"^\s+(\w+) -->\|([^|]*)\| (\w+)$", block, re.M):
            assert label.strip() and a in nodes and b in nodes


def test_ux_agent_writes_flows(tmp_path):
    for d in ("01_requirements", "02_system"):
        (tmp_path / d).mkdir()
        for f in (FX / d).iterdir():
            (tmp_path / d / f.name).write_bytes(f.read_bytes())
    ux.run(tmp_path, tmp_path / "03_ux", None, platforms=["web"], log=lambda *_: None)
    assert (tmp_path / "03_ux" / "user-flows.web.md").read_text(encoding="utf-8").startswith("# Пользовательские потоки")
