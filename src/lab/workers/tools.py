"""Offline tools for specialist workers. Python scripts must be trusted; subprocess limits are not a security sandbox."""
from __future__ import annotations

import csv
import json
import math
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

from langchain_core.tools import tool

MAX_ROWS = 100
MAX_RESULT_BYTES = 256_000
MAX_OUTPUT = 8_000


def _within(root: Path, path: str) -> Path:
    root = root.resolve()
    candidate = (root / path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("path must stay inside the worker workspace")
    return candidate


def data_tools(workspace: str | Path | None = None) -> list[Any]:
    root = Path(workspace or Path.cwd()).resolve()

    @tool
    def query_database(path: str, query: str, limit: int = 100) -> dict:
        """Run a single read-only SELECT query against a SQLite database inside the workspace."""
        target = _within(root, path)
        if not target.is_file():
            raise ValueError("database file does not exist")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_ROWS:
            raise ValueError(f"limit must be between 1 and {MAX_ROWS}")
        statement = query.strip()
        if not statement or ";" in statement.rstrip(";"):
            raise ValueError("exactly one SQL statement is allowed")
        if not statement.rstrip().endswith(";"):
            statement += ";"
        leading = statement.lstrip().split(None, 1)[0].upper().rstrip(";")
        if leading not in {"SELECT", "WITH"}:
            raise ValueError("only SELECT or read-only WITH queries are allowed")
        uri = f"file:{quote(target.as_posix(), safe='/:')}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only = ON")
            cursor = connection.execute(statement)
            rows = cursor.fetchmany(limit + 1)
            columns = [item[0] for item in cursor.description or []]
            payload = []
            truncated = len(rows) > limit
            for row in rows[:limit]:
                item = dict(row)
                payload.append(item)
                if len(json.dumps(payload, default=str).encode("utf-8")) > MAX_RESULT_BYTES:
                    payload.pop()
                    truncated = True
                    break
            return {"status": "success", "rows": len(payload), "columns": columns,
                    "data": payload, "truncated": truncated}
        finally:
            connection.close()

    @tool
    def inspect_data(path: str) -> dict:
        """Inspect a local CSV or JSON file and return its shape and field names."""
        target = _within(root, path)
        if target.suffix.lower() == ".csv":
            with target.open(newline="", encoding="utf-8-sig") as stream:
                reader = csv.DictReader(stream)
                rows = list(reader)
                return {"columns": reader.fieldnames or [], "rows": len(rows), "sample": rows[:5]}
        if target.suffix.lower() == ".json":
            data = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(data, list):
                columns = list(data[0]) if data and isinstance(data[0], dict) else []
                return {"columns": columns, "rows": len(data), "sample": data[:5]}
            return {"type": type(data).__name__, "columns": list(data) if isinstance(data, dict) else [],
                    "keys": list(data)[:50] if isinstance(data, dict) else [], "sample": data}
        raise ValueError("only CSV and JSON files are supported")

    @tool
    def validate_data(path: str, required_columns: list[str] | None = None) -> dict:
        """Validate a CSV/JSON file and optionally check required field names."""
        result = inspect_data.invoke({"path": path})
        columns = result.get("columns", [])
        missing = sorted(set(required_columns or []) - set(columns))
        return {"valid": not missing, "missing_columns": missing, "rows": result.get("rows")}

    return [query_database, inspect_data, validate_data]


def code_tools(workspace: str | Path | None = None, *, timeout: float = 10.0) -> list[Any]:
    """Create file tools and bounded script runner for trusted local code only."""
    root = Path(workspace or Path.cwd()).resolve()
    if timeout <= 0:
        raise ValueError("timeout must be positive")

    @tool
    def create_file(path: str, content: str) -> str:
        """Create a new text file within the worker workspace without overwriting."""
        target = _within(root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8", newline="") as stream:
            stream.write(content)
        return f"created {target.relative_to(root).as_posix()}"

    @tool
    def edit_file(path: str, old_text: str, new_text: str) -> str:
        """Replace one unique text fragment in a workspace file."""
        target = _within(root, path)
        if not target.is_file():
            raise ValueError("file does not exist")
        if not old_text:
            raise ValueError("old_text must not be empty")
        content = target.read_text(encoding="utf-8")
        count = content.count(old_text)
        if count != 1:
            raise ValueError(f"old_text must match exactly once; found {count}")
        target.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return f"updated {target.relative_to(root).as_posix()}"

    @tool
    def run_python(path: str) -> dict:
        """Run a trusted Python script with a timeout; this is not an OS security sandbox."""
        target = _within(root, path)
        if target.suffix != ".py" or not target.is_file():
            raise ValueError("path must name an existing .py file")
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH") if key in os.environ}
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        proc = subprocess.Popen([sys.executable, "-I", str(target)], cwd=root, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
            else:
                import signal
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                stdout, stderr = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
            return {"returncode": 124, "stdout": stdout[-MAX_OUTPUT:],
                    "stderr": (stderr + f"\ntimed out after {timeout}s")[-MAX_OUTPUT:]}
        return {"returncode": proc.returncode, "stdout": stdout[-MAX_OUTPUT:], "stderr": stderr[-MAX_OUTPUT:]}

    return [create_file, edit_file, run_python]


def evaluator_tools() -> list[Any]:
    @tool
    def score_result(accuracy: float, completeness: float, clarity: float, performance: float) -> dict:
        """Calculate a weighted quality score from four 0-to-100 dimensions."""
        values = {"accuracy": accuracy, "completeness": completeness, "clarity": clarity, "performance": performance}
        if any(not math.isfinite(v) or v < 0 or v > 100 for v in values.values()):
            raise ValueError("all scores must be finite numbers from 0 to 100")
        score = 0.3 * accuracy + 0.3 * completeness + 0.2 * clarity + 0.2 * performance
        return {"score": round(score, 2), "grade": _grade(score), "dimensions": values}

    @tool
    def validate_result(required_items: list[str], delivered_items: list[str]) -> dict:
        """Check which required items are present in a result."""
        delivered = set(delivered_items)
        missing = [item for item in required_items if item not in delivered]
        coverage = 1.0 if not required_items else (len(required_items) - len(missing)) / len(required_items)
        return {"valid": not missing, "missing": missing, "coverage": round(coverage, 3)}

    @tool
    def compare_results(expected: str, actual: str) -> dict:
        """Compare expected and actual text after trimming surrounding whitespace."""
        matches = expected.strip() == actual.strip()
        return {"matches": matches, "expected_length": len(expected), "actual_length": len(actual)}

    @tool
    def generate_report(title: str, findings: list[str], issues: list[str] | None = None) -> dict:
        """Format evaluation findings and issues as a Markdown report."""
        if not title.strip():
            raise ValueError("title must not be empty")
        lines = [f"# {title.strip()}", "", "## Findings"]
        lines.extend(f"- {item}" for item in findings)
        if issues:
            lines.extend(["", "## Issues", *(f"- {item}" for item in issues)])
        return {"title": title.strip(), "markdown": "\n".join(lines)}

    return [score_result, validate_result, compare_results, generate_report]


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"
