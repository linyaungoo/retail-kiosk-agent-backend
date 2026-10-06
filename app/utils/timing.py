"""Lightweight latency measurement helpers."""

import time
from types import TracebackType


class Timer:
    """Measure elapsed wall time in milliseconds.

    Usage:
        with Timer() as t:
            ...
        t.ms  # elapsed milliseconds (int)
    """

    __slots__ = ("_start", "_end")

    def __init__(self) -> None:
        self._start = 0.0
        self._end: float | None = None

    @classmethod
    def started(cls) -> "Timer":
        """A running timer, for spans that don't fit a `with` block (e.g. streams)."""
        return cls().__enter__()

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        self._end = None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._end = time.perf_counter()

    @property
    def ms(self) -> int:
        end = self._end if self._end is not None else time.perf_counter()
        return round((end - self._start) * 1000)
