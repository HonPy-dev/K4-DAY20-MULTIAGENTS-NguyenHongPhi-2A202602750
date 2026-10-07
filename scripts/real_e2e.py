"""Run exactly one opt-in model-backed worker request against a temporary SQLite fixture."""
from __future__ import annotations

import asyncio
import gc
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lab.model import make_model
from lab.workers import DataAgent


async def main():
    with tempfile.TemporaryDirectory(prefix="lab-real-e2e-") as temp:
        root = Path(temp)
        with sqlite3.connect(root / "sales.db") as db:
            db.execute("CREATE TABLE sales (quarter TEXT, revenue INTEGER)")
            db.executemany("INSERT INTO sales VALUES (?, ?)", [("Q3", 5000000), ("Q2", 4200000)])
        model = make_model()
        worker = DataAgent(model, root)
        request = (
            "Use the query_database tool exactly once to find total Q3 revenue. "
            "The database is sales.db in the workspace. Query: "
            "SELECT sum(revenue) AS total FROM sales WHERE quarter='Q3'. "
            "Then reply with the total and state that the value came from the query."
        )
        started = time.perf_counter()
        result = await worker.process_async(request)
        seconds = time.perf_counter() - started
        response = {
            "status": result.get("status"),
            "result": result.get("result"),
            "error": result.get("error"),
            "metadata": result.get("metadata"),
            "seconds": round(seconds, 3),
            "model": type(model).__name__,
        }
        print(json.dumps(response, indent=2, ensure_ascii=False))
        if result.get("status") != "success":
            raise SystemExit(1)
        del worker, model, result
        gc.collect()


if __name__ == "__main__":
    asyncio.run(main())
