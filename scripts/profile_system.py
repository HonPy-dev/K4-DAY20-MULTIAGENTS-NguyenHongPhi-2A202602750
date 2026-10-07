"""Profile offline coordinator orchestration; does not instantiate an LLM."""
from __future__ import annotations

import argparse
import asyncio
import cProfile
import pstats
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lab.coordinator import Coordinator


class FakeWorker:
    def __init__(self, name: str):
        self.name = name

    async def process_async(self, content: str, parameters=None):
        await asyncio.sleep(0)
        return {"status": "success", "result": f"{self.name}:{content}"}


async def exercise(iterations: int):
    coordinator = Coordinator([FakeWorker("data_agent"), FakeWorker("code_agent")])
    for index in range(iterations):
        await coordinator.process_request({"task_type": "complex", "content": f"profile {index}"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--output", default="report/profile_offline.prof")
    args = parser.parse_args()
    profiler = cProfile.Profile()
    profiler.enable()
    asyncio.run(exercise(args.iterations))
    profiler.disable()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    profiler.dump_stats(str(output))
    pstats.Stats(profiler).sort_stats("cumulative").print_stats(20)
    print(f"Saved offline profile: {output}")


if __name__ == "__main__":
    main()
