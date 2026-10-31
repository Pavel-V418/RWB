"""
Категория находки → SEC-требование кейса.

Номера SEC меняются от прогона к прогону, поэтому сопоставляем по категории
(requirements.json → security[].category), а номер берём оттуда же (договорённость с участником 2).
Ничего не знает о домене кейса.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .findings import Finding

CWE_CATEGORY = {
    "secrets": {"798", "259", "321", "522", "540", "615"},
    "input_validation": {"20", "22", "23", "77", "78", "79", "80", "89", "90", "94", "95", "502", "611", "643",
                         "776", "917", "918", "1236", "1333", "116", "117", "643", "943"},
    "authz": {"284", "285", "639", "862", "863", "732", "425", "566", "915", "352", "601"},
    "authn": {"287", "306", "307", "384", "521", "613", "620", "640", "1390", "347", "345", "295", "297"},
    "privacy": {"200", "201", "209", "312", "319", "359", "532", "538", "548", "614", "1004", "311", "315", "523"},
    "rate_limiting": {"770", "400", "799", "307"},
    "audit": {"778", "223", "779"},
    "crypto": {"326", "327", "328", "330", "338", "916", "759", "760"},
    "config": {"16", "693", "1021", "942", "1275", "693", "524", "525", "10020", "10038"},
}
KEYWORDS = [
    ("secrets", r"secret|password|api[-_ ]?key|token|credential|private key|пароль|ключ"),
    ("input_validation", r"injection|xss|cross[- ]site scripting|traversal|deserializ|ssrf|xxe|sqli|валидац"),
    ("authz", r"authoriz|access control|idor|privilege|csrf|open redirect|доступ"),
    ("authn", r"authenticat|session fixation|jwt|cookie without|аутентиф"),
    ("privacy", r"log(?:ging)? of|sensitive|personal|pii|cleartext|plaintext|disclos|tls|https|логир"),
    ("rate_limiting", r"rate limit|dos|denial of service|throttl"),
    ("crypto", r"weak (?:hash|cipher|crypto)|md5|sha1|insecure random"),
    ("config", r"header|csp|x-frame|hsts|cors|misconfig|debuggable|cleartexttraffic|backup"),
]
DEPENDENCY_TOOLS = {"trivy", "trivy-image", "grype", "dependency-check", "osv-scanner"}


def category_of(f: Finding) -> str | None:
    if f.tool == "gitleaks":
        return "secrets"
    if f.cve or (f.tool in DEPENDENCY_TOOLS and re.match(r"(CVE|GHSA)-", f.rule)):
        return "dependencies"
    for cwe in f.cwe:
        num = cwe.split("-")[-1]
        for cat, nums in CWE_CATEGORY.items():
            if num in nums:
                return cat
    text = f"{f.rule} {f.title}".lower()
    for cat, pattern in KEYWORDS:
        if re.search(pattern, text):
            return cat
    return None


# если в требованиях нет категории находки — ближайшая по смыслу
FALLBACK = {"crypto": "privacy", "config": "privacy", "dependencies": "supply_chain"}


def load_security_requirements(path: Path | None) -> list[dict]:
    if not path or not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("security", []) if isinstance(data, dict) else []


def classify(findings: list[Finding], sec_reqs: list[dict]) -> None:
    by_cat: dict[str, list[str]] = {}
    for r in sec_reqs:
        by_cat.setdefault(str(r.get("category", "")).lower(), []).append(r["id"])
    for f in findings:
        f.category = f.category or category_of(f)
        if f.req:  # сканер уже поставил тег (properties.req / tags) — уважаем
            continue
        cat = f.category
        ids = by_cat.get(cat or "") or by_cat.get(FALLBACK.get(cat or "", ""), [])
        f.req = list(ids)


# ---------------------------------------------------------------- политика блокировки
# Договорённость с участником 3: блокируют gitleaks, Trivy-образ HIGH+, и после триажа — подтверждённые
# high+ из любых сканеров. Semgrep, ZAP и Trivy fs до триажа только информируют.
BLOCKING_SEVERITIES = {"critical", "high"}


def apply_policy(findings: list[Finding], image_tools: set[str] | None = None) -> None:
    image_tools = image_tools or {"trivy-image"}
    for f in findings:
        if f.status != "open":
            f.blocking = False
        elif f.tool == "gitleaks":
            f.blocking = True
        elif f.tool in image_tools:
            f.blocking = f.severity in BLOCKING_SEVERITIES
        else:  # информирующие: блокируют, только если триаж подтвердил high+
            f.blocking = f.severity in BLOCKING_SEVERITIES and f.verdict_reason.startswith("подтверждено")
