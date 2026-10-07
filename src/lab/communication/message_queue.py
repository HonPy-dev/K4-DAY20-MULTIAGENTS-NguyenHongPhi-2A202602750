"""In-memory asynchronous message queue for worker coordination."""
from __future__ import annotations

import asyncio
import copy
import uuid
from datetime import datetime, timezone
from typing import Any


class MessageQueue:
    def __init__(self):
        self.queues: dict[str, asyncio.Queue] = {}
        self.message_log: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    def register_agent(self, agent_name: str) -> None:
        if not agent_name:
            raise ValueError("agent_name must not be empty")
        self.queues.setdefault(agent_name, asyncio.Queue())

    async def send_message(
        self,
        from_agent: str,
        to_agent: str,
        message: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> str:
        if to_agent not in self.queues:
            raise ValueError(f"Agent {to_agent} not registered")
        if not isinstance(message, dict):
            raise TypeError("message must be a dictionary")
        payload = copy.deepcopy(message)
        message_id = str(uuid.uuid4())
        payload.update({
            "from": from_agent,
            "to": to_agent,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "id": message_id,
        })
        if correlation_id is not None:
            payload["correlation_id"] = correlation_id
        async with self._lock:
            self.message_log.append(copy.deepcopy(payload))
        await self.queues[to_agent].put(payload)
        return message_id

    async def receive_message(
        self,
        agent_name: str,
        timeout: float = 30,
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        if agent_name not in self.queues:
            raise ValueError(f"Agent {agent_name} not registered")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        queue = self.queues[agent_name]
        deadline = asyncio.get_running_loop().time() + timeout
        deferred = []
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                message = await asyncio.wait_for(queue.get(), timeout=remaining)
                if correlation_id is None or message.get("correlation_id") == correlation_id:
                    return message
                deferred.append(message)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"No message for {agent_name} within {timeout}s") from exc
        finally:
            for message in deferred:
                queue.put_nowait(message)

    def get_message_log(self, agent_name: str | None = None) -> list[dict[str, Any]]:
        if agent_name is None:
            selected = self.message_log
        else:
            selected = [m for m in self.message_log if m["from"] == agent_name or m["to"] == agent_name]
        return copy.deepcopy(selected)
