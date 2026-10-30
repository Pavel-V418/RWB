"""Эмбеддинги для векторного поиска по документации. Режим выбирается при старте:

(а) EMBED_BASE_URL задан → OpenAI-совместимый /embeddings (EMBED_MODEL, EMBED_API_KEY), батчи по 64;
(б) иначе локально через fastembed (ONNX, без torch): EMBED_LOCAL_MODEL, по умолчанию
    paraphrase-multilingual-MiniLM-L12-v2; веса — в data/models, во время прогона только с диска
    (на площадке может не быть интернета; скачивает infra/fetch_docs.sh);
(в) ни того, ни другого → None: поиск только FTS5, warning в лог (и строка в doctor).

Векторы нормализованы (L2), поэтому косинус — скалярное произведение. Модели e5 требуют
префиксов «query: » / «passage: » — они добавляются по имени модели.

    uv run python -m specforge.rag.embeddings download   — скачать веса локальной модели
"""

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
import numpy.typing as npt
import structlog

from specforge.paths import PROJECT_ROOT

log = structlog.get_logger(__name__)

Vectors = npt.NDArray[np.float32]
DEFAULT_LOCAL_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODELS_DIR = PROJECT_ROOT / "data" / "models"
BATCH = 64


class Embedder(Protocol):
    mode: Literal["remote", "local", "fake"]
    model: str

    def embed_passages(self, texts: Sequence[str]) -> Vectors: ...

    def embed_query(self, text: str) -> Vectors: ...


def needs_prefix(model: str) -> bool:
    return "e5" in model.lower().rsplit("/", 1)[-1]


def normalize(vectors: npt.ArrayLike) -> Vectors:
    array = np.asarray(vectors, dtype=np.float32)
    if array.ndim == 1:
        array = array[None, :]
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    return np.asarray(array / np.where(norms == 0, 1.0, norms), dtype=np.float32)


class RemoteEmbedder:
    mode: Literal["remote", "local", "fake"] = "remote"

    def __init__(self, base_url: str, model: str, api_key: str = "EMPTY") -> None:
        from openai import OpenAI  # noqa: PLC0415

        self.model = model
        self._client = OpenAI(base_url=base_url, api_key=api_key or "EMPTY", timeout=120)

    def _embed(self, texts: Sequence[str]) -> Vectors:
        rows: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            batch = list(texts[start : start + BATCH])
            response = self._client.embeddings.create(model=self.model, input=batch)
            rows += [item.embedding for item in sorted(response.data, key=lambda d: d.index)]
        return normalize(rows) if rows else np.zeros((0, 0), dtype=np.float32)

    def embed_passages(self, texts: Sequence[str]) -> Vectors:
        prefix = "passage: " if needs_prefix(self.model) else ""
        return self._embed([prefix + t for t in texts])

    def embed_query(self, text: str) -> Vectors:
        prefix = "query: " if needs_prefix(self.model) else ""
        return np.asarray(self._embed([prefix + text])[0], dtype=np.float32)


class LocalEmbedder:
    mode: Literal["remote", "local", "fake"] = "local"

    def __init__(
        self,
        model: str = DEFAULT_LOCAL_MODEL,
        cache_dir: Path = MODELS_DIR,
        *,
        offline: bool = True,
    ) -> None:
        from fastembed import TextEmbedding  # noqa: PLC0415 — ONNX грузится только по требованию

        self.model = model
        self._model: Any = TextEmbedding(
            model_name=model, cache_dir=str(cache_dir), local_files_only=offline
        )

    def _embed(self, texts: Sequence[str]) -> Vectors:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        return normalize(list(self._model.embed(list(texts), batch_size=BATCH)))

    def embed_passages(self, texts: Sequence[str]) -> Vectors:
        prefix = "passage: " if needs_prefix(self.model) else ""
        return self._embed([prefix + t for t in texts])

    def embed_query(self, text: str) -> Vectors:
        prefix = "query: " if needs_prefix(self.model) else ""
        return np.asarray(self._embed([prefix + text])[0], dtype=np.float32)


def embedder_from_env(env: Mapping[str, str] | None = None) -> Embedder | None:
    env = os.environ if env is None else env
    base_url = env.get("EMBED_BASE_URL", "")
    if base_url:
        model = env.get("EMBED_MODEL", "")
        if not model:
            log.warning("embeddings.no_model", reason="EMBED_BASE_URL задан, EMBED_MODEL — нет")
            return None
        return RemoteEmbedder(base_url, model, env.get("EMBED_API_KEY", ""))
    model = env.get("EMBED_LOCAL_MODEL") or DEFAULT_LOCAL_MODEL
    try:
        return LocalEmbedder(model, Path(env.get("EMBED_CACHE_DIR") or MODELS_DIR))
    except Exception as exc:  # нет fastembed или весов на диске
        log.warning(
            "embeddings.fts_only",
            model=model,
            error=str(exc)[:200],
            hint="infra/fetch_docs.sh скачает веса; иначе поиск только FTS5",
        )
        return None


def describe(embedder: Embedder | None) -> str:
    """Строка для doctor и логов: remote <модель> / local <модель> / FTS-only."""
    return "FTS-only" if embedder is None else f"{embedder.mode} {embedder.model}"


def download(model: str | None = None, cache_dir: Path = MODELS_DIR) -> str:
    """Скачать веса локальной модели в data/models (для работы без интернета)."""
    name = model or os.environ.get("EMBED_LOCAL_MODEL") or DEFAULT_LOCAL_MODEL
    cache_dir.mkdir(parents=True, exist_ok=True)
    embedder = LocalEmbedder(name, cache_dir, offline=False)
    embedder.embed_query("проверка")
    return name


if __name__ == "__main__":
    if sys.argv[1:2] == ["download"]:
        print(f"веса {download(sys.argv[2] if len(sys.argv) > 2 else None)} → {MODELS_DIR}")
    else:
        raise SystemExit("использование: python -m specforge.rag.embeddings download [модель]")
