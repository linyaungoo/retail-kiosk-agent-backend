"""OpenAI text-to-speech provider (streaming)."""

from collections.abc import AsyncIterator

import httpx2
import openai
from openai import AsyncOpenAI

from app.models.kiosk import Language
from app.services.tts_service import TTSError, TTSStream, TTSTimeoutError, start_stream
from app.utils.logging import get_logger

logger = get_logger(__name__)

CHUNK_SIZE = 4096

# Voice direction for instruction-following models (gpt-4o-mini-tts and newer).
DEFAULT_INSTRUCTIONS: dict[Language, str] = {
    "my-MM": (
        "Speak Burmese (Myanmar) naturally, like a native speaker. You are a warm, "
        "friendly store assistant. Clear and at a natural, unhurried pace. Read product "
        "names and the words Aisle, Rack and Shelf in English."
    ),
    "en-US": ("You are a warm, friendly store assistant. Speak clearly at a natural pace."),
}


class OpenAITextToSpeech:
    name = "openai"

    def __init__(
        self,
        client: AsyncOpenAI,
        *,
        model: str,
        voice: str,
        audio_format: str,
        speed: float,
        request_timeout: float,
        first_byte_timeout: float,
        attempts: int = 2,
        instructions: dict[Language, str] | None = None,
    ) -> None:
        self._client = client.with_options(timeout=request_timeout)
        self._attempts = max(1, attempts)
        self._model = model
        self._voice = voice
        self._format = audio_format
        self._speed = speed
        self._first_byte_timeout = first_byte_timeout
        # tts-1 / tts-1-hd don't take instructions.
        self._instructions = (
            (instructions or DEFAULT_INSTRUCTIONS) if not model.startswith("tts-") else {}
        )

    async def _chunks(self, text: str, language: Language) -> AsyncIterator[bytes]:
        extra = {}
        if instructions := self._instructions.get(language):
            extra["instructions"] = instructions
        try:
            async with self._client.audio.speech.with_streaming_response.create(
                model=self._model,
                voice=self._voice,
                input=text,
                response_format=self._format,  # type: ignore[arg-type]
                speed=self._speed,
                **extra,  # type: ignore[arg-type]
            ) as response:
                async for chunk in response.iter_bytes(CHUNK_SIZE):
                    yield chunk
        except openai.APITimeoutError as exc:
            logger.warning("tts_provider_timeout", extra={"provider": self.name})
            raise TTSError("Speech generation timed out.") from exc
        except openai.APIError as exc:
            logger.error(
                "tts_provider_error",
                extra={
                    "provider": self.name,
                    "error": type(exc).__name__,
                    "detail": str(exc)[:300],
                },
            )
            raise TTSError() from exc
        except httpx2.HTTPError as exc:
            # Errors while reading the streamed body (e.g. the provider stalls after
            # sending headers) surface as raw transport errors, not openai.APIError.
            logger.warning(
                "tts_stream_error", extra={"provider": self.name, "error": type(exc).__name__}
            )
            raise TTSError() from exc

    async def stream(self, text: str, language: Language) -> TTSStream:
        """Start synthesis. A stalled request (no audio within the first-byte timeout)
        or a transport error before any audio is retried with a fresh request."""
        for attempt in range(1, self._attempts + 1):
            try:
                return await start_stream(
                    self._chunks(text, language),
                    provider=self.name,
                    voice=self._voice,
                    audio_format=self._format,
                    first_byte_timeout=self._first_byte_timeout,
                )
            except (TTSTimeoutError, TTSError) as exc:
                if attempt == self._attempts:
                    raise
                logger.warning(
                    "tts_retry",
                    extra={"provider": self.name, "attempt": attempt, "error": exc.code},
                )
        raise AssertionError("unreachable")
