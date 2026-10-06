"""POST /api/tts: text in, streamed audio out."""

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.dependencies import SettingsDep, TTSDep, verify_kiosk_key
from app.models.tts import TTSRequest
from app.services.tts_service import TTSStream
from app.utils.logging import get_logger
from app.utils.timing import Timer

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["tts"], dependencies=[Depends(verify_kiosk_key)])


def audio_response(
    stream: TTSStream,
    *,
    log_extra: dict[str, object],
    log_event: str = "tts",
    headers: dict[str, str] | None = None,
    request_timer: Timer | None = None,
) -> StreamingResponse:
    """Stream audio to the client as it arrives; log `<log_event>_completed` (or
    `_incomplete` if the client went away) with totals when the stream ends."""

    async def body() -> AsyncIterator[bytes]:
        size = 0
        completed = False
        try:
            async for chunk in stream.chunks:
                size += len(chunk)
                yield chunk
            completed = True
        finally:
            extra = {
                **log_extra,
                "provider": stream.provider,
                "voice": stream.voice,
                "format": stream.audio_format,
                "tts_first_byte_ms": stream.first_byte_ms,
                "tts_ms": stream.started.ms,
                "audio_bytes": size,
            }
            if request_timer is not None:
                extra["stream_end_ms"] = request_timer.ms
            logger.info(f"{log_event}_{'completed' if completed else 'incomplete'}", extra=extra)

    return StreamingResponse(
        body(),
        media_type=stream.media_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'inline; filename="speech.{stream.audio_format}"',
            "X-TTS-Provider": stream.provider,
            "X-TTS-First-Byte-Ms": str(stream.first_byte_ms),
            **(headers or {}),
        },
    )


@router.post(
    "/tts",
    response_class=StreamingResponse,
    responses={200: {"content": {"audio/mpeg": {}}, "description": "Streamed audio"}},
)
async def text_to_speech(body: TTSRequest, tts: TTSDep, settings: SettingsDep) -> StreamingResponse:
    text = body.text[: settings.max_tts_chars]
    # Synthesis starts here; errors before the first audio byte become JSON errors.
    stream = await tts.stream(text, body.language)
    return audio_response(stream, log_extra={"language": body.language, "text_chars": len(text)})
