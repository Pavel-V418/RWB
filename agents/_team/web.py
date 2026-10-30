"""Стыковка веб-части (участник 4) с анализом (участник 2) по путям и форматам артефактов.

Расхождения договорённостей, которые закрывает адаптер UX-этапа:
- платформы: анализ пишет 01_requirements/profile.json с needs_web_ui / needs_admin_ui /
  needs_mobile_app, веб ждёт profile.platforms или functional[].channels → platforms();
- экраны и трассируемость для документации: анализ читает 03_ux/screens.json и
  03_ux/traceability.json, веб пишет screen-spec.<платформа>.json → write_team_inputs();
- документация интерфейса: анализ берёт 03_ux/docs/*.md, веб пишет её отдельным шагом в
  09_docs/ui → ui_docs() в каталог UX-этапа.
"""

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from specforge_web.tools.ui_docs import generate_docs

PLATFORMS = ("web", "android")
SCREENS_FILE = "screens.json"
TRACE_FILE = "traceability.json"
UI_DOCS_DIR = "docs"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def platforms(artifacts: Path) -> list[str]:
    """Платформы экранов из профиля анализа; явный profile.platforms важнее флагов."""
    profile = _load(artifacts / "01_requirements" / "profile.json") or {}
    explicit = [p for p in profile.get("platforms") or [] if p in PLATFORMS]
    if explicit:
        return explicit
    found = []
    if profile.get("needs_web_ui") or profile.get("needs_admin_ui"):
        found.append("web")
    if profile.get("needs_mobile_app"):
        found.append("android")
    return found or ["web"]


def write_team_inputs(ux_dir: Path) -> int:
    """screens.json ({"screens": [{id, title, platform, roles, fr}]}) и traceability.json
    ({"FR-1": [экраны]}) из спек экранов. Возвращает число экранов."""
    screens: list[dict[str, Any]] = []
    trace: dict[str, list[str]] = {}
    for platform in PLATFORMS:
        spec = _load(ux_dir / f"screen-spec.{platform}.json")
        for screen in (spec or {}).get("screens") or []:
            fr = [str(r) for r in screen.get("requirements") or []]
            screens.append(
                {
                    "id": screen.get("id"),
                    "title": screen.get("title"),
                    "platform": platform,
                    "roles": screen.get("roles") or [],
                    "fr": fr,
                }
            )
            for req in fr:
                ids = trace.setdefault(req, [])
                if screen.get("id") not in ids:
                    ids.append(str(screen.get("id")))
    (ux_dir / SCREENS_FILE).write_text(
        json.dumps({"screens": screens}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ux_dir / TRACE_FILE).write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(screens)


def ui_docs(artifacts: Path, ux_dir: Path) -> list[Path]:
    """Руководство пользователя по экранам, каталог экранов и трассируемость FR → экраны —
    в ux_dir/docs. Генератор веб-части читает <корень>/01_requirements и <корень>/03_ux, а
    каталог UX-этапа не обязан лежать в artifacts (agent run --out): собирается временный
    корень."""
    with tempfile.TemporaryDirectory(prefix="specforge-ui-docs-") as tmp:
        view = Path(tmp)
        requirements = artifacts / "01_requirements" / "requirements.json"
        if requirements.is_file():
            (view / "01_requirements").mkdir()
            shutil.copy2(requirements, view / "01_requirements" / "requirements.json")
        (view / "03_ux").mkdir()
        for path in ux_dir.glob("*.json"):
            shutil.copy2(path, view / "03_ux" / path.name)
        target = ux_dir / UI_DOCS_DIR
        written = generate_docs(view, target)
        return [target / p.name for p in written]
