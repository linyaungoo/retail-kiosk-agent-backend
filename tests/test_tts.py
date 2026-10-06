"""TTS services: streaming helper, mock and OpenAI providers, per-language routing."""

import asyncio
import io
import json
import wave
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx2
import pytest
from openai import AsyncOpenAI

from app.config import Settings
from app.errors import ErrorCode
from app.main import create_tts
from app.models.kiosk import Language
from app.services.openai_tts_service import DEFAULT_INSTRUCTIONS, OpenAITextToSpeech
from app.services.tts_service import (
    CachingTTS,
    LanguageRoutedTTS,
    MockTextToSpeech,
    TTSError,
    TTSStream,
    TTSTimeoutError,
    start_stream,
    synthesize,
)

# --- start_stream -------------------------------------------------------------


async def _gen(*chunks: bytes, delay: float = 0) -> AsyncIterator[bytes]:
    for chunk in chunks:
        if delay:
            await asyncio.sleep(delay)
        yield chunk


async def test_stream_replays_first_chunk_and_skips_empty() -> None:
    stream = await start_stream(
        _gen(b"", b"ab", b"", b"cd"),
        provider="p",
        voice="v",
        audio_format="mp3",
        first_byte_timeout=1,
    )
    assert b"".join([c async for c in stream.chunks]) == b"abcd"
    assert stream.first_byte_ms >= 0 and stream.media_type == "audio/mpeg"


async def test_first_byte_timeout() -> None:
    with pytest.raises(TTSTimeoutError) as err:
        await start_stream(
            _gen(b"x", delay=1),
            provider="p",
            voice="v",
            audio_format="mp3",
            first_byte_timeout=0.05,
        )
    assert err.value.code == ErrorCode.TTS_TIMEOUT


async def test_no_audio_is_an_error() -> None:
    with pytest.raises(TTSError):
        await start_stream(
            _gen(), provider="p", voice="v", audio_format="mp3", first_byte_timeout=1
        )


# --- mock provider and router ----------------------------------------------------


async def test_mock_returns_valid_wav() -> None:
    result = await synthesize(MockTextToSpeech(), "hello there", "en-US")
    with wave.open(io.BytesIO(result.audio)) as wav:
        assert wav.getframerate() == 16_000 and wav.getnframes() > 0
    assert result.media_type == "audio/wav" and result.provider == "mock"


async def test_router_uses_provider_per_language() -> None:
    class Named(MockTextToSpeech):
        def __init__(self, name: str) -> None:
            self.name = name

    router = LanguageRoutedTTS({"my-MM": Named("burmese-vendor"), "en-US": Named("english")})
    assert router.provider_for("my-MM").name == "burmese-vendor"
    assert router.provider_for("en-US").name == "english"


# --- OpenAI provider (real SDK, simulated HTTP) ------------------------------------


def _openai_tts(
    handler: Callable[[httpx2.Request], httpx2.Response],
    model: str = "gpt-4o-mini-tts",
    *,
    first_byte_timeout: float = 5,
    attempts: int = 1,
) -> OpenAITextToSpeech:
    client = AsyncOpenAI(
        api_key="sk-test",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return OpenAITextToSpeech(
        client,
        model=model,
        voice="coral",
        audio_format="mp3",
        speed=1.0,
        request_timeout=5,
        first_byte_timeout=first_byte_timeout,
        attempts=attempts,
    )


class StalledBody(httpx2.AsyncByteStream):
    """Headers arrive, then the body hangs (what was observed in production-like runs)."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        await asyncio.sleep(10)
        yield b"late"


class BrokenBody(httpx2.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        raise httpx2.ReadError("connection reset")
        yield b""  # pragma: no cover


def _sequence(*responses: Callable[[], httpx2.Response]) -> tuple[Callable[..., Any], list[int]]:
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return responses[min(len(calls), len(responses)) - 1]()

    return handler, calls


async def test_stalled_request_is_retried() -> None:
    handler, calls = _sequence(
        lambda: httpx2.Response(200, stream=StalledBody()),
        lambda: httpx2.Response(200, content=b"ID3audio"),
    )
    tts = _openai_tts(handler, first_byte_timeout=0.2, attempts=2)
    result = await synthesize(tts, "Hello", "en-US")
    assert result.audio == b"ID3audio" and len(calls) == 2


async def test_transport_error_before_audio_is_retried() -> None:
    handler, calls = _sequence(
        lambda: httpx2.Response(200, stream=BrokenBody()),
        lambda: httpx2.Response(200, content=b"ID3audio"),
    )
    result = await synthesize(_openai_tts(handler, attempts=2), "Hello", "en-US")
    assert result.audio == b"ID3audio" and len(calls) == 2


async def test_gives_up_after_all_attempts_stall() -> None:
    handler, calls = _sequence(lambda: httpx2.Response(200, stream=StalledBody()))
    tts = _openai_tts(handler, first_byte_timeout=0.1, attempts=2)
    with pytest.raises(TTSTimeoutError):
        await tts.stream("Hello", "en-US")
    assert len(calls) == 2


async def test_transport_error_without_retry_is_tts_failed() -> None:
    handler, _ = _sequence(lambda: httpx2.Response(200, stream=BrokenBody()))
    with pytest.raises(TTSError) as err:
        await _openai_tts(handler).stream("Hello", "en-US")
    assert err.value.code == ErrorCode.TTS_FAILED


async def test_openai_request_and_streamed_audio() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, content=b"ID3" + b"\xff" * 10_000)

    result = await synthesize(_openai_tts(handler), "ဆိုင်က ည ၉ နာရီမှာ ပိတ်ပါတယ်။", "my-MM")

    assert result.audio.startswith(b"ID3") and len(result.audio) == 10_003
    assert result.provider == "openai" and result.voice == "coral"
    body = sent[0]
    assert body["model"] == "gpt-4o-mini-tts" and body["voice"] == "coral"
    assert body["input"] == "ဆိုင်က ည ၉ နာရီမှာ ပိတ်ပါတယ်။"
    assert body["response_format"] == "mp3"
    assert body["instructions"] == DEFAULT_INSTRUCTIONS["my-MM"]


async def test_tts1_gets_no_instructions() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, content=b"audio")

    await synthesize(_openai_tts(handler, model="tts-1"), "Hello", "en-US")
    assert "instructions" not in sent[0]


async def test_openai_error_becomes_tts_failed() -> None:
    tts = _openai_tts(lambda _: httpx2.Response(500, json={"error": {"message": "boom"}}))
    with pytest.raises(TTSError) as err:
        await tts.stream("Hello", "en-US")
    assert err.value.code == ErrorCode.TTS_FAILED and err.value.status_code == 502


# --- startup wiring -----------------------------------------------------------------


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_create_tts_per_language_override() -> None:
    client = AsyncOpenAI(api_key="sk-test")
    tts = create_tts(
        _settings(
            openai_tts_model="gpt-4o-mini-tts", tts_provider_my_mm="mock", tts_cache_entries=0
        ),
        client,
    )
    assert isinstance(tts, LanguageRoutedTTS)
    assert tts.provider_for("my-MM").name == "mock"
    assert tts.provider_for("en-US").name == "openai"


def test_create_tts_disabled_without_openai_config() -> None:
    assert create_tts(_settings(openai_tts_model=""), AsyncOpenAI(api_key="sk-test")) is None
    assert create_tts(_settings(openai_tts_model="gpt-4o-mini-tts"), None) is None
    assert create_tts(_settings(tts_provider="mock"), None) is not None


def test_create_tts_wraps_cache_by_default() -> None:
    assert isinstance(create_tts(_settings(tts_provider="mock"), None), CachingTTS)


# --- clip cache -------------------------------------------------------------------------


class CountingTTS(MockTextToSpeech):
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, text: str, language: Language) -> TTSStream:
        self.calls += 1
        return await super().stream(text, language)


async def test_cache_serves_repeat_text_instantly() -> None:
    inner = CountingTTS()
    cache = CachingTTS(inner, max_entries=10)
    first = await synthesize(cache, "We close at 9 PM.", "en-US")
    second = await synthesize(cache, "We close at 9 PM.", "en-US")
    assert inner.calls == 1
    assert second.audio == first.audio and second.first_byte_ms == 0
    assert second.provider == "mock+cache"
    await synthesize(cache, "We close at 9 PM.", "my-MM")  # language is part of the key
    assert inner.calls == 2


async def test_cache_ignores_incomplete_streams() -> None:
    inner = CountingTTS()
    cache = CachingTTS(inner, max_entries=10)
    stream = await cache.stream("Hello", "en-US")
    await anext(stream.chunks)  # client disconnects after the first chunk
    await stream.chunks.aclose()  # type: ignore[attr-defined]
    await synthesize(cache, "Hello", "en-US")
    assert inner.calls == 2 and cache.size == 1


async def test_cache_evicts_least_recently_used() -> None:
    inner = CountingTTS()
    cache = CachingTTS(inner, max_entries=2)
    for text in ("a", "b", "a", "c"):  # "a" refreshed, so "b" is evicted
        await synthesize(cache, text, "en-US")
    calls = inner.calls
    await synthesize(cache, "a", "en-US")
    assert inner.calls == calls
    await synthesize(cache, "b", "en-US")
    assert inner.calls == calls + 1


async def test_cache_skips_long_text() -> None:
    inner = CountingTTS()
    cache = CachingTTS(inner, max_entries=10, max_text_chars=10)
    for _ in range(2):
        await synthesize(cache, "x" * 50, "en-US")
    assert inner.calls == 2 and cache.size == 0


def test_empty_cache_is_still_truthy() -> None:
    # Regression: `if app.state.tts:` must not treat an empty cache as "TTS disabled".
    assert bool(CachingTTS(MockTextToSpeech(), max_entries=10))
