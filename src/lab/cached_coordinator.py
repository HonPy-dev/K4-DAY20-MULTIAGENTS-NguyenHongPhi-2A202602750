"""Bounded in-memory result cache for successful coordinator requests."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections import OrderedDict
from typing import Any

from .coordinator import Coordinator


class CachingCoordinator(Coordinator):
    """Coordinator wrapper that caches successful normalized requests in LRU order."""

    def __init__(self, workers, message_queue=None, *, max_cache_entries: int = 128):
        if max_cache_entries < 1:
            raise ValueError("max_cache_entries must be positive")
        super().__init__(workers, message_queue)
        self.max_cache_entries = max_cache_entries
        self._result_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._cache_lock = asyncio.Lock()

    @staticmethod
    def _cache_key(request: str | dict[str, Any]) -> str:
        canonical = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def process_request(self, user_input: str | dict[str, Any], timeout: float = 60) -> dict[str, Any]:
        key = self._cache_key(user_input)
        async with self._cache_lock:
            cached = self._result_cache.get(key)
            if cached is not None:
                self._result_cache.move_to_end(key)
                response = copy.deepcopy(cached)
                response["cache_hit"] = True
                return response

        response = await super().process_request(user_input, timeout=timeout)
        response["cache_hit"] = False
        if response.get("status") == "success":
            async with self._cache_lock:
                self._result_cache[key] = copy.deepcopy(response)
                self._result_cache.move_to_end(key)
                while len(self._result_cache) > self.max_cache_entries:
                    self._result_cache.popitem(last=False)
        return response
