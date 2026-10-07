"""Debug a deterministic offline coordinator request with structured logs."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lab.coordinator import Coordinator


class DebugWorker:
    def __init__(self, name: str):
        self.name = name

    async def process_async(self, content: str, parameters=None):
        logging.getLogger(self.name).debug("handling task content=%r parameters=%r", content, parameters or {})
        return {"status": "success", "result": f"[{self.name}] processed: {content}"}


async def debug_request(request: str):
    coordinator = Coordinator([DebugWorker("data_agent"), DebugWorker("code_agent"), DebugWorker("evaluator_agent")])
    print(f"Debugging request: {request}\n{'-' * 60}")
    result = await coordinator.process_request(request, timeout=10)
    for item in result["results"]:
        print(f"worker={item.get('from')} status={item['status']} result={item.get('result')} error={item.get('error')}")
    print(f"\nAggregate status: {result['status']}\n{result['summary']}")
    if result["errors"]:
        raise RuntimeError(f"worker errors: {result['errors']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", default="Analyze sales data and create a report")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        asyncio.run(debug_request(args.request))
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
