"""DevOps-агент: стенд продукта из шаблонов + шлюз «compose up + healthcheck» + ремонт по диагностике.

    python -m specforge_backend.devops.agent <artifacts_dir> [--seed DIR] [--no-generate] [--check /health/ready ...]
                                  [--max-attempts 3] [--down] [--keep-going]

Контракт команды: run(input_dir, output_dir, llm) -> GateResult{passed, errors, tokens, attempts}.
  input_dir  — artifacts (04_backend после backend-агента, 07_tests);
  output_dir — artifacts/08_infra.

1. Детерминированно: generators/gen-infra.sh (compose, deploy.sh, .gitlab-ci.yml, db-init, моки кейса).
2. Шлюз: docker compose up --build --wait (healthcheck каждого сервиса) и HTTP-проверки снаружи
   (/health/ready, /swagger/v1/swagger.json, /metrics → 200).
3. Провал → модели уходит диагностика (статусы сервисов, логи упавших, compose и Dockerfile; секреты из .env
   замаскированы). Модель отвечает слоем причины:
   - infra — полные новые версии разрешённых файлов и/или несекретные переменные .env → повторный шлюз;
   - app — ошибка в коде/DDL: текст для backend-агента пишется в 08_infra/devops-feedback.txt
     (python -m specforge_backend.backend.agent <artifacts> --tags <Tag> --feedback 08_infra/devops-feedback.txt).
Итог — 08_infra/gate.json (формат шлюзов команды) и 08_infra/devops-agent.json; ответы модели — 08_infra/.agent/.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from specforge_backend.backend.llm import LlmClient, estimate_tokens

ROOT = Path(__file__).resolve().parents[2]
CHECKS = ["/health/ready", "/swagger/v1/swagger.json", "/metrics"]
# Что модели разрешено менять (относительно artifacts). Код приложения — только через backend-агента.
ALLOWED = ["08_infra/docker-compose.yml", "08_infra/docker-compose.override.yml", "04_backend/Dockerfile",
           "08_infra/mocks/*/Dockerfile"]
SECRET_KEY = re.compile(r"PASSWORD|SECRET|TOKEN|KEY", re.IGNORECASE)
FORBIDDEN = [
    (re.compile(r"privileged:\s*true"), "SEC003: privileged-контейнеры запрещены"),
    (re.compile(r"docker\.sock"), "SEC003: монтировать docker.sock запрещено — это root на хосте"),
    (re.compile(r"network_mode:\s*[\"']?host"), "SEC003: network_mode: host запрещён"),
    (re.compile(r"pid:\s*[\"']?host|cap_add:"), "SEC003: pid: host и cap_add запрещены"),
]
JSON_BLOCK = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)

SYSTEM = """Ты — DevOps-инженер. Стенд продукта поднимается `docker compose up --build --wait` на VM (4 CPU, 3.8 ГБ RAM)
и не прошёл шлюз. По диагностике найди причину и ответь ТОЛЬКО одним блоком ```json:
{"layer": "infra" | "app", "diagnosis": "причина в 1–3 предложениях со ссылкой на строку лога",
 "files": {"<путь из списка разрешённых>": "<ПОЛНОЕ новое содержимое файла>"}, "env": {"KEY": "value"}}

- infra: причина в compose/Dockerfile/переменных окружения (порт занят, неверный healthcheck, имя хоста сервиса, лимит памяти,
  порядок запуска, путь сборки). Верни только изменённые файлы целиком; env — только несекретные переменные (порты, флаги).
- app: падает сам код или SQL схемы (исключение .NET при старте, ошибка миграции/DDL в db-init, 500 на проверке).
  files и env пустые; diagnosis — что именно сломано, с текстом исключения: это уйдёт backend-агенту как обратная связь.
Нельзя: менять имена сервисов (backend, postgres, kafka — на них завязаны web и CI) и порт backend 8080 внутри сети,
убирать healthcheck и лимиты памяти, privileged, docker.sock, network_mode: host, pid: host, cap_add, менять секреты."""


@dataclass
class GateResult:
    passed: bool
    errors: list[dict] = field(default_factory=list)
    tokens: int = 0
    attempts: int = 0
    diagnosis: list[dict] = field(default_factory=list)  # по попыткам: layer, diagnosis, files


def sh(cmd: list[str], cwd: Path | None = None, timeout: int = 900) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as e:
        return 124, f"timeout {timeout} s: {' '.join(cmd)}\n{(e.stdout or '')}{(e.stderr or '')}"


def read_env(infra: Path) -> dict[str, str]:
    env = {}
    path = infra / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    return env


def masker(env: dict[str, str]):
    secrets = sorted((v for k, v in env.items() if SECRET_KEY.search(k) and len(v) >= 4), key=len, reverse=True)

    def mask(text: str) -> str:
        for value in secrets:
            text = text.replace(value, "***")
        return text
    return mask


class Stand:
    def __init__(self, artifacts: Path, checks: list[str]):
        self.artifacts = artifacts
        self.infra = artifacts / "08_infra"
        self.checks = checks

    def dc(self, *args: str) -> list[str]:
        cmd = ["docker", "compose", "--project-directory", str(self.infra)]
        if (self.infra / ".env").exists():
            cmd += ["--env-file", str(self.infra / ".env")]
        return cmd + list(args)

    def base_url(self) -> str:
        return f"http://127.0.0.1:{read_env(self.infra).get('BACKEND_PORT', '8080')}"

    def services(self) -> list[dict]:
        code, out = sh(self.dc("ps", "--all", "--format", "json"))
        if code != 0:
            return []
        out = out.strip()
        rows = json.loads(out) if out.startswith("[") else [json.loads(l) for l in out.splitlines() if l.startswith("{")]
        return [{"service": r.get("Service"), "state": r.get("State"), "health": r.get("Health", ""),
                 "exit_code": r.get("ExitCode")} for r in rows]

    def stop_other_stands(self) -> None:
        """Стенды других кейсов (specforge-*) держат те же порты: останавливаем без -v, как deploy.sh."""
        code, cfg = sh(self.dc("config", "--format", "json"))
        if code != 0:
            return
        project = json.JSONDecoder().raw_decode(cfg, cfg.index("{"))[0]["name"]  # в sh() stderr идёт следом
        code, out = sh(["docker", "compose", "ls", "-q"])
        for other in out.split() if code == 0 else []:
            if other.startswith("specforge") and other != project:
                print(f"[stand] останавливаю стенд {other}: порты нужны {project}", file=sys.stderr)
                sh(["docker", "compose", "-p", other, "stop"])

    def gate(self, wait_timeout: int) -> tuple[bool, list[dict], str]:
        """(прошёл, ошибки для gate.json, диагностика для модели)."""
        code, config = sh(self.dc("config", "-q"))
        if code != 0:
            return False, [{"file": "08_infra/docker-compose.yml", "line": None, "message": config.strip()[-1500:]}], \
                "docker compose config:\n" + config[-3000:]
        self.stop_other_stands()
        code, up = sh(self.dc("up", "-d", "--build", "--wait", "--wait-timeout", str(wait_timeout)), timeout=wait_timeout + 600)
        errors: list[dict] = []
        if code != 0:
            errors.append({"file": None, "line": None, "message": "compose up --wait: " + up.strip()[-800:]})
        else:
            base = self.base_url()
            for path in self.checks:
                status, body = http_get(base + path)
                if status != 200:
                    errors.append({"file": None, "line": None, "message": f"GET {path} → {status}: {body[:300]}"})
        if not errors:
            return True, [], ""
        services = self.services()
        broken = [s["service"] for s in services if s["state"] != "running" or s["health"] not in ("", "healthy")]
        logs = []
        for name in broken or [s["service"] for s in services if s["service"] == "backend"]:
            _, out = sh(self.dc("logs", "--no-color", "--tail", "60", name))
            logs.append(f"--- logs {name} (последние 60 строк)\n{out.strip()}")
        diagnostics = "\n\n".join([
            "## Ошибки шлюза\n" + "\n".join(f"- {e['message']}" for e in errors),
            "## Сервисы\n" + json.dumps(services, ensure_ascii=False),
            "## Вывод compose up (хвост)\n" + up.strip()[-2500:],
            *logs,
        ])
        return False, errors, diagnostics


def http_get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def allowed(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, p) for p in ALLOWED) and ".." not in rel


def infra_files(artifacts: Path) -> dict[str, str]:
    files = {}
    for pattern in ALLOWED:
        for path in sorted(artifacts.glob(pattern)):
            files[str(path.relative_to(artifacts))] = path.read_text(encoding="utf-8")
    return files


def repair_prompt(diagnostics: str, files: dict[str, str], env: dict[str, str], previous: list[dict]) -> str:
    env_view = "\n".join(f"{k}={'***' if SECRET_KEY.search(k) else v}" for k, v in env.items()) or "(пусто)"
    parts = [diagnostics,
             "## Разрешённые к изменению файлы\n" + "\n\n".join(f"### {p}\n```\n{c}```" for p, c in files.items()),
             f"## .env (секреты скрыты)\n```\n{env_view}\n```"]
    if previous:
        parts.append("## Прошлые попытки (не повторяй то, что не помогло)\n" +
                     "\n".join(f"- {d['layer']}: {d['diagnosis']} (файлы: {', '.join(d['files']) or '—'})" for d in previous))
    return "\n\n".join(parts) + "\n\nОтветь одним блоком ```json."


def apply_answer(artifacts: Path, answer: dict) -> list[dict]:
    """Пишет файлы и переменные из ответа модели. Возвращает нарушения политики (тогда ничего не пишется)."""
    files = {k: v for k, v in (answer.get("files") or {}).items() if isinstance(v, str)}
    violations = [{"file": rel, "line": None, "message": "файл вне разрешённого списка"} for rel in files if not allowed(rel)]
    for rel, content in files.items():
        for number, line in enumerate(content.splitlines(), 1):
            for pattern, message in FORBIDDEN:
                if pattern.search(line):
                    violations.append({"file": rel, "line": number, "message": message})
    env = {k: str(v) for k, v in (answer.get("env") or {}).items()}
    violations += [{"file": "08_infra/.env", "line": None, "message": f"{k}: менять секреты и неверные имена нельзя"}
                   for k in env if SECRET_KEY.search(k) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", k)]
    if violations:
        return violations
    for rel, content in files.items():
        (artifacts / rel).write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    if env:
        path = artifacts / "08_infra" / ".env"
        lines = [l for l in (path.read_text(encoding="utf-8").splitlines() if path.exists() else [])
                 if l.split("=", 1)[0].strip() not in env]
        path.write_text("\n".join(lines + [f"{k}={v}" for k, v in env.items()]) + "\n", encoding="utf-8")
        path.chmod(0o600)
    return []


def parse_answer(text: str) -> dict | None:
    blocks = JSON_BLOCK.findall(text)
    for block in sorted(blocks, key=len, reverse=True):
        try:
            data = json.loads(block)
            if isinstance(data, dict) and data.get("layer") in ("infra", "app"):
                return data
        except json.JSONDecodeError:
            continue
    return None


def run(input_dir: Path, output_dir: Path, llm: LlmClient | None, seed: Path | None = None, generate: bool = True,
        checks: list[str] | None = None, max_attempts: int = 3, wait_timeout: int = 300, down: bool = False,
        log=print) -> GateResult:
    artifacts, infra = input_dir, output_dir
    result = GateResult(passed=False)
    if generate:
        code, out = sh([str(ROOT / "generators" / "gen-infra.sh"), str(artifacts)] + ([str(seed)] if seed else []))
        if code != 0:
            result.errors = [{"file": None, "line": None, "message": "gen-infra: " + out.strip()[-1500:]}]
            return finish(result, infra, llm)
    trace = infra / ".agent"
    trace.mkdir(parents=True, exist_ok=True)
    (trace / ".gitignore").write_text("*\n", encoding="utf-8")
    (infra / "devops-feedback.txt").unlink(missing_ok=True)
    stand = Stand(artifacts, checks or CHECKS)

    for attempt in range(1, max_attempts + 2):  # последняя итерация — только проверка после ремонта
        started = time.monotonic()
        ok, errors, diagnostics = stand.gate(wait_timeout)
        log(f"[stand] шлюз {attempt}: {'пройден' if ok else f'ошибок {len(errors)}'} ({time.monotonic() - started:.0f} с)")
        result.errors = errors
        if ok:
            result.passed = True
            break
        if llm is None or attempt > max_attempts:
            break
        mask = masker(read_env(infra))
        prompt = mask(repair_prompt(diagnostics, infra_files(artifacts), read_env(infra), result.diagnosis))
        log(f"[stand] ремонт {attempt}: контекст ~{estimate_tokens(SYSTEM + prompt)} токенов")
        result.attempts += 1
        text = llm.chat(SYSTEM, prompt, max_tokens=6000)
        (trace / f"stand-{attempt}.md").write_text(text, encoding="utf-8")
        answer = parse_answer(text)
        if answer is None:
            result.diagnosis.append({"layer": "?", "diagnosis": "ответ без блока ```json", "files": []})
            continue
        result.diagnosis.append({"layer": answer["layer"], "diagnosis": answer.get("diagnosis", ""),
                                 "files": sorted((answer.get("files") or {}).keys())})
        log(f"[stand] {answer['layer']}: {answer.get('diagnosis', '')}")
        if answer["layer"] == "app":
            (infra / "devops-feedback.txt").write_text(
                "Стенд не поднялся из-за кода приложения (диагноз DevOps-агента):\n" + answer.get("diagnosis", "") +
                "\n\nЛоги:\n" + mask(diagnostics)[-3000:] + "\n", encoding="utf-8")
            result.errors.append({"file": "08_infra/devops-feedback.txt", "line": None,
                                  "message": "причина в коде приложения — обратная связь для backend-агента"})
            break
        if violations := apply_answer(artifacts, answer):
            result.diagnosis[-1]["diagnosis"] += " | отклонено политикой: " + "; ".join(v["message"] for v in violations)
            log(f"[stand] правка отклонена политикой: {violations}")

    if down:
        sh(stand.dc("down", "-v", "--remove-orphans"))
    return finish(result, infra, llm)


def finish(result: GateResult, infra: Path, llm: LlmClient | None) -> GateResult:
    result.tokens = llm.usage.total if llm else 0
    infra.mkdir(parents=True, exist_ok=True)
    (infra / "gate.json").write_text(json.dumps({"gate": "stand", "passed": result.passed, "errors": result.errors[:50]},
                                                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (infra / "devops-agent.json").write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("artifacts", type=Path)
    p.add_argument("--seed", type=Path, help="сиды и дополнения кейса для gen-infra (reference/caseN/seed)")
    p.add_argument("--no-generate", action="store_true", help="не перегенерировать 08_infra (проверить и чинить как есть)")
    p.add_argument("--check", action="append", help=f"HTTP-проверка (повторяемая), по умолчанию {' '.join(CHECKS)}")
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--wait-timeout", type=int, default=300)
    p.add_argument("--down", action="store_true", help="после шлюза остановить стенд и удалить тома")
    p.add_argument("--no-llm", action="store_true", help="только генерация и шлюз, без ремонта")
    a = p.parse_args()
    artifacts = a.artifacts.resolve()
    llm = None if a.no_llm else LlmClient.from_env()
    result = run(artifacts, artifacts / "08_infra", llm, a.seed.resolve() if a.seed else None, not a.no_generate,
                 a.check, a.max_attempts, a.wait_timeout, a.down)
    print(json.dumps({k: v for k, v in asdict(result).items() if k != "errors"}, ensure_ascii=False))
    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
