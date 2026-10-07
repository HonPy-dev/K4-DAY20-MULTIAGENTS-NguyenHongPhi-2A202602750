import asyncio

from lab.cached_coordinator import CachingCoordinator


class CountingWorker:
    def __init__(self, name="data_agent"):
        self.name = name
        self.calls = 0

    async def process_async(self, content, parameters=None):
        self.calls += 1
        return {"status": "success", "result": f"answer:{content}"}


class FlakyWorker:
    def __init__(self):
        self.name = "data_agent"
        self.calls = 0

    async def process_async(self, content, parameters=None):
        self.calls += 1
        if self.calls == 1:
            return {"status": "error", "error": "temporary"}
        return {"status": "success", "result": "recovered"}


def test_caches_successful_requests_and_returns_independent_copy():
    async def scenario():
        worker = CountingWorker()
        coordinator = CachingCoordinator([worker])
        request = {"task_type": "data_analysis", "content": "same"}
        first = await coordinator.process_request(request)
        first["results"].clear()
        second = await coordinator.process_request(request)
        assert worker.calls == 1
        assert first["cache_hit"] is False and second["cache_hit"] is True
        assert second["results"]

    asyncio.run(scenario())


def test_cache_separates_requests_and_does_not_store_errors():
    async def scenario():
        worker = FlakyWorker()
        coordinator = CachingCoordinator([worker])
        request = {"task_type": "data_analysis", "content": "retry me"}
        first = await coordinator.process_request(request)
        second = await coordinator.process_request(request)
        third = await coordinator.process_request({"task_type": "data_analysis", "content": "different"})
        assert first["status"] == "error" and first["cache_hit"] is False
        assert second["status"] == "success" and second["cache_hit"] is False
        assert third["cache_hit"] is False
        assert worker.calls == 3

    asyncio.run(scenario())


def test_cache_is_bounded_lru():
    async def scenario():
        worker = CountingWorker()
        coordinator = CachingCoordinator([worker], max_cache_entries=1)
        a = {"task_type": "data_analysis", "content": "a"}
        b = {"task_type": "data_analysis", "content": "b"}
        await coordinator.process_request(a)
        await coordinator.process_request(b)
        await coordinator.process_request(a)
        assert worker.calls == 3
        assert len(coordinator._result_cache) == 1

    asyncio.run(scenario())
