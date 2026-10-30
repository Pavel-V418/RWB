"""Снимки workspace на git — для отката неудачных ремонтов.

Отдельный репозиторий в /work/.specforge/git (git-dir отдельно от рабочего дерева): не
вмешивается ни в родительский репозиторий хоста, ни в .git сгенерированного проекта. Команды
идут через песочницу, то есть git выполняется там же, где работает агент. Пользовательские
настройки git (подпись коммитов, хуки, autocrlf) на служебные снимки не влияют.

Метка GREEN_TAG — последнее состояние, на котором шлюз этапа был зелёным.
"""

import structlog

from specforge.sandbox.base import Sandbox

log = structlog.get_logger(__name__)

STATE_DIR = ".specforge"
GIT_DIR = f"{STATE_DIR}/git"
GREEN_TAG = "specforge-green"
# Артефакты сборки и зависимости не входят в снимки: они тяжёлые и пересобираются.
EXCLUDES = (
    f"{STATE_DIR}/",
    "node_modules/",
    "bin/",
    "obj/",
    "build/",
    ".gradle/",
    "dist/",
    "__pycache__/",
    ".venv/",
    "TestResults/",
)
_GIT = (
    'git -c "safe.directory=*" -c core.autocrlf=false -c core.safecrlf=false '
    f"-c core.hooksPath={STATE_DIR}/no-hooks -c commit.gpgsign=false -c tag.gpgsign=false "
    "-c user.name=specforge -c user.email=specforge@localhost "
    f"--git-dir={GIT_DIR} --work-tree=."
)
GIT_TIMEOUT_S = 300


class SnapshotError(Exception):
    pass


class Snapshotter:
    def __init__(self, sandbox: Sandbox, agent: str | None = None) -> None:
        self.sandbox = sandbox
        self.green_tag = f"{GREEN_TAG}-{agent}" if agent else GREEN_TAG
        """specforge-green-<agent>: у агентов одного каталога этапа — свои зелёные снимки."""
        self._ready: bool | None = None

    @property
    def available(self) -> bool:
        """git есть в песочнице и служебный репозиторий инициализирован."""
        if self._ready is None:
            self._ready = self._init()
        return self._ready

    def snapshot(self, label: str) -> str:
        """Зафиксировать текущее состояние; вернуть sha снимка."""
        self._require()
        self._git("add -A")
        self._git(f"commit -q --allow-empty -m {_safe(label)}")
        return self._git("rev-parse HEAD").strip()

    def restore(self, ref: str) -> None:
        """Вернуть рабочее дерево к снимку: изменённые файлы восстанавливаются, новые удаляются."""
        self._require()
        self._git(f"reset -q --hard {_safe(ref)}")
        self._git("clean -fdq")

    def changed_files(self, since: str) -> list[str]:
        """Файлы, изменённые после снимка `since` (включая новые и удалённые)."""
        self._require()
        self._git("add -A")
        out = self._git(f"diff --cached --name-only {_safe(since)}")
        return [line.strip() for line in out.splitlines() if line.strip()]

    def diff(self, since: str, max_chars: int = 20_000) -> str:
        self._require()
        self._git("add -A")
        out = self._git(f"diff --cached --no-color {_safe(since)}")
        return out if len(out) <= max_chars else out[:max_chars] + "\n…[diff усечён]"

    def mark_green(self) -> str:
        """Снимок текущего (зелёного) состояния с меткой GREEN_TAG."""
        sha = self.snapshot("green")
        self._git(f"tag -f {self.green_tag} {sha}")
        return sha

    def green(self) -> str | None:
        """sha последнего зелёного снимка, если он был."""
        if not self.available:
            return None
        result = self.sandbox.exec(f"{_GIT} rev-parse -q --verify {self.green_tag}", timeout_s=60)
        if result.exit_code != 0:
            return None
        return result.stdout.strip() or None

    # --- внутреннее ---------------------------------------------------------------------------

    def _init(self) -> bool:
        probe = self.sandbox.exec("git --version", timeout_s=60)
        if probe.exit_code != 0:
            log.warning("snapshots.git_unavailable", output=probe.output[:200])
            return False
        try:
            head = self.sandbox.exec(f"{_GIT} rev-parse -q --verify HEAD", timeout_s=60)
            if head.exit_code != 0:
                # git init не создаёт родителя для --git-dir; заодно — пустой каталог хуков
                self.sandbox.write_text(f"{STATE_DIR}/no-hooks/.keep", "")
                self._git("init -q")
                self.sandbox.write_text(f"{GIT_DIR}/info/exclude", "\n".join(EXCLUDES) + "\n")
                self._git("add -A")
                self._git("commit -q --allow-empty -m init")
        except SnapshotError as exc:
            log.warning("snapshots.init_failed", error=str(exc))
            return False
        return True

    def _require(self) -> None:
        if not self.available:
            raise SnapshotError("снимки недоступны: нет git в песочнице")

    def _git(self, args: str) -> str:
        result = self.sandbox.exec(f"{_GIT} {args}", timeout_s=GIT_TIMEOUT_S)
        if result.exit_code != 0:
            raise SnapshotError(f"git {args.split(maxsplit=1)[0]}: {result.output.strip()[:500]}")
        return result.stdout


def _safe(value: str) -> str:
    """Ссылки и метки снимков — только из безопасных символов (команда идёт в shell)."""
    cleaned = "".join(ch if ch.isalnum() or ch in "-_./" else "-" for ch in value)
    return cleaned or "snapshot"
