"""Frontend-агент (шаг 6): screen-spec + OpenAPI → собранная Angular-админка.

  run(input_dir, output_dir, llm, build=False) -> GateResult
  python -m agents.frontend.agent --in artifacts --out artifacts/05_web [--build] [--offline]

1. Генератор (0 токенов) создаёт весь проект; нестандартные места — слоты (slots.json).
2. Каждый слот заполняет LLM (thinking off, guided JSON): шаблон + тело класса + список модулей
   из kit-manifest. Импорты и обвязку собирает код — модель не может импортировать несуществующее.
3. Пре-шлюзы слота за секунды: kit-lint, template-check, ts-check → ошибки обратно в LLM (≤3 попыток).
   Слот, который не починился, остаётся пустой заглушкой — сборка не ломается, в отчёте предупреждение.
4. Шлюз проекта: kit-lint + template-check + ts-check; с --build ещё npm install, ng build, jest.
5. Если ng build/jest упали из-за слотов LLM — слоты откатываются к заглушкам (сначала те, на которые указывают
   ошибки, затем все) и проект пересобирается: сборка остаётся зелёной, в отчёте — предупреждение FE_SLOT_ROLLBACK.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

from specforge_web.agents.common.llm import TrackedLLM, Usage, llm_from_env  # noqa: E402
from specforge_web.contracts.agent.agent_contract import GateError, GateResult  # noqa: E402
from specforge_web.generators.angular.generate import SLOT_STUB_MARK, generate  # noqa: E402
from specforge_web.tools import kit_lint, template_check, ts_check  # noqa: E402

MANIFEST = json.loads((ROOT / "design-system" / "primeng" / "kit-manifest.json").read_text(encoding="utf-8"))
RULES = (ROOT / "design-system" / "primeng" / "rules.md").read_text(encoding="utf-8")
MAX_ATTEMPTS = 3
PARALLEL = 4

HELPERS = {
    # имя → (import, путь от src/app/slots, это компонент?)
    "AppValueComponent": ("../shared/value/app-value.component", True),
    "AppStateComponent": ("../shared/state/app-state.component", True),
    "AppMapComponent": ("../shared/map/app-map.component", True),
    "RouterLink": ("@angular/router", True),
    "FormsModule": ("@angular/forms", True),
    "itemsOf": ("../shared/screen/data-helpers", False),
    "totalOf": ("../shared/screen/data-helpers", False),
    "formatValue": ("../shared/value/format", False),
    "fmtDuration": ("../shared/value/format", False),
    "fmtDate": ("../shared/value/format", False),
    "fmtRelative": ("../shared/value/format", False),
    "toSeverity": ("../shared/value/format", False),
    "interpolate": ("../shared/value/format", False),
    "toFeatures": ("../shared/map/features", False),
    "MapLayerData": ("../shared/map/features", False),
}
ANGULAR = ["computed", "signal", "effect", "inject", "DestroyRef"]

SLOT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["template", "members", "modules"],
    "properties": {
        "template": {"type": "string", "description": "HTML-шаблон Angular (control flow @if/@for), только компоненты из списка"},
        "members": {"type": "string", "description": "Тело класса TypeScript (поля/computed/методы), без конструктора и декоратора"},
        "modules": {"type": "array", "items": {"enum": sorted({c["module"] for c in MANIFEST["components"]} | {d["module"] for d in MANIFEST["directives"]})}},
        "helpers": {"type": "array", "items": {"enum": sorted(HELPERS)}},
        "angular": {"type": "array", "items": {"enum": ANGULAR}},
        "models": {"type": "array", "items": {"type": "string"}},
        "styles": {"type": "string", "description": "SCSS компонента; цвета только var(--sf-*)/var(--p-*)"},
    },
}

SYSTEM = f"""Ты — frontend-разработчик Angular 20 (standalone, signals, control flow @if/@for) с UI Kit PrimeNG 20.
Ты реализуешь ОДИН виджет-слот внутри уже сгенерированного экрана. Обвязку компонента (декоратор, импорты, input `data`)
сделает генератор — ты возвращаешь JSON: template, members (тело класса), modules (модули PrimeNG), helpers, angular, models, styles.

Контракт слота:
- `readonly data = input<Record<string, unknown>>({{}})` уже объявлен: данные источников экрана, ключи — имена источников
  (primary, …). Значение — ответ API: Page {{items, total}} или массив или объект. Приводи тип явно:
  `computed(() => itemsOf(this.data()['primary'] as Thing[] | {{ items: Thing[] }} | null))` (itemsOf — в helpers).
- Используй только типы моделей из раздела «Модели» (перечисли их в models) и только компоненты из «Разрешённые компоненты».
- members: только поля и методы класса с модификатором protected/readonly; без конструктора, без декораторов, без import.
- template: никаких <button>, <table>, <select>, <input> без pInputText, цветов литералом; у интерактивных элементов data-testid.
- Значения форматируй через <app-value [value]="…" format="…" /> (helper AppValueComponent) или функции format*.
- Пустые данные — показывай понятный текст (class="sf-muted"), не падай на null.

{RULES}"""


def models_text(project: Path, names: set[str]) -> str:
    src = (project / "src" / "app" / "generated" / "api" / "models.ts").read_text(encoding="utf-8")
    blocks = {m.group(1): m.group(0) for m in re.finditer(r"export (?:interface|type) ([A-Z][A-Za-z0-9]*)[^\n]*(?:\{[\s\S]*?\n\}|;)", src)}
    want, seen = list(names), set()
    while want:
        n = want.pop()
        if n in seen or n not in blocks:
            continue
        seen.add(n)
        want += [x for x in re.findall(r"\b[A-Z][A-Za-z0-9]*\b", blocks[n]) if x in blocks and x not in seen]
    return "\n".join(blocks[n] for n in sorted(seen))


def component_docs(tags: list[str]) -> str:
    by = {c["tag"]: c for c in MANIFEST["components"]}
    tags = tags or [c["tag"] for c in MANIFEST["components"]][:12]
    out = []
    for t in tags:
        c = by.get(t)
        if not c:
            continue
        line = f"- <{t}> ({c['module']}): {c['purpose']}"
        if c.get("props"):
            line += f" Свойства: {', '.join(c['props'][:10])}."
        if c.get("example"):
            line += f" Пример: {c['example']}"
        out.append(line)
    out.append("- Директивы: pInputText (InputTextModule), pTextarea (TextareaModule), pTooltip (TooltipModule)")
    return "\n".join(out)


def escape_tpl(s: str) -> str:
    return s.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")


def assemble(slot: dict, out: dict) -> tuple[str, str, str]:
    """→ (ts, html, scss). Импорты строит код по спискам modules/helpers/angular/models."""
    by_mod = {c["module"]: c["from"] for c in MANIFEST["components"]} | {d["module"]: d["from"] for d in MANIFEST["directives"]}
    mods = [m for m in dict.fromkeys(out.get("modules", [])) if m in by_mod]
    helpers = [h for h in dict.fromkeys(out.get("helpers", [])) if h in HELPERS]
    members = out.get("members", "")
    template = out.get("template", "")
    # автоподстановка: используемое, но не объявленное
    for h, (_, is_comp) in HELPERS.items():
        tag_used = is_comp and h == "AppValueComponent" and "<app-value" in template or is_comp and h == "AppStateComponent" and "<app-state" in template \
            or is_comp and h == "AppMapComponent" and "<app-map" in template or h == "RouterLink" and "routerLink" in template \
            or h == "FormsModule" and "ngModel" in template
        fn_used = not is_comp and re.search(rf"\b{h}\b", members)
        if (tag_used or fn_used) and h not in helpers:
            helpers.append(h)
    for c in MANIFEST["components"]:
        if re.search(rf"<{re.escape(c['tag'])}[\s/>]", template, re.I) and c["module"] not in mods:
            mods.append(c["module"])
    for d in MANIFEST["directives"]:
        if re.search(rf"\b{d['attr']}\b", template) and d["module"] not in mods:
            mods.append(d["module"])
    angular = ["ChangeDetectionStrategy", "Component", "input"] + [a for a in ANGULAR if re.search(rf"\b{a}\b", members)]
    models = sorted(set(out.get("models", [])) | set(re.findall(r"\b[A-Z][A-Za-z0-9]*\b", members)) - set(HELPERS) - set(angular) - {"Math", "Number", "String", "Date", "Record", "Array", "JSON", "Object", "Boolean", "Partial", "Readonly"})
    imports = [f"import {{ {', '.join(sorted(set(angular)))} }} from '@angular/core';"]
    for mod in sorted(mods):
        imports.append(f"import {{ {mod} }} from '{by_mod[mod]}';")
    by_path: dict[str, list[str]] = {}
    for h in helpers:
        by_path.setdefault(HELPERS[h][0], []).append(h)
    for path, names in sorted(by_path.items()):
        imports.append(f"import {{ {', '.join(sorted(names))} }} from '{path}';")
    known_models = set(re.findall(r"export (?:interface|type) ([A-Z][A-Za-z0-9]*)", (Path(slot["_project"]) / "src/app/generated/api/models.ts").read_text(encoding="utf-8")))
    models = [m for m in models if m in known_models]
    if models:
        imports.append(f"import {{ {', '.join(models)} }} from '../generated/api/models';")
    comp_imports = sorted(mods) + [h for h in helpers if HELPERS[h][1]]
    sel = Path(slot["file"]).name.replace(".slot.ts", "")
    ts = f"""// @slot-implemented — frontend-агент (LLM). Задание: {slot['task'].splitlines()[0][:200]}
// Требования: {', '.join(slot.get('requirements', [])) or '—'}
{chr(10).join(imports)}

@Component({{
  selector: 'slot-{sel}',
  imports: [{', '.join(comp_imports)}],
  templateUrl: './{sel}.slot.html',
  styleUrl: './{sel}.slot.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
}})
export class {slot['class']} {{
  /** Данные всех источников экрана: {{ primary, <secondary>... }} */
  readonly data = input<Record<string, unknown>>({{}});

{chr(10).join('  ' + line if line.strip() else '' for line in members.strip().splitlines())}
}}
"""
    return ts, template.strip() + "\n", (out.get("styles") or ":host { display: block; }").strip() + "\n"


class FrontendAgent:
    def __init__(self, llm, project: Path, spec: dict, openapi: dict, log=print):
        self.llm = TrackedLLM(llm, Usage()) if llm is not None else None
        self.project, self.spec, self.openapi, self.log = project, spec, openapi, log

    def slot_context(self, slot: dict) -> str:
        screen = next(s for s in self.spec["screens"] if s["id"] == slot["screen"])
        from specforge_web.generators.angular.openapi_ts import TsGen
        gen = TsGen(self.openapi)
        data = screen.get("data") or {}
        sources = {"primary": data.get("primary")} | dict(data.get("secondary") or {})
        src_lines, names = [], set()
        for k, ds in sources.items():
            if ds and ds["operationId"] in gen.ops:
                m = gen.ops[ds["operationId"]]
                src_lines.append(f"- data()['{k}'] ← {m.op_id}: {m.response_type} ({m.response_kind}, элемент {m.item_type})")
                names |= set(re.findall(r"\b[A-Z][A-Za-z0-9]*\b", m.response_type + " " + m.item_type))
        return (f"## Экран\n{screen['id']} «{screen['title']}» ({screen['archetype']})\n\n## Слот\nid: {slot['slot']}\nзадание: {slot['task']}\n"
                f"требования: {', '.join(slot.get('requirements', [])) or '—'}\n\n## Источники данных (input data)\n" + ("\n".join(src_lines) or "—") +
                f"\n\n## Модели\n```ts\n{models_text(self.project, names)}\n```\n\n## Разрешённые компоненты\n{component_docs(slot.get('components', []))}")

    def check_slot(self, slot: dict) -> list[str]:
        base = self.project / slot["file"].replace(".ts", "")
        ts_path, html_path = Path(str(base) + ".ts"), Path(str(base) + ".html")
        errs = []
        m = kit_lint.Manifest(ROOT / "design-system" / "primeng" / "kit-manifest.json")
        for f in kit_lint.lint_template(html_path.read_text(encoding="utf-8"), html_path.name, m) + kit_lint.lint_ts(ts_path.read_text(encoding="utf-8"), ts_path.name, m):
            if f.severity == "error":
                errs.append(f"{f.file}:{f.line} {f.code}: {f.message} → {f.fix}")
        for f in template_check.check_component(ts_path, html_path, set()):
            errs.append(f"{html_path.name}:{f.line} {f.code}: {f.message}")
        if ts_check.find_tsc():
            for e in ts_check.run(self.project):
                if e["file"].replace("\\", "/").endswith(slot["file"]):
                    errs.append(f"{ts_path.name}:{e['line']} {e['code']}: {e['message']}")
        return errs

    def fill_slot(self, slot: dict, feedback: list[str] | None = None) -> dict:
        slot = {**slot, "_project": str(self.project)}
        stub_path = self.project / slot["file"]
        stub = stub_path.read_text(encoding="utf-8")
        ctx = self.slot_context(slot)
        if feedback:
            cur_html = Path(str(stub_path).replace(".ts", ".html"))
            ctx += ("\n\n## Замечания UI-ревью (исправь их)\n" + "\n".join(f"- {f}" for f in feedback[:10]) +
                    f"\n\n## Текущая реализация\n```ts\n{stub[:4000]}\n```" + (f"\n```html\n{cur_html.read_text(encoding='utf-8')[:4000]}\n```" if cur_html.exists() else ""))
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": ctx + "\n\nВерни JSON слота."}]
        errs: list[str] = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                out = self.llm.json(msgs, SLOT_SCHEMA, tag="fe.slot" if attempt == 1 else "fe.repair", thinking=False, max_tokens=4000)
            except Exception as e:  # noqa: BLE001
                errs = [f"LLM: {e}"]
                break
            ts, html, scss = assemble(slot, out)
            base = str(stub_path).replace(".ts", "")
            Path(base + ".ts").write_text(ts, encoding="utf-8")
            Path(base + ".html").write_text(html, encoding="utf-8")
            Path(base + ".scss").write_text(scss, encoding="utf-8")
            errs = self.check_slot(slot)
            if not errs:
                return {"slot": slot["slot"], "screen": slot["screen"], "ok": True, "attempts": attempt, "lines": ts.count("\n") + html.count("\n"),
                        "_file": slot["file"], "_stub": stub}
            self.log(f"[fe] слот {slot['screen']}/{slot['slot']}: попытка {attempt}, ошибок {len(errs)}")
            msgs = msgs + [{"role": "assistant", "content": json.dumps(out, ensure_ascii=False)[:6000]},
                           {"role": "user", "content": "Проверки отклонили слот. Исправь и верни JSON целиком.\n" + "\n".join(f"- {e}" for e in errs[:15])}]
        # откат к заглушке
        stub_path.write_text(stub, encoding="utf-8")
        for ext in (".html", ".scss"):
            Path(str(stub_path).replace(".ts", ext)).unlink(missing_ok=True)
        return {"slot": slot["slot"], "screen": slot["screen"], "ok": False, "attempts": MAX_ATTEMPTS, "errors": errs[:10]}


def revert_slot(project: Path, result: dict) -> None:
    """Вернуть слот к заглушке генератора (тот же код, что при неудаче пре-шлюзов)."""
    path = project / result["_file"]
    path.write_text(result["_stub"], encoding="utf-8")
    for ext in (".html", ".scss"):
        Path(str(path).replace(".ts", ext)).unlink(missing_ok=True)
    result.update(ok=False, rolledBack=True, lines=0)


def blamed_slots(errors: list[GateError], results: list[dict]) -> list[dict]:
    """Слоты LLM, на которые указывают ошибки сборки (по имени файла слота)."""
    text = "\n".join(f"{e.file or ''} {e.message}" for e in errors).replace("\\", "/")
    out = []
    for r in results:
        if r.get("ok") and r.get("_file"):
            stem = Path(r["_file"]).name.replace(".slot.ts", "")
            if f"{stem}.slot" in text:
                out.append(r)
    return out


def build_with_rollback(project: Path, results: list[dict], log=print) -> tuple[list[GateError], dict, list[str]]:
    """ng build + jest; при падении — откат слотов LLM (сначала виновных, потом всех) и пересборка.
    → (ошибки итоговой сборки, тайминги, сообщения об откате)."""
    errs, timings = npm_build(project, log)
    notes: list[str] = []
    rounds = [lambda e: blamed_slots(e, results), lambda e: [r for r in results if r.get("ok") and r.get("_file")]]
    for pick in rounds:
        if not errs or any(e.code == "FE_NO_NPM" for e in errs):
            break
        victims = pick(errs)
        if not victims:
            continue
        first = errs[0]
        for r in victims:
            revert_slot(project, r)
            notes.append(f"слот {r['screen']}/{r['slot']} откатился к заглушке: сборка упала "
                         f"({(first.file or '')}{':' + str(first.line) if first.line else ''} {first.message[:200]})")
        log(f"[fe] сборка упала — откат слотов: {', '.join(r['slot'] for r in victims)}; пересборка")
        errs, t2 = npm_build(project, log)
        timings |= {f"{k} (после отката)": v for k, v in t2.items()}
    return errs, timings, notes


def project_gate(project: Path) -> list[GateError]:
    errs: list[GateError] = []
    for f in kit_lint.lint_dir(project / "src"):
        errs.append(GateError(message=f"{f.code}: {f.message} → {f.fix}", file=f"src/{f.file}", line=f.line, severity=f.severity, code=f.code))
    for f in template_check.check_dir(project / "src"):
        errs.append(GateError(message=f"{f.code}: {f.message}", file=str(Path(f.file).relative_to(project)) if Path(f.file).is_absolute() else f.file, line=f.line, code=f.code))
    if ts_check.find_tsc():
        for e in ts_check.run(project):
            errs.append(GateError(message=f"{e['code']}: {e['message']}", file=e["file"], line=e["line"], code=e["code"]))
    return errs


RE_NG_ERR = re.compile(r"\[ERROR\] (?P<code>[A-Z]+\d+): (?P<msg>.+?)\s*(?:\[plugin[^\]]*\])?\s*\n\s*\n\s+(?P<file>[^:\s]+):(?P<line>\d+):", re.M)


def npm_build(project: Path, log=print) -> tuple[list[GateError], dict]:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        return [GateError(message="npm не найден — ng build пропущен", severity="warning", code="FE_NO_NPM")], {}
    timings = {}
    steps = ([[npm, "install", "--no-audit", "--no-fund"]] if not (project / "node_modules").exists() else []) + \
        [[npm, "run", "build"], [npm, "test", "--", "--ci", "--silent"]]
    for cmd in steps:
        t0 = time.time()
        log(f"[fe] {' '.join(cmd[1:])}")
        p = subprocess.run(cmd, cwd=project, capture_output=True, text=True, encoding="utf-8", errors="replace")
        timings[" ".join(cmd[1:3])] = round(time.time() - t0, 1)
        if p.returncode != 0:
            out = p.stdout + "\n" + p.stderr
            errs = [GateError(message=f"{m['code']}: {m['msg'].strip()}", file=m["file"], line=int(m["line"]), code=m["code"]) for m in RE_NG_ERR.finditer(out)]
            return errs or [GateError(message=f"{' '.join(cmd[1:])} завершился с кодом {p.returncode}: {out[-1500:]}", code="FE_BUILD")], timings
    return [], timings


def run(input_dir: Path, output_dir: Path, llm, build: bool = False, log=print, feedback: dict[str, list[str]] | None = None) -> GateResult:
    t0 = time.time()
    spec = json.loads((input_dir / "03_ux" / "screen-spec.web.json").read_text(encoding="utf-8"))
    openapi = yaml.safe_load((input_dir / "02_system" / "openapi.yaml").read_text(encoding="utf-8"))
    ev_path = input_dir / "03_ux" / "mock-events.json"
    events = json.loads(ev_path.read_text(encoding="utf-8")) if ev_path.exists() else None
    project = output_dir / "admin"
    summary = generate(spec, openapi, project, events)
    log(f"[fe] сгенерировано: страниц {len(summary['pages'])}, строк {summary['generatedLines']}, слотов {summary['slotsTotal']}")
    agent = FrontendAgent(llm, project, spec, openapi, log)
    feedback = feedback or {}
    slots = [s for s in json.loads((project / "slots.json").read_text(encoding="utf-8")) if not s["implemented"] or s["screen"] in feedback]
    results = []
    if slots and agent.llm:
        # ts-check проекта общий, поэтому слоты проверяются последовательно
        for s in slots:
            results.append(agent.fill_slot(s, feedback.get(s["screen"])))
    elif slots:
        results = [{"slot": s["slot"], "screen": s["screen"], "ok": False, "attempts": 0, "errors": ["нет LLM — оставлена заглушка"]} for s in slots]
    errors = project_gate(project)
    for r in results:
        if not r["ok"]:
            errors.append(GateError(message=f"слот {r['screen']}/{r['slot']} не реализован: {'; '.join(r.get('errors', []))[:400]}",
                                    severity="warning", code="FE_SLOT_STUB"))
    timings = {}
    if build and not any(e.severity == "error" for e in errors):
        build_errs, timings, notes = build_with_rollback(project, results, log)
        errors += build_errs
        errors += [GateError(message=n, severity="warning", code="FE_SLOT_ROLLBACK") for n in notes]
    llm_lines = sum(r.get("lines", 0) for r in results if r["ok"])
    results = [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]
    det = summary["generatedLines"] + summary["scaffoldLines"]
    u = agent.llm.usage if agent.llm else Usage()
    metrics = {"pages": len(summary["pages"]), "generatedLines": summary["generatedLines"], "scaffoldLines": summary["scaffoldLines"],
               "llmLines": llm_lines, "deterministicShare": round(det / max(1, det + llm_lines), 3),
               "slots": results, "build": "skipped" if not build else ("ok" if not any(e.code and e.code.startswith(("FE_BUILD", "TS", "NG")) for e in errors) else "failed"),
               "timings": timings, "usage": u.__dict__}
    res = GateResult(passed=not any(e.severity == "error" for e in errors), errors=errors, tokens_in=u.tokens_in, tokens_out=u.tokens_out,
                     tokens_cached=u.tokens_cached, llm_calls=u.calls, attempts=max([r.get("attempts", 1) for r in results] or [1]),
                     duration_sec=round(time.time() - t0, 2), artifacts=[str(project)], metrics=metrics)
    res.write(output_dir)
    (output_dir / "fe-report.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[fe] {'PASSED' if res.passed else 'FAILED'} · доля кода без LLM {metrics['deterministicShare']:.0%} · сборка {metrics['build']}")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SpecForge frontend agent")
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--build", action="store_true", help="npm install + ng build + jest")
    ap.add_argument("--offline", action="store_true", help="без LLM: слоты остаются заглушками")
    a = ap.parse_args(argv)
    res = run(a.inp, a.out, None if a.offline else llm_from_env(), build=a.build)
    for e in res.errors:
        if e.severity == "error":
            print(f"{e.file}:{e.line}: {e.message}")
    return 0 if res.passed else 1


if __name__ == "__main__":
    sys.exit(main())
