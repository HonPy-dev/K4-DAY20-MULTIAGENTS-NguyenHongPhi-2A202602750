"""Offline integration check for worker-to-tool execution; no model credentials needed."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from langchain_core.messages import AIMessage
from lab.workers import CodeAgent, DataAgent, EvaluatorAgent


class FakeModel:
    def __init__(self, *responses):
        self.responses = list(responses)

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages):
        return self.responses.pop(0)


def call(name, args, call_id="call-1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def main():
    with tempfile.TemporaryDirectory(prefix="worker-tools-") as temp:
        root = Path(temp)
        database = sqlite3.connect(root / "sales.db")
        database.execute("CREATE TABLE sales (year INTEGER, revenue INTEGER)")
        database.executemany("INSERT INTO sales VALUES (?, ?)", [(2026, 5_000_000), (2025, 4_000_000)])
        database.commit()
        database.close()

        data = DataAgent(FakeModel(call("query_database", {
            "path": "sales.db", "query": "SELECT sum(revenue) AS total FROM sales WHERE year=2026"
        }), AIMessage(content="2026 revenue: 5M USD")), root)
        data_result = data.process("Query 2026 sales")
        assert data_result["status"] == "success" and data_result["metadata"]["tools_used"] == ["query_database"]
        print("Data Agent: read-only SQLite query ✓")

        code = CodeAgent(FakeModel(
            call("create_file", {"path": "report.py", "content": "print('chart-ready')\n"}),
            call("run_python", {"path": "report.py"}, "call-2"),
            AIMessage(content="Report script ran successfully"),
        ), root)
        code_result = code.process("Create and run a report script")
        assert code_result["status"] == "success" and len(code_result["metadata"]["tools_used"]) == 2
        assert (root / "report.py").is_file()
        print("Code Agent: create and execute workspace script ✓")

        evaluator = EvaluatorAgent(FakeModel(
            call("score_result", {"accuracy": 85, "completeness": 90, "clarity": 80, "performance": 85}),
            AIMessage(content=json.dumps({"score": 85, "feedback": "Good", "issues": [], "suggestions": []})),
        ))
        evaluation = evaluator.process("Score the report")
        assert evaluation["status"] == "success" and evaluation["metadata"]["tools_used"] == ["score_result"]
        print("Evaluator Agent: score tool ✓")

    print("All tool integration checks passed! (3/3)")


if __name__ == "__main__":
    main()
