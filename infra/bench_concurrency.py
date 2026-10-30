"""Бенчмарк параллельности LLM-эндпоинта: throughput и p50/p95 latency при 1/2/4/8 потоках.

Рекомендуемый LLM_MAX_CONCURRENCY — наибольшее число потоков, при котором p95 latency выросла
не более чем в 1,5 раза относительно одного потока (и не было ошибок).
Результат — bench/concurrency.md.

    uv run python infra/bench_concurrency.py [--levels 1,2,4,8] [--requests 4] [--max-tokens 128]

Эндпоинт и модель — из .env (LLM_BASE_URL, LLM_MODEL, LLM_API_KEY). Запрос короткий и одинаковый,
temperature из профиля CODE (> 0) — дедупликация ответов не срабатывает.
"""

import argparse
import sys
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GROWTH_LIMIT = 1.5
DEFAULT_LEVELS = (1, 2, 4, 8)
PROMPT = "Перечисли через запятую пять языков программирования и одной фразой объясни выбор."

CallFn = Callable[[], int]
"""Один запрос к эндпоинту; возвращает число сгенерированных токенов."""


@dataclass
class LevelResult:
    threads: int
    requests: int
    errors: int
    wall_s: float
    p50_s: float
    p95_s: float
    tokens: int

    @property
    def throughput_rps(self) -> float:
        return (self.requests - self.errors) / self.wall_s if self.wall_s else 0.0

    @property
    def tokens_per_s(self) -> float:
        return self.tokens / self.wall_s if self.wall_s else 0.0


def percentile(values: Sequence[float], q: float) -> float:
    """Перцентиль с линейной интерполяцией (q от 0 до 100)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def run_level(call: CallFn, threads: int, requests: int) -> LevelResult:
    latencies: list[float] = []
    tokens = 0
    errors = 0
    lock = threading.Lock()

    def one() -> None:
        nonlocal tokens, errors
        started = time.perf_counter()
        try:
            produced = call()
        except Exception:
            with lock:
                errors += 1
            return
        elapsed = time.perf_counter() - started
        with lock:
            latencies.append(elapsed)
            tokens += produced

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        for future in [pool.submit(one) for _ in range(requests)]:
            future.result()
    wall = time.perf_counter() - started
    return LevelResult(
        threads=threads,
        requests=requests,
        errors=errors,
        wall_s=wall,
        p50_s=percentile(latencies, 50),
        p95_s=percentile(latencies, 95),
        tokens=tokens,
    )


def bench(
    call: CallFn, levels: Sequence[int] = DEFAULT_LEVELS, requests_per_thread: int = 4
) -> list[LevelResult]:
    call()  # прогрев: загрузка модели и prefix cache не должны попасть в замер
    return [run_level(call, n, n * requests_per_thread) for n in levels]


def recommend(results: Sequence[LevelResult], growth: float = GROWTH_LIMIT) -> int:
    """Наибольшее число потоков без ошибок, где p95 ≤ growth × p95 минимального уровня."""
    ok = [r for r in results if r.errors == 0]
    if not ok:
        return 1
    base = min(ok, key=lambda r: r.threads)
    good = [r.threads for r in ok if r.p95_s <= growth * base.p95_s]
    return max(good) if good else base.threads


def render(results: Sequence[LevelResult], best: int, endpoint: str, model: str) -> str:
    base = min(results, key=lambda r: r.threads)
    lines = [
        "# Бенчмарк параллельности LLM-эндпоинта",
        "",
        f"Эндпоинт `{endpoint}`, модель `{model}`, {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.",
        "",
        "| Потоки | Запросов | Ошибок | Запросов/с | Токенов/с | p50, с | p95, с "
        "| p95 к 1 потоку |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        growth = r.p95_s / base.p95_s if base.p95_s else 0.0
        mark = " ✅" if r.threads == best else ""
        lines.append(
            f"| {r.threads}{mark} | {r.requests} | {r.errors} | {r.throughput_rps:.2f} "
            f"| {r.tokens_per_s:.1f} | {r.p50_s:.2f} | {r.p95_s:.2f} | ×{growth:.2f} |"
        )
    lines += [
        "",
        f"**Рекомендуемый `LLM_MAX_CONCURRENCY={best}`** — наибольшее число потоков, при котором "
        f"p95 выросла не более чем в {GROWTH_LIMIT} раза и не было ошибок.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from dotenv import find_dotenv, load_dotenv  # noqa: PLC0415

    from specforge.llm import ChatMessage, LLMClient, LLMConfig  # noqa: PLC0415

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--levels", default=",".join(map(str, DEFAULT_LEVELS)))
    parser.add_argument("--requests", type=int, default=4, help="запросов на поток")
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--out", type=Path, default=ROOT / "bench" / "concurrency.md")
    args = parser.parse_args(argv)
    levels = [int(x) for x in args.levels.split(",") if x.strip()]

    load_dotenv(find_dotenv(usecwd=True))
    config = LLMConfig.from_env().model_copy(
        update={"max_concurrency": max(levels), "max_retries": 0}
    )
    llm = LLMClient(config)

    def call() -> int:
        response = llm.chat(
            [ChatMessage(role="user", content=PROMPT)], thinking=False, max_tokens=args.max_tokens
        )
        return response.usage.completion_tokens

    results = bench(call, levels, args.requests)
    best = recommend(results)
    report = render(results, best, config.base_url, config.model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
