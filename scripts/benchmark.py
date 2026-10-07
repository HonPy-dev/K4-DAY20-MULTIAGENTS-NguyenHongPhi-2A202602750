"""Offline coordinator benchmark using deterministic fake workers only."""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lab.coordinator import Coordinator
from lab.cached_coordinator import CachingCoordinator


class FakeWorker:
    def __init__(self, name: str, delay: float = 0.002):
        self.name = name
        self.delay = delay
        self.calls = 0

    async def process_async(self, content: str, parameters=None):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return {"status": "success", "result": f"{self.name}:{content}"}


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percent + 0.5)))
    return ordered[index]


async def run_benchmark(iterations: int) -> dict:
    worker_set = [FakeWorker("data_agent"), FakeWorker("code_agent")]
    coordinator = Coordinator(worker_set)
    cached = CachingCoordinator(worker_set)
    latencies = []
    failures = 0
    started_all = time.perf_counter()
    for index in range(iterations):
        start = time.perf_counter()
        result = await coordinator.process_request(
            {"task_type": "complex", "content": f"benchmark request {index}"}, timeout=2
        )
        latency = time.perf_counter() - start
        latencies.append(latency)
        failures += result["status"] != "success"
    elapsed = time.perf_counter() - started_all

    cache_request = {"task_type": "data_analysis", "content": "repeatable cache benchmark"}
    cold_started = time.perf_counter()
    cold_result = await cached.process_request(cache_request, timeout=2)
    cold_latency = time.perf_counter() - cold_started
    warm_started = time.perf_counter()
    warm_result = await cached.process_request(cache_request, timeout=2)
    warm_latency = time.perf_counter() - warm_started
    return {
        "benchmark": "offline_fake_workers",
        "uses_llm_api": False,
        "iterations": iterations,
        "latency_seconds": {
            "min": min(latencies), "max": max(latencies), "mean": statistics.mean(latencies),
            "median": statistics.median(latencies), "p50": percentile(latencies, 0.50),
            "p99": percentile(latencies, 0.99),
        },
        "throughput_requests_per_minute": iterations / elapsed * 60 if elapsed else None,
        "error_rate": failures / iterations,
        "elapsed_seconds": elapsed,
        "cache_comparison": {
            "cold_seconds": cold_latency, "warm_seconds": warm_latency,
            "warm_cache_hit": warm_result.get("cache_hit"),
            "worker_calls": sum(w.calls for w in worker_set),
            "note": "Single repeated request with fake workers; timing is illustrative, not statistically significant.",
        },
        "note": "Measures local orchestration and fake workers only; not provider/model latency.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--output", default="report/benchmark_offline.json")
    args = parser.parse_args()
    if args.iterations < 3:
        parser.error("--iterations must be at least 3")
    report = asyncio.run(run_benchmark(args.iterations))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Saved: {output}")


if __name__ == "__main__":
    main()
