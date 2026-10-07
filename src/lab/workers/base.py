"""Shared implementation for specialist workers."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Sequence


class BaseWorker:
    """A tool-using worker with bounded execution and structured errors."""

    def __init__(self, name: str, model: Any, tools: Sequence[Any], *, max_tool_rounds: int = 8):
        if max_tool_rounds < 1:
            raise ValueError("max_tool_rounds must be positive")
        self.name = name
        self.model = model
        self.tools = {tool.name: tool for tool in tools}
        self.system_prompt = "You are a helpful specialist worker."
        self.logger = logging.getLogger(name)
        self.max_tool_rounds = max_tool_rounds
        self._executed_tools: list[str] = []

    def process(self, task_content: str, parameters: dict | None = None) -> dict:
        self._executed_tools = []
        try:
            prompt = self._build_prompt(task_content, parameters)
            model = self.model.bind_tools(list(self.tools.values())) if self.tools else self.model
            messages = [{"role": "user", "content": prompt}]
            for _ in range(self.max_tool_rounds + 1):
                response = model.invoke(messages)
                calls = getattr(response, "tool_calls", None) or []
                if not calls:
                    return {
                        "status": "success",
                        "result": response.content,
                        "metadata": {"worker": self.name, "tools_used": list(self._executed_tools)},
                    }
                messages.append(response)
                for call in calls:
                    tool_name = call.get("name")
                    tool_input = call.get("args", call.get("input", {}))
                    result = self._execute_tool(tool_name, tool_input)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", tool_name),
                        "name": tool_name,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    })
            raise RuntimeError(f"tool-call limit ({self.max_tool_rounds}) exceeded")
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Worker processing failed")
            return {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "result": None,
                "metadata": {"worker": self.name, "tools_used": list(self._executed_tools)},
            }

    async def process_async(self, task_content: str, parameters: dict | None = None) -> dict:
        return await asyncio.to_thread(self.process, task_content, parameters)

    def _build_prompt(self, task_content: str, parameters: dict | None = None) -> str:
        return (
            f"{self.system_prompt}\n\nTask: {task_content}\n"
            f"Parameters: {json.dumps(parameters or {}, ensure_ascii=False, default=str)}\n"
            f"Available tools: {', '.join(self.tools)}"
        )

    def _execute_tool(self, tool_name: str, tool_input: Any) -> Any:
        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")
        tool = self.tools[tool_name]
        try:
            result = tool.invoke(tool_input)
            self._executed_tools.append(tool_name)
            return result
        except Exception:
            self.logger.exception("Tool %s failed", tool_name)
            raise
