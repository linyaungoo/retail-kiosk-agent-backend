"""In-memory business data cache with stale-while-revalidate refresh.

Google Sheets -> refresh -> backend memory -> agent tools.

- Fresh (age < TTL): served from memory.
- Stale: still served from memory immediately; one background refresh starts.
- Refresh fails: the last good data keeps being served; retries are throttled.
- Never loaded: requests wait for one load; if that fails they get DATA_UNAVAILABLE.

The repository only sees `get()`, so Redis (or a DB) can replace this class later
without touching the tools.
"""

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.data.business_data import BusinessData
from app.data.sources import BusinessDataSource
from app.errors import AppError, ErrorCode
from app.utils.logging import get_logger
from app.utils.timing import Timer

logger = get_logger(__name__)


class DataUnavailableError(AppError):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.DATA_UNAVAILABLE, "Store information is temporarily unavailable.", 503
        )


class BusinessDataCache:
    def __init__(
        self,
        source: BusinessDataSource,
        *,
        ttl_seconds: float,
        retry_seconds: float = 15,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source = source
        self._ttl = ttl_seconds
        self._retry = retry_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._snapshot: BusinessData | None = None
        self._loaded_at: float | None = None
        self._loaded_at_utc: datetime | None = None
        self._last_refresh_ms: int | None = None
        self._last_failure_at: float | None = None
        self._last_error: str | None = None
        self._refresh_task: asyncio.Task[None] | None = None

    @property
    def source_name(self) -> str:
        return self._source.name

    def _age(self) -> float | None:
        return None if self._loaded_at is None else self._clock() - self._loaded_at

    def _is_stale(self) -> bool:
        age = self._age()
        return age is None or age >= self._ttl

    def _recently_failed(self) -> bool:
        return (
            self._last_failure_at is not None
            and self._clock() - self._last_failure_at < self._retry
        )

    async def get(self) -> BusinessData:
        snapshot = self._snapshot
        if snapshot is None:
            logger.info("cache_miss", extra={"source": self.source_name})
            return await self._load_now()
        if self._is_stale():
            self._schedule_refresh()
        else:
            logger.debug("cache_hit", extra={"source": self.source_name})
        return snapshot

    async def start(self, *, attempts: int = 3, backoff_seconds: float = 1.0) -> None:
        """Initial load at startup, retried briefly to ride out network blips. A final
        failure is logged, not raised: the app still starts and retries on demand."""
        for attempt in range(1, attempts + 1):
            try:
                await self.refresh()
                return
            except DataUnavailableError:
                if attempt < attempts:
                    await asyncio.sleep(backoff_seconds * attempt)
        self._last_failure_at = None  # let the first customer request try again at once
        logger.error(
            "cache_initial_load_failed", extra={"source": self.source_name, "attempts": attempts}
        )

    async def refresh(self) -> BusinessData:
        """Reload from the source now (startup, admin endpoint)."""
        async with self._lock:
            return await self._refresh_locked()

    async def close(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

    async def _load_now(self) -> BusinessData:
        async with self._lock:
            if self._snapshot is not None:  # loaded while we waited for the lock
                return self._snapshot
            if self._recently_failed():
                raise DataUnavailableError()
            return await self._refresh_locked()

    def _schedule_refresh(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            return
        if self._recently_failed():
            return
        logger.info("cache_stale", extra={"source": self.source_name, "age_s": self._age()})
        self._refresh_task = asyncio.create_task(self._background_refresh())

    async def _background_refresh(self) -> None:
        async with self._lock:
            if not self._is_stale():
                return
            try:
                await self._refresh_locked()
            except DataUnavailableError:
                pass  # logged; keep serving the last good snapshot

    async def _refresh_locked(self) -> BusinessData:
        timer = Timer()
        try:
            with timer:
                data = await self._source.load()
        except Exception as exc:
            self._last_failure_at = self._clock()
            self._last_error = f"{type(exc).__name__}: {exc}"[:300]
            logger.error(
                "cache_refresh_failed",
                extra={
                    "source": self.source_name,
                    "refresh_ms": timer.ms,
                    "error": self._last_error,
                    "serving_stale": self._snapshot is not None,
                },
            )
            raise DataUnavailableError() from exc

        self._snapshot = data
        self._loaded_at = self._clock()
        self._loaded_at_utc = datetime.now(UTC)
        self._last_refresh_ms = timer.ms
        self._last_failure_at = None
        self._last_error = None
        logger.info(
            "cache_refreshed",
            extra={"source": self.source_name, "refresh_ms": timer.ms, **data.counts()},
        )
        return data

    def status(self) -> dict[str, Any]:
        age = self._age()
        return {
            "source": self.source_name,
            "loaded": self._snapshot is not None,
            "loaded_at": self._loaded_at_utc.isoformat() if self._loaded_at_utc else None,
            "age_seconds": round(age, 1) if age is not None else None,
            "ttl_seconds": self._ttl,
            "stale": self._is_stale(),
            "counts": self._snapshot.counts() if self._snapshot else None,
            "last_refresh_ms": self._last_refresh_ms,
            "last_error": self._last_error,
            "refreshing": bool(self._refresh_task and not self._refresh_task.done()),
        }
