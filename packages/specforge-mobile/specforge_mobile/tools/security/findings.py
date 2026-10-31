"""
Единая модель находки и разбор отчётов сканеров (SARIF: Semgrep, gitleaks, Trivy; JSON: OWASP ZAP).
Только стандартная библиотека.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

SEVERITIES = ["critical", "high", "medium", "low", "info"]


@dataclass
class Finding:
    tool: str
    rule: str
    title: str
    severity: str                      # critical|high|medium|low|info
    location: str = ""                 # path[:line]
    cwe: list[str] = field(default_factory=list)
    cve: str | None = None
    category: str | None = None        # категория SEC-требования (authn, authz, secrets, ...)
    req: list[str] = field(default_factory=list)
    status: str = "open"               # open|false_positive|fixed|accepted
    verdict_reason: str = ""
    fix: str = ""
    blocking: bool = False
    id: str = ""

    def __post_init__(self):
        if not self.id:  # отпечаток без номера строки: находка переживает сдвиг кода
            path = self.location.split(":")[0]
            raw = f"{self.tool}|{self.rule}|{path}|{self.title[:80]}"
            self.id = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    @property
    def path(self) -> str:
        return self.location.split(":")[0]

    @property
    def line(self) -> int:
        parts = self.location.split(":")
        return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

    def to_dict(self) -> dict:
        return asdict(self)


def norm_severity(value) -> str:
    """security-severity (CVSS 0..10), уровни SARIF и слова сканеров → одна шкала."""
    if value is None:
        return "medium"
    try:
        v = float(value)
        return "critical" if v >= 9 else "high" if v >= 7 else "medium" if v >= 4 else "low" if v > 0 else "info"
    except (TypeError, ValueError):
        pass
    v = str(value).lower()
    return {"error": "high", "warning": "medium", "note": "low", "none": "info", "moderate": "medium",
            "informational": "info", "unknown": "medium"}.get(v, v if v in SEVERITIES else "medium")


def _cwes(*parts) -> list[str]:
    """CWE из тегов и свойств правила в любом виде: "CWE-89", "cwe_89", "CWE 89: SQL Injection"."""
    found = []
    for m in re.findall(r"CWE[-_ ]?(\d+)", json.dumps(parts, ensure_ascii=False), re.I):
        if f"CWE-{m}" not in found:
            found.append(f"CWE-{m}")
    return found


def parse_sarif(path: Path, root: Path | None = None) -> list[Finding]:
    """Имя сканера из SARIF; trivy-image*.sarif → "trivy-image" (образ блокирует, fs — нет)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for run in data.get("runs", []):
        driver = run.get("tool", {}).get("driver", {})
        tool = (driver.get("name") or path.stem).split()[0].lower()
        tool = "semgrep" if tool.startswith("semgrep") else tool
        if tool == "trivy" and path.stem.startswith("trivy-image"):
            tool = "trivy-image"
        rules = {r.get("id"): r for r in driver.get("rules", [])}
        for r in run.get("results", []):
            rule_id = r.get("ruleId", "")
            rule = rules.get(rule_id, {})
            props = {**rule.get("properties", {}), **r.get("properties", {})}
            sev = props.get("security-severity")
            if sev is None:
                sev = r.get("level") or rule.get("defaultConfiguration", {}).get("level")
            if tool == "gitleaks":
                sev = "high"  # секрет в репозитории всегда серьёзен
            loc = ""
            for l in r.get("locations", [])[:1]:
                pl = l.get("physicalLocation", {})
                uri = pl.get("artifactLocation", {}).get("uri", "")
                uri = re.sub(r"^file://", "", uri).replace("\\", "/")  # Semgrep на Windows пишет «\»
                if root and Path(uri).is_absolute():
                    try:
                        uri = Path(uri).resolve().relative_to(root.resolve()).as_posix()
                    except ValueError:
                        pass
                loc = uri + (f":{pl['region']['startLine']}" if pl.get("region", {}).get("startLine") else "")
            text = r.get("message", {}).get("text", "") or rule.get("shortDescription", {}).get("text", "")
            cve = next(iter(re.findall(r"CVE-\d{4}-\d+", f"{rule_id} {text}")), None)
            status = "fixed" if r.get("baselineState") == "absent" else (
                "false_positive" if r.get("suppressions") else "open")
            req = props.get("req") or [t for t in props.get("tags", []) if re.match(r"SEC-\d+", str(t))]
            out.append(Finding(tool=tool, rule=rule_id, title=text.strip().splitlines()[0][:300] if text else rule_id,
                               severity=norm_severity(sev), location=loc,
                               cwe=_cwes(props.get("tags", []), props.get("cwe", ""), rule.get("properties", {})),
                               cve=cve, status=status, req=[req] if isinstance(req, str) else list(req)))
    return out


ZAP_RISK = {"3": "high", "2": "medium", "1": "low", "0": "info"}


def parse_zap(path: Path) -> list[Finding]:
    """Отчёт OWASP ZAP (zap-baseline/zap-api-scan, ключ -J)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for site in data.get("site", []):
        for a in site.get("alerts", []):
            inst = (a.get("instances") or [{}])[0]
            uri = inst.get("uri", site.get("@name", ""))
            out.append(Finding(tool="zap", rule=str(a.get("pluginid", "")),
                               title=a.get("alert") or a.get("name", ""),
                               severity=ZAP_RISK.get(str(a.get("riskcode")), "medium"),
                               location=f"{inst.get('method', 'GET')} {uri}",
                               cwe=[f"CWE-{a['cweid']}"] if str(a.get("cweid", "")).isdigit()
                               and str(a.get("cweid")) not in ("0", "-1") else []))
    return out


def load_reports(raw_dir: Path, root: Path | None = None) -> tuple[list[Finding], list[str]]:
    """Все отчёты из папки: *.sarif и zap*.json. Возвращает (находки, прочитанные файлы)."""
    findings, sources = [], []
    for p in sorted(raw_dir.glob("*.sarif")):
        findings += parse_sarif(p, root)
        sources.append(p.name)
    for p in sorted(raw_dir.glob("zap*.json")):
        findings += parse_zap(p)
        sources.append(p.name)
    uniq = {}
    for f in findings:  # один и тот же сканер иногда дублирует результат
        uniq.setdefault(f.id, f)
    return list(uniq.values()), sources
