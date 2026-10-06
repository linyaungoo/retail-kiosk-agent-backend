"""BusinessDataCache: TTL, stale-while-revalidate, failure handling."""

import asyncio

import pytest

from app.data.business_data import BusinessData
from app.services.cache_service import BusinessDataCache, DataUnavailableError


def _data(version: int) -> BusinessData:
    store = {"store_id": f"STORE-{version}", "organization_id": "ORG-001", "name": "S"}
    return BusinessData.from_rows(products=[], stores=[store], faqs=[])


def _version(data: BusinessData) -> int:
    return int(next(iter(data.stores)).split("-")[1])


class FakeSource:
    name = "fake"

    def __init__(self) -> None:
        self.loads = 0
        self.fail = False
        self.gate: asyncio.Event | None = None

    async def load(self) -> BusinessData:
        self.loads += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise ConnectionError("sheets down")
        return _data(self.loads)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def source() -> FakeSource:
    return FakeSource()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def cache(source: FakeSource, clock: Clock) -> BusinessDataCache:
    return BusinessDataCache(source, ttl_seconds=300, retry_seconds=15, clock=clock)


async def _settle(cache: BusinessDataCache) -> None:
    task = cache._refresh_task
    if task is not None:
        await task


async def test_fresh_data_served_from_memory(
    cache: BusinessDataCache, source: FakeSource, clock: Clock
) -> None:
    assert _version(await cache.get()) == 1  # miss: loads
    clock.now += 299
    for _ in range(100):
        assert _version(await cache.get()) == 1
    assert source.loads == 1


async def test_stale_data_served_while_refreshing(
    cache: BusinessDataCache, source: FakeSource, clock: Clock
) -> None:
    await cache.get()
    clock.now += 300
    source.gate = asyncio.Event()

    # Customer requests are not blocked by the slow refresh.
    assert _version(await asyncio.wait_for(cache.get(), timeout=0.5)) == 1
    assert _version(await asyncio.wait_for(cache.get(), timeout=0.5)) == 1
    assert cache.status()["refreshing"] is True

    source.gate.set()
    await _settle(cache)
    assert _version(await cache.get()) == 2
    assert source.loads == 2  # exactly one background refresh


async def test_failed_refresh_keeps_last_good_data(
    cache: BusinessDataCache, source: FakeSource, clock: Clock
) -> None:
    await cache.get()
    clock.now += 301
    source.fail = True
    await cache.get()
    await _settle(cache)

    assert _version(await cache.get()) == 1
    status = cache.status()
    assert status["loaded"] and status["stale"]
    assert status["last_error"] == "ConnectionError: sheets down"


async def test_retries_are_throttled_after_failure(
    cache: BusinessDataCache, source: FakeSource, clock: Clock
) -> None:
    await cache.get()
    clock.now += 301
    source.fail = True
    await cache.get()
    await _settle(cache)
    assert source.loads == 2

    clock.now += 10  # inside retry window: no new attempt
    await cache.get()
    await _settle(cache)
    assert source.loads == 2

    clock.now += 6  # retry window over
    source.fail = False
    await cache.get()
    await _settle(cache)
    assert source.loads == 3 and _version(await cache.get()) == 3


async def test_never_loaded_and_failing_raises(
    cache: BusinessDataCache, source: FakeSource, clock: Clock
) -> None:
    source.fail = True
    with pytest.raises(DataUnavailableError):
        await cache.get()
    with pytest.raises(DataUnavailableError):
        await cache.get()
    assert source.loads == 1  # second call inside retry window doesn't hit the source

    clock.now += 16
    source.fail = False
    assert _version(await cache.get()) == 2


async def test_concurrent_first_requests_load_once(
    cache: BusinessDataCache, source: FakeSource
) -> None:
    results = await asyncio.gather(*(cache.get() for _ in range(20)))
    assert source.loads == 1
    assert {_version(r) for r in results} == {1}


async def test_start_does_not_raise(cache: BusinessDataCache, source: FakeSource) -> None:
    source.fail = True
    await cache.start(attempts=3, backoff_seconds=0)
    assert cache.status()["loaded"] is False
    assert source.loads == 3
    source.fail = False
    assert _version(await cache.get()) == 4  # first request retries immediately


async def test_start_rides_out_a_transient_failure(source: FakeSource, clock: Clock) -> None:
    class FlakySource(FakeSource):
        async def load(self) -> BusinessData:
            self.fail = self.loads == 0  # only the first attempt fails
            return await super().load()

    flaky = FlakySource()
    cache = BusinessDataCache(flaky, ttl_seconds=300, clock=clock)
    await cache.start(attempts=3, backoff_seconds=0)
    assert cache.status()["loaded"] is True and flaky.loads == 2


async def test_manual_refresh_reloads_even_when_fresh(
    cache: BusinessDataCache, source: FakeSource
) -> None:
    await cache.get()
    await cache.refresh()
    assert _version(await cache.get()) == 2
    assert cache.status()["counts"] == {"products": 0, "stores": 1, "faqs": 0}
