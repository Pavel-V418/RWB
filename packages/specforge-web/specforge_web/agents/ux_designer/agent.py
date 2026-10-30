"""UX-агент (шаг 5): требования + OpenAPI → screen-spec.<platform>.json, прошедший шлюз UX.

  run(input_dir, output_dir, llm) -> GateResult
  python -m agents.ux_designer.agent --in artifacts --out artifacts/03_ux [--platforms web,android] [--offline]

Алгоритм (минимум токенов, максимум устойчивости):
  0. baseline: спека из OpenAPI эвристиками (0 токенов) — черновик и страховка;
  1. план экранов — 1 вызов LLM, thinking on, guided JSON;
  2. каждый экран — отдельный вызов, thinking off, guided JSON по подсхеме экрана, параллельно;
     в промпт идёт дайджест только нужных операций и черновик экрана;
  3. шлюз UX по всей спеке; ошибки раскладываются по экранам → точечное исправление (≤2 круга);
  4. экран, который не починился, заменяется базовым; если и это не помогло — вся спека = baseline.
Вход:  <in>/01_requirements/requirements.json, <in>/02_system/openapi.yaml, [<in>/01_requirements/profile.json]
Выход: <out>/screen-spec.<platform>.json, ux-report.json, gate-result.json
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

from specforge_web.agents.common.api_digest import ApiDigest  # noqa: E402
from specforge_web.agents.common.llm import TrackedLLM, Usage, llm_from_env  # noqa: E402
from specforge_web.agents.ux_designer import prompts  # noqa: E402
from specforge_web.agents.ux_designer.baseline import Baseline, default_states, kebab  # noqa: E402
from specforge_web.agents.ux_designer.labels import clean_glossary  # noqa: E402
from specforge_web.agents.ux_designer import trace  # noqa: E402
from specforge_web.agents.ux_designer.flows import user_flows  # noqa: E402
from specforge_web.agents.ux_designer.gate import SCHEMA_PATH, run_gate  # noqa: E402
from specforge_web.contracts.agent.agent_contract import GateError, GateResult  # noqa: E402

MAX_REPAIR_ROUNDS = 2
PARALLEL = 4
SCREEN_SCHEMA_FULL = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
SCREEN_SCHEMA = {"$defs": SCREEN_SCHEMA_FULL["$defs"], **SCREEN_SCHEMA_FULL["$defs"]["screen"]}
RE_SCREEN = re.compile(r"^screens\[([a-z0-9-]+)\]")
RE_SCREEN_IDX = re.compile(r"^screens/(\d+)")


class UxAgent:
    def __init__(self, llm, requirements: dict, openapi: dict, log=print):
        self.llm = TrackedLLM(llm, Usage()) if llm is not None else None
        self.req, self.openapi, self.log = requirements, openapi, log
        self.digest = ApiDigest(openapi)
        self.glossary: dict[str, str] = {}
        self.report: dict = {"platforms": {}}

    # ------------------------------------------------------------ public
    def design(self, platform: str) -> tuple[dict, GateResult, dict]:
        rep: dict = {"fallbackScreens": [], "repairRounds": 0, "droppedScreens": [], "mode": "llm" if self.llm else "baseline"}
        builder = Baseline(self.openapi, self.req, platform, self.glossary)
        base = builder.build()
        if not self.llm:
            return base, run_gate(base, self.openapi, self.req), rep | {"mode": "baseline"}
        try:
            plan = self._plan(platform, base, sorted(builder.misses - set(self.glossary)))
            got = clean_glossary(plan.get("glossary"))
            if got:
                # baseline-экраны (страховка) получают русские подписи от модели — один раз на все платформы
                self.glossary |= got
                base = Baseline(self.openapi, self.req, platform, self.glossary).build()
                rep["glossaryTerms"] = len(got)
            spec = self._screens(platform, plan, base)
        except Exception as e:  # noqa: BLE001 — любая ошибка LLM не должна ронять конвейер
            self.log(f"[ux] LLM недоступна или ответила неверно ({e}) — используем baseline")
            rep |= {"mode": "baseline-fallback", "error": str(e)[:500]}
            return base, run_gate(base, self.openapi, self.req), rep
        spec, res = self._repair_loop(spec, base, rep, platform)
        if not res.passed:
            self.log("[ux] спека не прошла шлюз после исправлений — используем baseline")
            rep["mode"] = "baseline-fallback"
            spec, res = base, run_gate(base, self.openapi, self.req)
        return spec, res, rep

    # ------------------------------------------------------------ stage 1: plan
    def _plan(self, platform: str, base: dict, terms: list[str] | None = None) -> dict:
        base_plan = [{"id": s["id"], "title": s["title"], "archetype": s["archetype"], "route": s["route"],
                      "primaryOperationId": (s.get("data") or {}).get("primary", {}).get("operationId") or (s.get("form") or {}).get("submit", {}).get("operationId"),
                      "requirements": s["requirements"]}
                     for s in base["screens"] if s["archetype"] not in ("login", "forbidden")]
        msgs = [{"role": "system", "content": prompts.SYSTEM_PLAN},
                {"role": "user", "content": prompts.plan_user(self.req, self.digest.text(), base_plan, platform, terms)}]
        plan = self.llm.json(msgs, prompts.PLAN_SCHEMA, tag=f"ux.plan.{platform}", thinking=True, max_tokens=12000)
        return self._normalize_plan(plan, platform, base)

    def _normalize_plan(self, plan: dict, platform: str, base: dict) -> dict:
        ops = self.digest.gen.ops
        seen_ids, seen_routes, screens = set(), set(), []
        for s in plan.get("screens", []):
            s["id"] = kebab(s.get("id", ""))[:48] or f"screen-{len(screens) + 1}"
            if s["id"] in seen_ids or s["id"] in ("login", "forbidden"):
                continue
            route = "/" + "/".join(p if p.startswith(":") else kebab(p) for p in str(s.get("route", "")).strip("/").split("/") if p)
            if route in seen_routes:
                route = f"/{s['id']}"
            if s.get("primaryOperationId") and s["primaryOperationId"] not in ops:
                s.pop("primaryOperationId")
            s["secondaryOperationIds"] = [o for o in s.get("secondaryOperationIds", []) if o in ops]
            s["route"], s["roles"] = route, [kebab(r) for r in s.get("roles", [])] or Baseline(self.openapi, self.req, platform).all_roles()
            seen_ids.add(s["id"])
            seen_routes.add(route)
            screens.append(s)
        # покрытие FR: если LLM пропустила требование — привязываем к экрану из baseline с тем же primary
        frs = [f["id"] for f in self.req.get("functional", []) if platform in f.get("channels", [platform])]
        covered = {r for s in screens for r in s["requirements"]}
        for fr in frs:
            if fr in covered:
                continue
            owner = next((b for b in base["screens"] if fr in b["requirements"]), None)
            prim = (owner or {}).get("data", {}).get("primary", {}).get("operationId")
            target = next((s for s in screens if prim and s.get("primaryOperationId") == prim), None)
            if not target and screens:
                # не «на первый экран», а по смыслу; уверенность потом проверит trace.assess
                fr_obj = next(f for f in self.req.get("functional", []) if f["id"] == fr)
                target = trace.choose(fr_obj, screens, self.req.get("roles"))[0][0]
            if target:
                target["requirements"].append(fr)
        plan["screens"] = screens
        return plan

    # ------------------------------------------------------------ stage 2: screens
    def _screen_ops(self, item: dict) -> list[str]:
        ops = [o for o in [item.get("primaryOperationId")] + item.get("secondaryOperationIds", []) if o]
        # действия: операции того же ресурса
        if item.get("primaryOperationId"):
            root = self.digest.gen.ops[item["primaryOperationId"]].path.strip("/").split("/")[0]
            ops += [o for o, m in self.digest.gen.ops.items() if m.path.strip("/").split("/")[0] == root and o not in ops]
        return ops[:12] or list(self.digest.gen.ops)[:12]

    def _gen_screen(self, item: dict, plan: dict, base_by_key: dict, platform: str) -> dict:
        base_screen = base_by_key.get(item["id"]) or base_by_key.get((item["archetype"], item.get("primaryOperationId")))
        msgs = [{"role": "system", "content": prompts.SYSTEM_SCREEN},
                {"role": "user", "content": prompts.screen_user(item, plan, self.digest.text(self._screen_ops(item)),
                                                                 self.req.get("functional", []), base_screen, platform)}]
        s = self.llm.json(msgs, SCREEN_SCHEMA, tag=f"ux.screen.{platform}", thinking=False, max_tokens=6000)
        return self._normalize_screen(s, item)

    @staticmethod
    def _normalize_screen(s: dict, item: dict) -> dict:
        """Детерминированные правки, на которые не стоит тратить попытки LLM."""
        s = dict(s or {})
        for k in ("id", "archetype", "route", "roles"):
            s[k] = item[k]
        s.setdefault("title", item["title"])
        s["requirements"] = sorted(set(s.get("requirements") or []) | set(item.get("requirements", [])))
        st = s.get("states") or {}
        dflt = default_states()
        s["states"] = {k: (st.get(k) if isinstance(st.get(k), dict) and (k in ("loading", "ready") or st[k].get("title")) else dflt[k]) for k in dflt}
        if s["states"]["loading"].get("kind") not in ("skeleton", "spinner", "progress"):
            s["states"]["loading"] = {"kind": "skeleton"}
        for a in s.get("actions", []) or []:
            if a.get("kind") == "danger" and not a.get("confirm"):
                a["confirm"] = f"Подтвердите действие «{a.get('label', '')}»"
        return s

    def _screens(self, platform: str, plan: dict, base: dict) -> dict:
        base_by_key: dict = {}
        for b in base["screens"]:
            base_by_key[b["id"]] = b
            prim = (b.get("data") or {}).get("primary", {}).get("operationId") or (b.get("form") or {}).get("submit", {}).get("operationId")
            base_by_key.setdefault((b["archetype"], prim), b)
        self._base_by_key = base_by_key
        self._plan_items = {it["id"]: it for it in plan["screens"]}
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            futures = {it["id"]: pool.submit(self._safe_gen, it, plan, base_by_key, platform) for it in plan["screens"]}
        screens = [futures[it["id"]].result() for it in plan["screens"]]
        screens = [s for s in screens if s]
        # служебные экраны — детерминированно из baseline
        fixed = [b for b in base["screens"] if b["archetype"] in ("login", "forbidden")]
        screens = [x for x in fixed if x["archetype"] == "login"] + screens + [x for x in fixed if x["archetype"] == "forbidden"]
        for f in fixed:
            if f["archetype"] == "forbidden" and f.get("actions"):
                f["actions"][0]["navigateTo"] = next((s["id"] for s in screens if s["archetype"] not in ("login", "forbidden")), f["actions"][0]["navigateTo"])
        nav_limit = 7 if platform == "web" else 5
        nav = [{"screen": it["id"], "label": (it.get("navLabel") or it["title"])[:30], "icon": it.get("navIcon") or Baseline._icon(it)}
               for it in plan["screens"] if it.get("inNavigation", it["archetype"] in ("dashboard", "map", "list", "timeline") and ":" not in it["route"])
               and any(s["id"] == it["id"] for s in screens)][:nav_limit]
        if platform == "android":
            for n in nav:
                if n["icon"].startswith("pi "):
                    n["icon"] = {"pi pi-home": "home", "pi pi-map": "map", "pi pi-history": "history"}.get(n["icon"], "list")
        roles = sorted({r for s in screens for r in s["roles"]})
        role_titles = {kebab(r["id"]): r["title"] for r in (plan.get("roles") or []) + self.req.get("roles", [])}
        app = copy.deepcopy(base["app"])
        app["title"] = (plan.get("appTitle") or app["title"])[:60]
        self.report.setdefault("assumptions", {})[platform] = plan.get("assumptions", [])
        return {"specVersion": "1.0", "app": app, "roles": [{"id": r, "title": role_titles.get(r, r)} for r in roles],
                "navigation": nav, "screens": screens}

    def _safe_gen(self, item, plan, base_by_key, platform):
        try:
            return self._gen_screen(item, plan, base_by_key, platform)
        except Exception as e:  # noqa: BLE001
            self.log(f"[ux] экран {item['id']}: генерация не удалась ({e}) — подставим черновик")
            b = base_by_key.get(item["id"]) or base_by_key.get((item["archetype"], item.get("primaryOperationId")))
            if b:
                return {**copy.deepcopy(b), "id": item["id"], "route": item["route"], "title": item["title"],
                        "requirements": sorted(set(b["requirements"]) | set(item["requirements"]))}
            return None

    # ------------------------------------------------------------ stage 3: gate + repair
    def _errors_by_screen(self, spec: dict, res: GateResult) -> tuple[dict[str, list[str]], list[str]]:
        by, glob = {}, []
        ids = [s.get("id") for s in spec.get("screens", [])]
        for e in res.blocking:
            m = RE_SCREEN.match(e.file or "") or RE_SCREEN.match(e.message)
            mi = RE_SCREEN_IDX.match(e.file or "")
            sid = m.group(1) if m else (ids[int(mi.group(1))] if mi and int(mi.group(1)) < len(ids) else None)
            if sid:
                by.setdefault(sid, []).append(f"[{e.code}] {e.message}")
            else:
                glob.append(f"[{e.code}] {e.message}")
        return by, glob

    def _repair_loop(self, spec: dict, base: dict, rep: dict, platform: str) -> tuple[dict, GateResult]:
        res = run_gate(spec, self.openapi, self.req)
        for rnd in range(MAX_REPAIR_ROUNDS + 1):
            if res.passed:
                break
            by, glob = self._errors_by_screen(spec, res)
            self._fix_global(spec, glob, res)
            if rnd == MAX_REPAIR_ROUNDS:
                # последний шанс: базовые экраны вместо сломанных
                for sid in by:
                    self._fallback_screen(spec, sid, rep)
                res = run_gate(spec, self.openapi, self.req)
                break
            rep["repairRounds"] = rnd + 1
            self.log(f"[ux] исправление, круг {rnd + 1}: экранов с ошибками {len(by)}, общих ошибок {len(glob)}")
            with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
                fixed = {sid: pool.submit(self._repair_screen, spec, sid, errs) for sid, errs in by.items()}
            for sid, fut in fixed.items():
                new = fut.result()
                if new:
                    spec["screens"] = [new if s.get("id") == sid else s for s in spec["screens"]]
            res = run_gate(spec, self.openapi, self.req)
        return spec, res

    def _repair_screen(self, spec: dict, sid: str, errs: list[str]) -> dict | None:
        cur = next((s for s in spec["screens"] if s.get("id") == sid), None)
        if not cur or cur.get("archetype") in ("login", "forbidden"):
            return None
        msgs = [{"role": "system", "content": prompts.SYSTEM_SCREEN},
                {"role": "user", "content": prompts.repair_user(cur, errs)}]
        try:
            new = self.llm.json(msgs, SCREEN_SCHEMA, tag="ux.repair", thinking=False, max_tokens=6000)
            return self._normalize_screen(new, cur)
        except Exception:  # noqa: BLE001
            return None

    def _fallback_screen(self, spec: dict, sid: str, rep: dict) -> None:
        cur = next((s for s in spec["screens"] if s.get("id") == sid), None)
        if not cur:
            return
        # операция берётся из плана: в сломанном экране она может быть выдуманной
        prim = (getattr(self, "_plan_items", {}).get(sid) or {}).get("primaryOperationId") or \
            (cur.get("data") or {}).get("primary", {}).get("operationId") or (cur.get("form") or {}).get("submit", {}).get("operationId")
        b = self._base_by_key.get(sid) or self._base_by_key.get((cur.get("archetype"), prim))
        if b:
            nb = {**copy.deepcopy(b), "id": sid, "route": cur["route"], "title": cur.get("title", b["title"]),
                  "requirements": sorted(set(b["requirements"]) | set(cur.get("requirements", [])))}
            # ссылки базового экрана на экраны, которых нет в спеке, убираем
            spec["screens"] = [nb if s.get("id") == sid else s for s in spec["screens"]]
            rep["fallbackScreens"].append(sid)
        else:
            spec["screens"] = [s for s in spec["screens"] if s.get("id") != sid]
            spec["navigation"] = [n for n in spec["navigation"] if n["screen"] != sid]
            rep["droppedScreens"].append(sid)
        self._prune_links(spec)

    @staticmethod
    def _prune_links(spec: dict) -> None:
        ids = {s["id"] for s in spec["screens"]}

        def walk(node):
            if isinstance(node, dict):
                for k in ("linkTo", "rowLink", "navigateTo"):
                    if isinstance(node.get(k), str) and node[k] not in ids:
                        node.pop(k)
                        if k == "linkTo" and node.get("format") == "link":
                            node["format"] = "text"
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(spec["screens"])
        spec["navigation"] = [n for n in spec["navigation"] if n["screen"] in ids]

    def _fix_global(self, spec: dict, glob: list[str], res: GateResult) -> None:
        """Ошибки вне экранов (навигация, auth, непокрытые FR) правим кодом."""
        self._prune_links(spec)
        for e in res.blocking:
            if e.code == "UX_FR_UNCOVERED":
                fr_obj = next((f for f in self.req.get("functional", []) if f["id"] == e.file), None)
                if fr_obj:
                    nav = {n["screen"]: n.get("label", "") for n in spec.get("navigation", [])}
                    for target in trace.choose(fr_obj, spec["screens"], self.req.get("roles"), nav)[0]:
                        target["requirements"].append(e.file)
            if e.code == "UX_FR_UNKNOWN":
                known = {f["id"] for f in self.req.get("functional", [])}
                for s in spec["screens"]:
                    s["requirements"] = [r for r in s["requirements"] if not r.startswith("FR-") or r in known]
            if e.code == "UX_NAV_LIMIT":
                spec["navigation"] = spec["navigation"][:5]


# ---------------------------------------------------------------- contract entry
def run(input_dir: Path, output_dir: Path, llm, platforms: list[str] | None = None, log=print) -> GateResult:
    t0 = time.time()
    req = json.loads((input_dir / "01_requirements" / "requirements.json").read_text(encoding="utf-8"))
    for name, key in (("stories.md", "stories"), ("acceptance.feature", "acceptance")):
        p = input_dir / "01_requirements" / name
        if p.exists():
            req[key] = p.read_text(encoding="utf-8")
    openapi = yaml.safe_load((input_dir / "02_system" / "openapi.yaml").read_text(encoding="utf-8"))
    profile_path = input_dir / "01_requirements" / "profile.json"
    if platforms is None:
        profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else {}
        platforms = profile.get("platforms") or sorted({c for f in req.get("functional", []) for c in f.get("channels", [])} & {"web", "android"}) or ["web"]
    agent = UxAgent(llm, req, openapi, log=log)
    output_dir.mkdir(parents=True, exist_ok=True)
    errors: list[GateError] = []
    metrics: dict = {}
    artifacts = []
    for platform in platforms:
        spec, res, rep = agent.design(platform)
        path = output_dir / f"screen-spec.{platform}.json"
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts.append(str(path))
        # пользовательские потоки по ролям (основные и альтернативные сценарии) — из той же спеки, что и код
        flows_path = output_dir / f"user-flows.{platform}.md"
        flows_path.write_text(user_flows(spec, req), encoding="utf-8")
        artifacts.append(str(flows_path))
        # честная трассируемость: насколько каждый экран отвечает своему FR (кто бы ни привязал — модель или эвристика)
        tr = trace.assess(spec, req, by=rep["mode"])
        (output_dir / f"trace.{platform}.json").write_text(json.dumps(tr, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts.append(str(output_dir / f"trace.{platform}.json"))
        for fid, t in tr.items():
            if t["confidence"] == "low":
                res.errors.append(GateError(message=f"{fid} «{t['title']}» привязано к {', '.join(t['screens'])} без явного совпадения "
                                                    f"({t['reason']}) — проверьте вручную", file=fid, severity="warning", code="UX_FR_WEAK"))
        res.metrics["coverageConfident"] = trace.confident_share(tr)
        res.metrics["weakRequirements"] = [fid for fid, t in tr.items() if t["confidence"] == "low"]
        errors += res.errors
        metrics[platform] = res.metrics | rep
        log(f"[ux] {platform}: {'PASSED' if res.passed else 'FAILED'} · экранов {res.metrics.get('screens')} · покрытие {res.metrics.get('coverage')} · режим {rep['mode']}")
    u = agent.llm.usage if agent.llm else Usage()
    report = {"platforms": metrics, "assumptions": agent.report.get("assumptions", {}), "usage": u.__dict__}
    (output_dir / "ux-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    result = GateResult(passed=not any(e.severity == "error" for e in errors), errors=errors, tokens_in=u.tokens_in,
                        tokens_out=u.tokens_out, tokens_cached=u.tokens_cached, llm_calls=u.calls,
                        attempts=1 + max((m.get("repairRounds", 0) for m in metrics.values()), default=0),
                        duration_sec=round(time.time() - t0, 2), artifacts=artifacts, metrics=metrics)
    result.write(output_dir)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SpecForge UX agent")
    ap.add_argument("--in", dest="inp", type=Path, required=True, help="папка artifacts (01_requirements, 02_system)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--platforms", help="web,android (по умолчанию — из profile.json / каналов требований)")
    ap.add_argument("--offline", action="store_true", help="без LLM: только baseline")
    a = ap.parse_args(argv)
    llm = None if a.offline else llm_from_env()
    res = run(a.inp, a.out, llm, a.platforms.split(",") if a.platforms else None)
    print(json.dumps({"passed": res.passed, "tokensIn": res.tokens_in, "tokensOut": res.tokens_out, "calls": res.llm_calls,
                      "sec": res.duration_sec}, ensure_ascii=False))
    return 0 if res.passed else 1


if __name__ == "__main__":
    sys.exit(main())
