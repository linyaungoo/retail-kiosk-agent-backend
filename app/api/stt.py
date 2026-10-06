"""POST /api/stt: audio upload in, transcript out. Audio is never stored."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.dependencies import CacheDep, SettingsDep, STTDep, verify_kiosk_key
from app.errors import AppError, ErrorCode
from app.models.agent import ID_PATTERN
from app.models.kiosk import Language
from app.models.stt import STTResponse
from app.services.cache_service import BusinessDataCache, DataUnavailableError
from app.services.stt_service import transcribe_upload, validate_audio
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["stt"], dependencies=[Depends(verify_kiosk_key)])


async def read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise AppError(
            ErrorCode.AUDIO_TOO_LARGE, f"Audio must be at most {max_bytes // 1_048_576} MB.", 413
        )
    return data


async def stt_vocabulary(cache: BusinessDataCache, enabled: bool) -> tuple[str, ...]:
    """Brand names from the cached catalog. Never blocks STT on business data."""
    if not enabled or not cache.status()["loaded"]:
        return ()
    try:
        return (await cache.get()).brands
    except DataUnavailableError:
        return ()


@router.post("/stt", response_model=STTResponse)
async def speech_to_text(
    audio: Annotated[UploadFile, File(description="Recorded utterance (wav, m4a, mp3, webm, ...)")],
    language: Annotated[Language, Form()],
    session_id: Annotated[str, Form(pattern=ID_PATTERN)],
    kiosk_id: Annotated[str, Form(pattern=ID_PATTERN)],
    stt: STTDep,
    cache: CacheDep,
    settings: SettingsDep,
) -> STTResponse:
    upload = validate_audio(
        await read_upload(audio, settings.max_audio_bytes), audio.filename, audio.content_type
    )
    result = await transcribe_upload(
        stt,
        upload,
        language,
        vocabulary=await stt_vocabulary(cache, settings.stt_catalog_vocabulary),
    )

    extra: dict[str, object] = {
        "session_id": session_id,
        "kiosk_id": kiosk_id,
        "language": language,
        "provider": result.provider,
        "model": result.model,
        "stt_ms": result.duration_ms,
        "audio_bytes": len(upload.data),
        "audio_format": upload.filename.rsplit(".", 1)[-1],
    }
    if settings.log_transcripts:
        extra["transcript"] = result.text
    logger.info("stt_completed", extra=extra)
    return STTResponse(text=result.text, language=language, duration_ms=result.duration_ms)
