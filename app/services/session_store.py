"""Short-term conversation memory per kiosk session.

Keeps only the last few (customer, assistant) text pairs so follow-ups work
("170 cm" ... "70 kg"). No tool chatter, no cross-session memory, and sessions
expire after a short idle period. Swap for Redis behind the same protocol later.
"""

import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

Turn = tuple[str, str]  # (customer message, assistant answer)


class SessionStore(Protocol):
    async def get_history(self, key: str) -> list[Turn]: ...

    async def append_turn(self, key: str, user: str, assistant: str) -> None: ...


@dataclass(slots=True)
class _Session:
    turns: deque[Turn]
    updated_at: float


class InMemorySessionStore:
    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_turns: int,
        max_sessions: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_turns = max_turns
        self._max_sessions = max_sessions
        self._clock = clock
        self._sessions: OrderedDict[str, _Session] = OrderedDict()

    def _get_live(self, key: str) -> _Session | None:
        session = self._sessions.get(key)
        if session is None:
            return None
        if self._clock() - session.updated_at > self._ttl:
            del self._sessions[key]
            return None
        return session

    async def get_history(self, key: str) -> list[Turn]:
        session = self._get_live(key)
        return list(session.turns) if session else []

    async def append_turn(self, key: str, user: str, assistant: str) -> None:
        session = self._get_live(key)
        if session is None:
            session = _Session(turns=deque(maxlen=self._max_turns), updated_at=self._clock())
            self._sessions[key] = session
        session.turns.append((user, assistant))
        session.updated_at = self._clock()
        self._sessions.move_to_end(key)
        while len(self._sessions) > self._max_sessions:
            self._sessions.popitem(last=False)  # evict least recently used

    def __len__(self) -> int:
        return len(self._sessions)
