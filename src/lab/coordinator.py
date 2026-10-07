"""Parse user requests and coordinate specialist workers over a message queue."""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .communication.message_queue import MessageQueue


class Coordinator:
    ROUTING = {
        "data_analysis": ("data_agent",),
        "code_generation": ("code_agent",),
        "evaluation": ("evaluator_agent",),
        "complex": ("data_agent", "code_agent"),
    }

    def __init__(self, workers, message_queue: MessageQueue | None = None):
        if isinstance(workers, dict):
            worker_map = dict(workers)
        else:
            worker_map = {}
            for worker in workers:
                name = getattr(worker, "name", None)
                if not name:
                    raise ValueError("each worker must have a name")
                if name in worker_map:
                    raise ValueError(f"duplicate worker name: {name}")
                worker_map[name] = worker
        if not worker_map:
            raise ValueError("at least one worker is required")
        self.name = "coordinator"
        self.workers = worker_map
        self.task_queue = message_queue or MessageQueue()
        self.message_queue = self.task_queue
        self.active_tasks: dict[str, dict[str, Any]] = {}
        self.logger = logging.getLogger("coordinator")
        self.task_queue.register_agent(self.name)
        for name in self.workers:
            self.task_queue.register_agent(name)

    def parse_request(self, user_input: str | dict[str, Any]) -> dict[str, Any]:
        """Normalize user text or structured input without making an API call."""
        if isinstance(user_input, dict):
            content = user_input.get("content", user_input.get("request", user_input.get("text", "")))
            if not isinstance(content, str) or not content.strip():
                raise ValueError("structured request must contain non-empty content")
            task_type = user_input.get("task_type") or self._infer_task_type(content)
            if task_type not in self.ROUTING:
                raise ValueError(f"unsupported task_type: {task_type}")
            parameters = user_input.get("parameters", {})
            if not isinstance(parameters, dict):
                raise ValueError("parameters must be a dictionary")
            return {
                "task_type": task_type,
                "content": content.strip(),
                "parameters": parameters.copy(),
                "priority": user_input.get("priority", "normal"),
                "worker": user_input.get("worker"),
            }
        if not isinstance(user_input, str) or not user_input.strip():
            raise ValueError("user_input must be a non-empty string or structured request")
        content = user_input.strip()
        return {"task_type": self._infer_task_type(content), "content": content,
                "parameters": {}, "priority": "normal", "worker": None}

    @staticmethod
    def _infer_task_type(content: str) -> str:
        text = content.casefold()
        data_terms = ("data", "csv", "sql", "database", "sales", "revenue", "analyze", "analysis", "query")
        code_terms = ("code", "script", "python", "create", "generate", "chart", "plot", "report", "build")
        eval_terms = ("evaluate", "evaluation", "score", "review", "quality", "validate")
        data = any(term in text for term in data_terms)
        code = any(term in text for term in code_terms)
        if data and code:
            return "complex"
        if any(term in text for term in eval_terms):
            return "evaluation"
        if code:
            return "code_generation"
        return "data_analysis"

    def route_task(self, task_type: str | dict[str, Any], content: str | None = None) -> list[dict[str, Any]]:
        """Map a normalized request to one or more worker task envelopes."""
        if isinstance(task_type, dict):
            request = task_type
            kind = request.get("task_type")
            content = request.get("content", content)
            explicit_worker = request.get("worker")
            parameters = request.get("parameters", {})
            priority = request.get("priority", "normal")
        else:
            kind = task_type
            explicit_worker = None
            parameters = {}
            priority = "normal"
        if not isinstance(content, str) or not content.strip():
            raise ValueError("task content must be a non-empty string")
        if explicit_worker:
            if explicit_worker not in self.workers:
                raise ValueError(f"Unknown worker: {explicit_worker}")
            names = (explicit_worker,)
        else:
            if kind not in self.ROUTING:
                raise ValueError(f"unsupported task_type: {kind}")
            names = tuple(name for name in self.ROUTING[kind] if name in self.workers)
            if not names:
                raise ValueError(f"No registered worker can handle task_type: {kind}")
        return [{"id": str(uuid.uuid4()), "worker": name, "content": content.strip(),
                 "parameters": parameters.copy() if isinstance(parameters, dict) else {},
                 "priority": priority, "task_type": kind} for name in names]

    async def execute_tasks(self, tasks: list[dict[str, Any]], timeout: float = 60) -> list[dict[str, Any]]:
        """Dispatch tasks concurrently and return one result per input in input order."""
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        prepared = []
        for index, task in enumerate(tasks):
            if not isinstance(task, dict):
                raise ValueError(f"task at index {index} must be a dictionary")
            task_id = str(task.get("id", index))
            worker_name = task.get("worker")
            correlation_id = str(uuid.uuid4())
            prepared.append((task, task_id, worker_name, correlation_id))
            self.active_tasks[correlation_id] = {"task_id": task_id, "worker": worker_name,
                                                 "status": "queued", "started_at": time.monotonic()}

        consumers = {name: asyncio.create_task(self._worker_loop(name)) for name in self.workers}
        for task, task_id, worker_name, correlation_id in prepared:
            if worker_name not in self.workers:
                self.active_tasks[correlation_id]["status"] = "error"
                continue
            await self.task_queue.send_message(
                self.name, worker_name,
                {"type": "task", "task_id": task_id, "content": task.get("content", ""),
                 "parameters": task.get("parameters", {})},
                correlation_id=correlation_id,
            )
            self.active_tasks[correlation_id]["status"] = "running"

        pending = {correlation_id: (task, task_id, worker) for task, task_id, worker, correlation_id in prepared
                   if worker in self.workers}
        results: dict[str, dict[str, Any]] = {}
        deadline = asyncio.get_running_loop().time() + timeout
        try:
            while pending:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    response = await self.task_queue.receive_message(self.name, timeout=remaining)
                except TimeoutError:
                    break
                correlation_id = response.get("correlation_id")
                if correlation_id in pending:
                    task, task_id, worker_name = pending.pop(correlation_id)
                    results[correlation_id] = response
                    state = self.active_tasks[correlation_id]
                    state.update(status=response.get("status", "error"), duration=round(time.monotonic() - state["started_at"], 3))

            output = []
            for task, task_id, worker_name, correlation_id in prepared:
                if worker_name not in self.workers:
                    output.append({"task_id": task_id, "status": "error", "worker": worker_name,
                                   "error": f"Unknown worker: {worker_name}"})
                elif correlation_id in results:
                    output.append(results[correlation_id])
                else:
                    self.active_tasks[correlation_id]["status"] = "timeout"
                    output.append({"task_id": task_id, "status": "timeout", "worker": worker_name,
                                   "error": f"No result within {timeout}s"})
            return output
        finally:
            for consumer in consumers.values():
                consumer.cancel()
            await asyncio.gather(*consumers.values(), return_exceptions=True)

    async def _worker_loop(self, worker_name: str) -> None:
        worker = self.workers[worker_name]
        queue = self.task_queue.queues[worker_name]
        while True:
            message = await queue.get()
            if message.get("type") == "stop":
                return
            if message.get("type") != "task":
                continue
            try:
                result = await worker.process_async(message.get("content", ""), message.get("parameters") or {})
                response = {"type": "result", "task_id": message.get("task_id"),
                            "status": result.get("status", "error"), "result": result.get("result"),
                            "error": result.get("error"), "metadata": result.get("metadata", {})}
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("Worker %s failed", worker_name)
                response = {"type": "result", "task_id": message.get("task_id"), "status": "error",
                            "result": None, "error": f"{type(exc).__name__}: {exc}",
                            "metadata": {"worker": worker_name}}
            await self.task_queue.send_message(worker_name, self.name, response,
                                               correlation_id=message.get("correlation_id"))

    def aggregate_results(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        successes = [r for r in results if r.get("status") == "success"]
        failures = [r for r in results if r.get("status") != "success"]
        parts = []
        for result in successes:
            value = result.get("result")
            if value is not None and str(value).strip():
                parts.append(str(value).strip())
        return {
            "status": "success" if not failures else ("partial" if successes else "error"),
            "results": results,
            "summary": "\n\n".join(parts),
            "errors": [{"task_id": r.get("task_id"), "worker": r.get("worker"), "error": r.get("error")}
                       for r in failures],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def process_request(self, user_input: str | dict[str, Any], timeout: float = 60) -> dict[str, Any]:
        request = self.parse_request(user_input)
        tasks = self.route_task(request)
        results = await self.execute_tasks(tasks, timeout=timeout)
        aggregated = self.aggregate_results(results)
        aggregated["request"] = request
        return aggregated

    def process_request_sync(self, user_input: str | dict[str, Any], timeout: float = 60) -> dict[str, Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.process_request(user_input, timeout=timeout))
        raise RuntimeError("process_request_sync cannot run inside an active event loop; await process_request instead")
