from app.services.session_store import InMemorySessionStore


async def test_history_keeps_last_turns() -> None:
    store = InMemorySessionStore(ttl_seconds=600, max_turns=2)
    for i in range(3):
        await store.append_turn("k", f"q{i}", f"a{i}")
    assert await store.get_history("k") == [("q1", "a1"), ("q2", "a2")]


async def test_sessions_are_isolated() -> None:
    store = InMemorySessionStore(ttl_seconds=600, max_turns=6)
    await store.append_turn("KIOSK-1:S1", "170 cm", "weight?")
    assert await store.get_history("KIOSK-2:S1") == []
    assert await store.get_history("KIOSK-1:S2") == []


async def test_session_expires_after_idle() -> None:
    now = [1000.0]
    store = InMemorySessionStore(ttl_seconds=60, max_turns=6, clock=lambda: now[0])
    await store.append_turn("k", "q", "a")
    now[0] += 59
    assert await store.get_history("k") == [("q", "a")]
    now[0] += 61
    assert await store.get_history("k") == []
    assert len(store) == 0


async def test_least_recently_used_session_evicted() -> None:
    store = InMemorySessionStore(ttl_seconds=600, max_turns=6, max_sessions=2)
    await store.append_turn("a", "q", "a")
    await store.append_turn("b", "q", "a")
    await store.append_turn("a", "q2", "a2")  # "a" is now most recent
    await store.append_turn("c", "q", "a")
    assert await store.get_history("b") == []
    assert len(await store.get_history("a")) == 2
