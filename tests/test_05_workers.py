import asyncio

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from lab.communication.message_queue import MessageQueue
from lab.coordinator import Coordinator
from lab.workers import BaseWorker, CodeAgent, DataAgent, EvaluatorAgent


class FakeModel:
    def __init__(self, *responses):
        self.responses = list(responses)

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages):
        return self.responses.pop(0)


def test_specialist_workers_have_tools_and_prompts(tmp_path):
    model = FakeModel(AIMessage(content="done"))
    data = DataAgent(model, tmp_path)
    code = CodeAgent(model, tmp_path)
    evaluator = EvaluatorAgent(model)

    assert data.name == "data_agent" and {"inspect_data", "validate_data"} <= set(data.tools)
    assert code.name == "code_agent" and {"create_file", "run_python"} <= set(code.tools)
    assert evaluator.name == "evaluator_agent" and {"score_result", "validate_result"} <= set(evaluator.tools)
    assert all(worker.system_prompt for worker in (data, code, evaluator))


def test_worker_process_executes_tool_and_returns_metadata():
    @tool
    def add_one(value: int) -> int:
        """Add one to an integer."""
        return value + 1

    model = FakeModel(
        AIMessage(content="", tool_calls=[{"name": "add_one", "args": {"value": 4}, "id": "call-1"}]),
        AIMessage(content="5"),
    )
    worker = BaseWorker("test", model, [add_one])
    result = worker.process("Add one to four")

    assert result["status"] == "success"
    assert result["result"] == "5"
    assert result["metadata"]["tools_used"] == ["add_one"]


def test_worker_reports_tool_failures():
    @tool
    def fail_tool() -> str:
        """Always fail."""
        raise RuntimeError("tool failed")

    model = FakeModel(AIMessage(content="", tool_calls=[{"name": "fail_tool", "args": {}, "id": "call-1"}]))
    result = BaseWorker("test", model, [fail_tool]).process("run")
    assert result["status"] == "error"
    assert "tool failed" in result["error"]


def test_message_queue_metadata_log_and_timeout():
    async def scenario():
        queue = MessageQueue()
        queue.register_agent("worker")
        queue.register_agent("coordinator")
        message_id = await queue.send_message("coordinator", "worker", {"type": "task"}, correlation_id="t1")
        message = await queue.receive_message("worker", timeout=0.5)
        assert message["id"] == message_id
        assert message["correlation_id"] == "t1"
        assert message["from"] == "coordinator" and message["to"] == "worker"
        assert queue.get_message_log("worker")[0]["id"] == message_id
        try:
            await queue.receive_message("worker", timeout=0.01)
        except TimeoutError:
            pass
        else:
            raise AssertionError("expected queue timeout")

    asyncio.run(scenario())


def test_coordinator_dispatches_to_workers_and_collects_results():
    class EchoWorker:
        async def process_async(self, content, parameters=None):
            await asyncio.sleep(0.01)
            return {"status": "success", "result": f"{content}:{parameters.get('n')}"}

    async def scenario():
        coordinator = Coordinator({"worker_a": EchoWorker(), "worker_b": EchoWorker()})
        results = await coordinator.execute_tasks([
            {"id": "one", "worker": "worker_a", "content": "alpha", "parameters": {"n": 1}},
            {"id": "two", "worker": "worker_b", "content": "beta", "parameters": {"n": 2}},
            {"id": "bad", "worker": "unknown", "content": "missing"},
        ], timeout=1)
        assert [result["task_id"] for result in results] == ["one", "two", "bad"]
        assert results[0]["result"] == "alpha:1" and results[0]["status"] == "success"
        assert results[1]["result"] == "beta:2" and results[1]["status"] == "success"
        assert results[2]["status"] == "error"

    asyncio.run(scenario())
