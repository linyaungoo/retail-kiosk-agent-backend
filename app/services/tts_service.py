"""Provider-neutral text-to-speech.

The rest of the app depends only on TextToSpeechService. Providers (OpenAI now;
Azure, Google, ElevenLabs, ... later) implement `stream()`; the per-language router
picks one per language. TTS runs after the agent has produced its final text; it is
not an agent tool.
"""

import asyncio
import io
import math
import wave
from collections import OrderedDict
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from app.errors import AppError, ErrorCode
from app.models.kiosk import Language
from app.utils.timing import Timer

MEDIA_TYPES: dict[str, str] = {
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",  # Opus in an Ogg container
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "pcm": "audio/L16;rate=24000;channels=1",  # raw 16-bit little-endian mono
}


class TTSError(AppError):
    def __init__(self, message: str = "Unable to generate speech.") -> None:
        super().__init__(ErrorCode.TTS_FAILED, message, 502)


class TTSTimeoutError(AppError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.TTS_TIMEOUT, "Speech generation took too long.", 504)


@dataclass(slots=True)
class TTSStream:
    """Audio that has started arriving. `first_byte_ms` is already known; the
    remaining audio is read from `chunks` (e.g. straight into an HTTP response)."""

    provider: str
    voice: str
    audio_format: str
    first_byte_ms: int
    chunks: AsyncIterator[bytes]
    started: Timer = field(repr=False, default_factory=Timer.started)

    @property
    def media_type(self) -> str:
        return MEDIA_TYPES[self.audio_format]


@dataclass(frozen=True, slots=True)
class TTSResult:
    audio: bytes
    provider: str
    voice: str
    audio_format: str
    first_byte_ms: int
    total_ms: int

    @property
    def media_type(self) -> str:
        return MEDIA_TYPES[self.audio_format]


class TextToSpeechService(Protocol):
    name: str

    async def stream(self, text: str, language: Language) -> TTSStream:
        """Start synthesis; return once the first audio bytes have arrived.

        Raises TTSError / TTSTimeoutError before any audio is returned, so callers
        can still send a clean JSON error.
        """
        ...


async def synthesize(service: TextToSpeechService, text: str, language: Language) -> TTSResult:
    """Whole clip in memory (for callers that can't stream)."""
    stream = await service.stream(text, language)
    audio = b"".join([chunk async for chunk in stream.chunks])
    return TTSResult(
        audio=audio,
        provider=stream.provider,
        voice=stream.voice,
        audio_format=stream.audio_format,
        first_byte_ms=stream.first_byte_ms,
        total_ms=stream.started.ms,
    )


async def start_stream(
    chunks: AsyncIterator[bytes],
    *,
    provider: str,
    voice: str,
    audio_format: str,
    first_byte_timeout: float,
) -> TTSStream:
    """Wait for the first non-empty chunk (measuring first-byte latency), then hand
    back a stream that replays it followed by the rest."""
    timer = Timer.started()
    try:
        async with asyncio.timeout(first_byte_timeout):
            first = b""
            while not first:
                first = await anext(chunks)
    except TimeoutError:
        await _close(chunks)
        raise TTSTimeoutError() from None
    except StopAsyncIteration:
        raise TTSError("The speech provider returned no audio.") from None
    first_byte_ms = timer.ms

    async def replay() -> AsyncIterator[bytes]:
        try:
            yield first
            async for chunk in chunks:
                if chunk:
                    yield chunk
        finally:
            await _close(chunks)

    return TTSStream(
        provider=provider,
        voice=voice,
        audio_format=audio_format,
        first_byte_ms=first_byte_ms,
        chunks=replay(),
        started=timer,
    )


async def _close(chunks: AsyncIterator[bytes]) -> None:
    aclose = getattr(chunks, "aclose", None)
    if aclose is not None:
        await aclose()


class LanguageRoutedTTS:
    """Picks the provider configured for each language."""

    name = "router"

    def __init__(self, providers: Mapping[Language, TextToSpeechService]) -> None:
        self._providers = dict(providers)

    def provider_for(self, language: Language) -> TextToSpeechService:
        return self._providers[language]

    async def stream(self, text: str, language: Language) -> TTSStream:
        return await self._providers[language].stream(text, language)


@dataclass(frozen=True, slots=True)
class _CachedAudio:
    audio: bytes
    provider: str
    voice: str
    audio_format: str


class CachingTTS:
    """In-memory LRU of synthesized clips, keyed by exact text and language.

    Template answers repeat word for word ("We close at 9 PM."), so popular answers
    are served instantly instead of re-synthesized (~1-2 s). A clip is stored only
    after it streamed completely. Changed business data produces different text,
    so nothing needs invalidating.
    """

    name = "cache"
    CHUNK = 16_384

    def __init__(
        self, inner: TextToSpeechService, *, max_entries: int, max_text_chars: int = 300
    ) -> None:
        self._inner = inner
        self._max_entries = max_entries
        self._max_text_chars = max_text_chars
        self._clips: OrderedDict[tuple[Language, str], _CachedAudio] = OrderedDict()

    @property
    def size(self) -> int:
        """Number of cached clips. (Not __len__: an empty cache must not be falsy.)"""
        return len(self._clips)

    async def stream(self, text: str, language: Language) -> TTSStream:
        key = (language, text)
        cached = self._clips.get(key)
        if cached is not None:
            self._clips.move_to_end(key)
            return self._replay(cached)

        stream = await self._inner.stream(text, language)
        if len(text) > self._max_text_chars:
            return stream
        return TTSStream(
            provider=stream.provider,
            voice=stream.voice,
            audio_format=stream.audio_format,
            first_byte_ms=stream.first_byte_ms,
            chunks=self._record(key, stream),
            started=stream.started,
        )

    async def _record(self, key: tuple[Language, str], stream: TTSStream) -> AsyncIterator[bytes]:
        parts: list[bytes] = []
        async for chunk in stream.chunks:
            parts.append(chunk)
            yield chunk
        # Only reached if the whole clip arrived (not on client disconnect or error).
        self._clips[key] = _CachedAudio(
            b"".join(parts), stream.provider, stream.voice, stream.audio_format
        )
        while len(self._clips) > self._max_entries:
            self._clips.popitem(last=False)

    def _replay(self, cached: _CachedAudio) -> TTSStream:
        async def chunks() -> AsyncIterator[bytes]:
            for start in range(0, len(cached.audio), self.CHUNK):
                yield cached.audio[start : start + self.CHUNK]

        return TTSStream(
            provider=f"{cached.provider}+cache",
            voice=cached.voice,
            audio_format=cached.audio_format,
            first_byte_ms=0,
            chunks=chunks(),
        )


class MockTextToSpeech:
    """Development provider: silent WAV whose length roughly matches the text."""

    name = "mock"
    SAMPLE_RATE = 16_000

    async def stream(self, text: str, language: Language) -> TTSStream:
        seconds = min(max(len(text) * 0.06, 0.5), 10.0)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.SAMPLE_RATE)
            wav.writeframes(b"\x00\x00" * math.ceil(seconds * self.SAMPLE_RATE))
        audio = buffer.getvalue()

        async def chunks() -> AsyncIterator[bytes]:
            for start in range(0, len(audio), 16_384):
                yield audio[start : start + 16_384]

        return await start_stream(
            chunks(), provider=self.name, voice="silence", audio_format="wav", first_byte_timeout=5
        )
