import asyncio
import sqlite3

from langchain_core.messages import AIMessage

from lab.coordinator import Coordinator
from lab.workers import DataAgent


class ToolCallingModel:
    def __init__(self, *responses):
        self.responses = list(responses)

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages):
        return self.responses.pop(0)


class EchoWorker:
    def __init__(self, name, delay=0):
        self.name = name
        self.delay = delay

    async def process_async(self, content, parameters=None):
        await asyncio.sleep(self.delay)
        return {"status": "success", "result": f"{self.name}:{content}"}


def test_coordinator_worker_tool_e2e_with_sqlite(tmp_path):
    db_path = tmp_path / "sales.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE sales (quarter TEXT, revenue INTEGER)")
        db.execute("INSERT INTO sales VALUES ('Q3', 5000000)")

    model = ToolCallingModel(
        AIMessage(content="", tool_calls=[{
            "name": "query_database",
            "args": {"path": "sales.db", "query": "SELECT sum(revenue) AS total FROM sales WHERE quarter='Q3'"},
            "id": "db-call",
        }]),
        AIMessage(content="Q3 revenue was 5000000."),
    )
    worker = DataAgent(model, tmp_path)

    async def scenario():
        result = await Coordinator([worker]).process_request("Analyze Q3 sales data")
        assert result["status"] == "success"
        assert "5000000" in result["summary"]
        assert result["results"][0]["metadata"]["tools_used"] == ["query_database"]

    asyncio.run(scenario())


def test_concurrent_coordinator_requests_do_not_cross_route_results():
    async def scenario():
        coordinator = Coordinator([EchoWorker("data_agent", 0.01), EchoWorker("code_agent", 0.01)])
        results = await asyncio.gather(
            coordinator.process_request({"task_type": "data_analysis", "content": "first"}),
            coordinator.process_request({"task_type": "code_generation", "content": "second"}),
        )
        assert "data_agent:first" in results[0]["summary"]
        assert "code_agent:second" in results[1]["summary"]

    asyncio.run(scenario())
