import json
import sqlite3

import pytest

from lab.workers.tools import code_tools, data_tools, evaluator_tools


def by_name(tools):
    return {item.name: item for item in tools}


def test_query_database_is_read_only_and_limited(tmp_path):
    db = tmp_path / "sample.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE sales (region TEXT, amount INTEGER)")
    connection.executemany("INSERT INTO sales VALUES (?, ?)", [("N", 5), ("S", 8)])
    connection.commit()
    connection.close()
    query = by_name(data_tools(tmp_path))["query_database"]

    result = query.invoke({"path": "sample.db", "query": "SELECT region, amount FROM sales", "limit": 1})
    assert result["status"] == "success" and result["rows"] == 1 and result["truncated"]
    with pytest.raises(Exception):
        query.invoke({"path": "sample.db", "query": "DELETE FROM sales"})
    with pytest.raises(Exception):
        query.invoke({"path": "sample.db", "query": "SELECT 1; SELECT 2"})
    with pytest.raises(Exception):
        query.invoke({"path": "../outside.db", "query": "SELECT 1"})
    assert sqlite3.connect(db).execute("SELECT count(*) FROM sales").fetchone()[0] == 2


def test_csv_and_json_validation(tmp_path):
    (tmp_path / "data.csv").write_text("id,name\n1,Ada\n", encoding="utf-8")
    (tmp_path / "data.json").write_text(json.dumps([{"id": 1, "name": "Ada"}]), encoding="utf-8")
    tools = by_name(data_tools(tmp_path))
    assert tools["validate_data"].invoke({"path": "data.csv", "required_columns": ["id", "name"]})["valid"]
    assert tools["validate_data"].invoke({"path": "data.json", "required_columns": ["id", "name"]})["valid"]
    assert not tools["validate_data"].invoke({"path": "data.csv", "required_columns": ["age"]})["valid"]


def test_create_and_edit_file_are_workspace_scoped(tmp_path):
    tools = by_name(code_tools(tmp_path))
    assert "created" in tools["create_file"].invoke({"path": "nested/a.txt", "content": "hello world"})
    assert "updated" in tools["edit_file"].invoke({"path": "nested/a.txt", "old_text": "world", "new_text": "there"})
    assert (tmp_path / "nested/a.txt").read_text(encoding="utf-8") == "hello there"
    with pytest.raises(Exception):
        tools["create_file"].invoke({"path": "nested/a.txt", "content": "overwrite"})
    with pytest.raises(Exception):
        tools["edit_file"].invoke({"path": "nested/a.txt", "old_text": "missing", "new_text": "x"})
    with pytest.raises(Exception):
        tools["create_file"].invoke({"path": "../escape.txt", "content": "bad"})


def test_run_python_captures_output_and_times_out(tmp_path):
    tools = by_name(code_tools(tmp_path, timeout=0.15))
    (tmp_path / "ok.py").write_text("print('hello')\n", encoding="utf-8")
    result = tools["run_python"].invoke({"path": "ok.py"})
    assert result["returncode"] == 0 and result["stdout"].strip() == "hello"
    (tmp_path / "slow.py").write_text("while True: pass\n", encoding="utf-8")
    result = tools["run_python"].invoke({"path": "slow.py"})
    assert result["returncode"] == 124 and "timed out" in result["stderr"]


def test_evaluation_tools_validate_score_compare_and_report():
    tools = by_name(evaluator_tools())
    score = tools["score_result"].invoke({"accuracy": 100, "completeness": 80, "clarity": 70, "performance": 90})
    assert score["score"] == 86 and score["grade"] == "B"
    with pytest.raises(Exception):
        tools["score_result"].invoke({"accuracy": float("nan"), "completeness": 80, "clarity": 70, "performance": 90})
    assert tools["validate_result"].invoke({"required_items": ["a"], "delivered_items": []})["missing"] == ["a"]
    assert tools["compare_results"].invoke({"expected": " ok ", "actual": "ok"})["matches"]
    assert "# Review" in tools["generate_report"].invoke({"title": "Review", "findings": ["passed"]})["markdown"]
