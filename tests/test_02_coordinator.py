import asyncio

import pytest

from lab.coordinator import Coordinator


class FakeWorker:
    def __init__(self, name, result=None, error=None, delay=0):
        self.name = name
        self.result = result or f"result from {name}"
        self.error = error
        self.delay = delay

    async def process_async(self, content, parameters=None):
        await asyncio.sleep(self.delay)
        if self.error:
            raise RuntimeError(self.error)
        return {"status": "success", "result": self.result, "metadata": {"worker": self.name}}


def coordinator():
    return Coordinator([FakeWorker("data_agent"), FakeWorker("code_agent"), FakeWorker("evaluator_agent")])


def test_coordinator_init_accepts_worker_list_and_dict():
    workers = [FakeWorker("data_agent"), FakeWorker("code_agent")]
    assert set(Coordinator(workers).workers) == {"data_agent", "code_agent"}
    assert set(Coordinator({worker.name: worker for worker in workers}).workers) == {"data_agent", "code_agent"}
    with pytest.raises(ValueError):
        Coordinator([])


def test_parse_request_infers_task_types_and_preserves_structured_fields():
    c = coordinator()
    assert c.parse_request("Analyze sales data")['task_type'] == "data_analysis"
    assert c.parse_request("Create a Python chart")['task_type'] == "code_generation"
    assert c.parse_request("Evaluate quality and score the report")['task_type'] == "evaluation"
    assert c.parse_request("Analyze sales data and create a chart")['task_type'] == "complex"
    parsed = c.parse_request({"content": "Do work", "task_type": "code_generation", "priority": "high", "parameters": {"x": 1}})
    assert parsed["priority"] == "high" and parsed["parameters"] == {"x": 1}
    with pytest.raises(ValueError):
        c.parse_request("  ")


def test_route_task_selects_workers_and_splits_complex_requests():
    c = coordinator()
    assert c.route_task("data_analysis", "inspect data")[0]["worker"] == "data_agent"
    assert c.route_task("code_generation", "write code")[0]["worker"] == "code_agent"
    assert c.route_task("evaluation", "score result")[0]["worker"] == "evaluator_agent"
    assert [task["worker"] for task in c.route_task("complex", "analyze and chart")] == ["data_agent", "code_agent"]
    with pytest.raises(ValueError):
        c.route_task({"task_type": "data_analysis", "content": "x", "worker": "missing"})


def test_aggregate_results_preserves_outputs_and_reports_partial_failures():
    aggregate = coordinator().aggregate_results([
        {"task_id": "1", "status": "success", "result": "sales total"},
        {"task_id": "2", "status": "error", "error": "worker failed"},
    ])
    assert aggregate["status"] == "partial"
    assert aggregate["summary"] == "sales total"
    assert aggregate["errors"][0]["error"] == "worker failed"
    assert len(aggregate["results"]) == 2
    assert coordinator().aggregate_results([])["status"] == "success"


def test_execute_tasks_handles_worker_error_timeout_and_duplicate_ids():
    async def scenario():
        c = Coordinator([
            FakeWorker("good", result="ok"),
            FakeWorker("bad", error="broken"),
            FakeWorker("slow", delay=0.2),
        ])
        results = await c.execute_tasks([
            {"id": "same", "worker": "good", "content": "first"},
            {"id": "same", "worker": "good", "content": "second"},
            {"id": "bad", "worker": "bad", "content": "fail"},
            {"id": "slow", "worker": "slow", "content": "wait"},
            {"id": "unknown", "worker": "missing", "content": "no"},
        ], timeout=0.05)
        assert len(results) == 5
        assert results[0]["status"] == results[1]["status"] == "success"
        assert results[2]["status"] == "error"
        assert results[3]["status"] == "timeout"
        assert results[4]["status"] == "error"
        assert len(c.active_tasks) == 5

    asyncio.run(scenario())


def test_process_request_end_to_end():
    async def scenario():
        c = Coordinator([FakeWorker("data_agent", result="Revenue is 5M")])
        result = await c.process_request("Analyze the sales data")
        assert result["status"] == "success"
        assert result["summary"] == "Revenue is 5M"
        assert result["request"]["task_type"] == "data_analysis"

    asyncio.run(scenario())
