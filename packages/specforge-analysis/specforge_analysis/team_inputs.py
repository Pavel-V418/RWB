"""Чтение результатов других участников для документации.

Пути договорены с командой и собраны в PATHS — если договорённость поменяется, правится только он.
Любой файл может отсутствовать: тогда соответствующий раздел документации помечается «ожидается».
Форматы читаются терпимо: свой JSON или стандартные JUnit XML / SARIF.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PATHS = {
    "screens": "03_ux/screens.json",
    "ui_trace": "03_ux/traceability.json",  # {"FR-1": ["screen-id", ...]} или [{"fr": "FR-1", "screens": [...]}]
    "ui_docs": "03_ux/docs",                # *.md и картинки участника 4 -> раздел «Интерфейс» в MkDocs
    "web_screenshots": "05_web/screenshots",
    "android_screenshots": "06_android/screenshots",
    "tests_dir": "07_tests",           # report.json и/или *.xml (JUnit)
    "security_dir": "07_tests",        # security.json и/или *.sarif
    "infra_dir": "08_infra",           # README-run.md, docker-compose.yml, .env.example
    "team_services": "08_infra/services",  # <slug>.md — паспорт сервиса от участника 3 заменяет сгенерированный
    "team_adr": "08_infra/adr",            # *.md — ADR участника 3 добавляются в журнал решений
}

TAG_RE = re.compile(r"\b(AC-FR-\d+-\d{2}|FR-\d+|NFR-\d+|SEC-\d+)\b")
SEVERITIES = ["critical", "high", "medium", "low", "info"]


@dataclass
class Screen:
    id: str
    title: str
    platform: str = "web"
    roles: list[str] = field(default_factory=list)
    fr: list[str] = field(default_factory=list)
    description: str = ""
    screenshot: str | None = None


@dataclass
class TestCase:
    name: str
    status: str            # passed | failed | skipped
    suite: str = ""
    tags: list[str] = field(default_factory=list)
    message: str = ""
    duration_s: float = 0.0


@dataclass
class Finding:
    tool: str
    rule: str
    title: str
    severity: str
    status: str = "open"   # open | fixed | false_positive | accepted
    location: str = ""
    cve: str | None = None
    reqs: list[str] = field(default_factory=list)   # SEC-n / NFR-n / FR-n, к которым относится находка


@dataclass
class Infra:
    run_readme: str | None = None
    services: list[dict] = field(default_factory=list)   # name, ports, healthcheck
    env_example: str | None = None
    ci_stages: list[str] = field(default_factory=list)   # .gitlab-ci.yml: stages
    ci_jobs: list[dict] = field(default_factory=list)    # name, stage


@dataclass
class TeamInputs:
    screens: list[Screen] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    tests: list[TestCase] = field(default_factory=list)
    test_sources: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    security_sources: list[str] = field(default_factory=list)
    infra: Infra = field(default_factory=Infra)
    metrics: dict[str, list[dict]] = field(default_factory=dict)   # этап -> записи metrics.jsonl
    problems: list[str] = field(default_factory=list)              # нечитаемые файлы


def _as_list(v) -> list[str]:
    return [v] if isinstance(v, str) else [str(x) for x in (v or [])]


def _tags(*texts: str) -> list[str]:
    out: list[str] = []
    for t in texts:
        for m in TAG_RE.findall(t or ""):
            if m not in out:
                out.append(m)
    return out


def _norm_status(s: str) -> str:
    s = (s or "").lower()
    if s in ("passed", "pass", "ok", "success"):
        return "passed"
    if s in ("skipped", "skip", "ignored", "pending"):
        return "skipped"
    return "failed"


def _norm_sev(s: str) -> str:
    s = (s or "").lower()
    return {"error": "high", "warning": "medium", "note": "low", "none": "info", "moderate": "medium",
            "unknown": "info"}.get(s, s if s in SEVERITIES else "medium")


def read_screens(root: Path, ti: TeamInputs) -> None:
    p = root / PATHS["screens"]
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for s in data.get("screens", data if isinstance(data, list) else []):
                ti.screens.append(Screen(id=str(s.get("id")), title=s.get("title", s.get("id", "")),
                                         platform=s.get("platform", "web"), roles=s.get("roles", []),
                                         fr=s.get("fr", []), description=s.get("description", ""),
                                         screenshot=s.get("screenshot")))
        except (json.JSONDecodeError, AttributeError) as exc:
            ti.problems.append(f"{p}: {exc}")
    tp = root / PATHS["ui_trace"]
    if tp.exists():
        try:
            data = json.loads(tp.read_text(encoding="utf-8"))
            pairs = data.items() if isinstance(data, dict) else [(d.get("fr"), d.get("screens", [])) for d in data]
            by_key = {s.id: s for s in ti.screens} | {s.title: s for s in ti.screens}
            for fr, screens in pairs:
                for sid in screens or []:
                    sc = by_key.get(sid)
                    if sc is None:
                        sc = Screen(id=str(sid), title=str(sid))
                        ti.screens.append(sc)
                        by_key[sc.id] = sc
                    if fr and fr not in sc.fr:
                        sc.fr.append(fr)
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            ti.problems.append(f"{tp}: {exc}")
    for key in ("web_screenshots", "android_screenshots"):
        d = root / PATHS[key]
        if d.is_dir():
            ti.screenshots += sorted(str(x.relative_to(root)) for x in d.glob("*.png"))


def _read_junit(p: Path, ti: TeamInputs) -> None:
    tree = ET.parse(p)
    for tc in tree.iter("testcase"):
        name = tc.get("name", "")
        cls = tc.get("classname", "")
        status = "passed"
        msg = ""
        for child in tc:
            if child.tag in ("failure", "error"):
                status, msg = "failed", (child.get("message") or child.text or "")[:300]
            elif child.tag == "skipped":
                status = "skipped"
        props = " ".join((pr.get("value") or "") for pr in tc.iter("property"))
        ti.tests.append(TestCase(name=name, status=status, suite=cls, tags=_tags(name, cls, props),
                                 message=msg, duration_s=float(tc.get("time") or 0)))


def read_tests(root: Path, ti: TeamInputs) -> None:
    d = root / PATHS["tests_dir"]
    if not d.is_dir():
        return
    rep = d / "report.json"
    if rep.exists():
        try:
            data = json.loads(rep.read_text(encoding="utf-8"))
            for t in data.get("tests", []):
                ti.tests.append(TestCase(name=t.get("name", ""), status=_norm_status(t.get("status", "")),
                                         suite=t.get("suite", ""),
                                         tags=t.get("tags") or _tags(t.get("name", "")),
                                         message=t.get("message", ""), duration_s=float(t.get("duration_s", 0))))
            ti.test_sources.append(str(rep.relative_to(root)))
        except (json.JSONDecodeError, AttributeError, ValueError) as exc:
            ti.problems.append(f"{rep}: {exc}")
    for x in sorted(d.glob("*.xml")):
        try:
            _read_junit(x, ti)
            ti.test_sources.append(str(x.relative_to(root)))
        except ET.ParseError as exc:
            ti.problems.append(f"{x}: {exc}")


def _read_sarif(p: Path, ti: TeamInputs) -> None:
    data = json.loads(p.read_text(encoding="utf-8"))
    for run in data.get("runs", []):
        tool = run.get("tool", {}).get("driver", {}).get("name", p.stem)
        rules = {r.get("id"): r for r in run.get("tool", {}).get("driver", {}).get("rules", [])}
        for r in run.get("results", []):
            rule = r.get("ruleId", "")
            props = (rules.get(rule) or {}).get("properties", {})
            sev = props.get("security-severity")
            if sev is not None:
                try:
                    v = float(sev)
                    sev = "critical" if v >= 9 else "high" if v >= 7 else "medium" if v >= 4 else "low"
                except ValueError:
                    sev = None
            loc = ""
            for l in r.get("locations", [])[:1]:
                pl = l.get("physicalLocation", {})
                loc = pl.get("artifactLocation", {}).get("uri", "")
                if pl.get("region", {}).get("startLine"):
                    loc += f":{pl['region']['startLine']}"
            status = "fixed" if r.get("baselineState") == "absent" else (
                "false_positive" if r.get("suppressions") else "open")
            cve = next(iter(re.findall(r"CVE-\d{4}-\d+", rule + " " + r.get("message", {}).get("text", ""))), None)
            ti.findings.append(Finding(tool=tool, rule=rule, title=r.get("message", {}).get("text", "")[:200],
                                       severity=sev or _norm_sev(r.get("level", "warning")), status=status,
                                       location=loc, cve=cve,
                                       reqs=_as_list((r.get("properties") or {}).get("req"))
                                       or _tags(" ".join((r.get("properties") or {}).get("tags", [])),
                                                r.get("message", {}).get("text", ""))))


def read_security(root: Path, ti: TeamInputs) -> None:
    d = root / PATHS["security_dir"]
    if not d.is_dir():
        return
    sec = d / "security.json"
    if sec.exists():
        try:
            data = json.loads(sec.read_text(encoding="utf-8"))
            for f in data.get("findings", []):
                ti.findings.append(Finding(tool=f.get("tool", ""), rule=f.get("id", f.get("rule", "")),
                                           title=f.get("title", ""), severity=_norm_sev(f.get("severity", "")),
                                           status=f.get("status", "open"), location=f.get("location", ""),
                                           cve=f.get("cve"), reqs=_as_list(f.get("req") or f.get("reqs"))
                                           or _tags(f.get("title", ""), f.get("id", ""))))
            ti.security_sources.append(str(sec.relative_to(root)))
        except (json.JSONDecodeError, AttributeError) as exc:
            ti.problems.append(f"{sec}: {exc}")
    for x in sorted(d.glob("*.sarif")):
        try:
            _read_sarif(x, ti)
            ti.security_sources.append(str(x.relative_to(root)))
        except (json.JSONDecodeError, AttributeError) as exc:
            ti.problems.append(f"{x}: {exc}")


def read_infra(root: Path, ti: TeamInputs, repo_root: Path | None) -> None:
    candidates = [root / PATHS["infra_dir"]] + ([repo_root] if repo_root else [])
    for d in candidates:
        if not d or not d.is_dir():
            continue
        rd = d / "README-run.md"
        if rd.exists() and not ti.infra.run_readme:
            ti.infra.run_readme = rd.read_text(encoding="utf-8")
        env = d / ".env.example"
        if env.exists() and not ti.infra.env_example:
            ti.infra.env_example = env.read_text(encoding="utf-8")
        ci = d / ".gitlab-ci.yml"
        if ci.exists() and not ti.infra.ci_stages:
            try:
                data = yaml.safe_load(ci.read_text(encoding="utf-8")) or {}
                ti.infra.ci_stages = [str(s) for s in data.get("stages", [])]
                ti.infra.ci_jobs = [{"name": k, "stage": (v or {}).get("stage", "test")} for k, v in data.items()
                                    if isinstance(v, dict) and not k.startswith(".") and ("script" in v or "stage" in v)]
            except yaml.YAMLError as exc:
                ti.problems.append(f"{ci}: {exc}")
        for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            dc = d / name
            if dc.exists() and not ti.infra.services:
                try:
                    data = yaml.safe_load(dc.read_text(encoding="utf-8")) or {}
                    for sname, s in (data.get("services") or {}).items():
                        s = s or {}
                        ti.infra.services.append({
                            "name": sname, "image": s.get("image") or ("build" if s.get("build") else ""),
                            "ports": [str(p) for p in (s.get("ports") or [])],
                            "healthcheck": bool(s.get("healthcheck")),
                            "depends_on": list(s.get("depends_on") or []),
                        })
                except yaml.YAMLError as exc:
                    ti.problems.append(f"{dc}: {exc}")


def read_metrics(root: Path, ti: TeamInputs) -> None:
    for p in sorted(root.glob("*/metrics.jsonl")):
        rows = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if rows:
            ti.metrics[p.parent.name] = rows


def read_all(artifacts_root: str | Path, repo_root: str | Path | None = None) -> TeamInputs:
    root = Path(artifacts_root)
    ti = TeamInputs()
    read_screens(root, ti)
    read_tests(root, ti)
    read_security(root, ti)
    read_infra(root, ti, Path(repo_root) if repo_root else None)
    read_metrics(root, ti)
    return ti
