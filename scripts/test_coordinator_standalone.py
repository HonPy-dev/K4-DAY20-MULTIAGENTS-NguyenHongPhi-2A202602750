"""Offline coordinator smoke test; does not require model credentials."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lab.coordinator import Coordinator


class MockWorker:
    def __init__(self, name, result):
        self.name = name
        self.result = result

    async def process_async(self, content, parameters=None):
        await asyncio.sleep(0.01)
        return {"status": "success", "result": self.result}


async def main():
    print("Testing Coordinator with mock workers...\n")
    coordinator = Coordinator([
        MockWorker("data_agent", "Revenue total: 5M USD"),
        MockWorker("code_agent", "Chart created: report.png"),
        MockWorker("evaluator_agent", "Quality score: 95/100"),
    ])

    simple = coordinator.parse_request("Analyze sales data")
    simple_tasks = coordinator.route_task(simple)
    simple_results = await coordinator.execute_tasks(simple_tasks, timeout=1)
    assert simple_results[0]["status"] == "success"
    print("Test 1: Simple task — routed to", simple_tasks[0]["worker"], "✓")

    complex = coordinator.parse_request("Analyze sales data and create a chart")
    complex_tasks = coordinator.route_task(complex)
    complex_results = await coordinator.execute_tasks(complex_tasks, timeout=1)
    assert len(complex_results) == 2 and all(r["status"] == "success" for r in complex_results)
    print("Test 2: Multiple tasks — results from both workers ✓")

    aggregate = coordinator.aggregate_results(complex_results)
    assert "Revenue total" in aggregate["summary"] and "Chart created" in aggregate["summary"]
    print("Test 3: Aggregation — both outputs preserved ✓")
    print("\nAll coordinator smoke tests passed! (3/3)")


if __name__ == "__main__":
    asyncio.run(main())
