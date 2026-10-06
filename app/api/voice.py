"""POST /api/kiosk/voice: the full voice turn (speech in, speech out).

audio -> STT -> Retail Store Assistant (+ tools) -> answer text -> TTS -> audio

Default response: the spoken answer is streamed as soon as the first audio bytes
exist; transcript, answer, action, data and timings are in the `X-Kiosk-Response`
header (base64url JSON). `?response=json` returns one JSON body with base64 audio.
"""

import base64
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse, Response

from app.api.agent import debug_tool_calls
from app.api.stt import read_upload, stt_vocabulary
from app.api.tts import audio_response
from app.dependencies import (
    AgentServiceDep,
    CacheDep,
    RepositoryDep,
    SettingsDep,
    STTDep,
    TTSDep,
    verify_kiosk_key,
)
from app.models.agent import ID_PATTERN, Timing
from app.models.kiosk import Language
from app.models.voice import VoiceResponse
from app.services.kiosk_service import resolve_kiosk
from app.services.stt_service import validate_audio
from app.services.voice_service import VoiceTurn, run_voice_turn
from app.utils.logging import get_logger
from app.utils.timing import Timer

logger = get_logger(__name__)

router = APIRouter(prefix="/api/kiosk", tags=["voice"], dependencies=[Depends(verify_kiosk_key)])

METADATA_HEADER = "X-Kiosk-Response"
MAX_HEADER_CHARS = 7000  # proxies commonly cap a header at ~8 KB


def encode_metadata(payload: VoiceResponse) -> str:
    def encode(model: VoiceResponse) -> str:
        raw = model.model_dump_json(exclude_none=True).encode()
        return base64.urlsafe_b64encode(raw).decode("ascii")

    encoded = encode(payload)
    if len(encoded) > MAX_HEADER_CHARS:
        encoded = encode(payload.model_copy(update={"data": {}, "data_truncated": True}))
    return encoded


def _log_extra(turn: VoiceTurn, payload: VoiceResponse, log_transcripts: bool) -> dict[str, Any]:
    extra: dict[str, Any] = {
        "session_id": payload.session_id,
        "language": payload.language,
        "action": payload.action,
        "stt_ms": payload.timing.stt_ms,
        "agent_ms": payload.timing.agent_ms,
        "tool_ms": payload.timing.tool_ms,
        "first_audio_ms": payload.timing.total_ms,
        "llm_calls": turn.reply.llm_calls,
    }
    if log_transcripts:
        extra |= {"transcript_text": turn.transcript, "answer_text": turn.reply.answer}
    return extra


@router.post(
    "/voice",
    response_model=None,
    responses={
        200: {
            "content": {"audio/mpeg": {}, "application/json": {}},
            "description": "Streamed spoken answer (metadata in X-Kiosk-Response), "
            "or JSON with ?response=json",
        }
    },
)
async def kiosk_voice(
    audio: Annotated[UploadFile, File(description="Recorded utterance (wav, m4a, mp3, webm, ...)")],
    organization_id: Annotated[str, Form(pattern=ID_PATTERN)],
    store_id: Annotated[str, Form(pattern=ID_PATTERN)],
    kiosk_id: Annotated[str, Form(pattern=ID_PATTERN)],
    session_id: Annotated[str, Form(pattern=ID_PATTERN)],
    language: Annotated[Language, Form()],
    stt: STTDep,
    assistant: AgentServiceDep,
    tts: TTSDep,
    repository: RepositoryDep,
    cache: CacheDep,
    settings: SettingsDep,
    response: Annotated[Literal["audio", "json"], Query()] = "audio",
) -> Response:
    request_timer = Timer.started()

    upload = validate_audio(
        await read_upload(audio, settings.max_audio_bytes), audio.filename, audio.content_type
    )
    # Reject an unknown/inactive kiosk before paying for speech recognition.
    kiosk = await resolve_kiosk(
        repository,
        organization_id=organization_id,
        store_id=store_id,
        kiosk_id=kiosk_id,
        session_id=session_id,
        language=language,
    )
    turn = await run_voice_turn(
        upload=upload,
        kiosk=kiosk,
        stt=stt,
        assistant=assistant,
        tts=tts,
        repository=repository,
        vocabulary=await stt_vocabulary(cache, settings.stt_catalog_vocabulary),
    )

    reply = turn.reply
    payload = VoiceResponse(
        session_id=kiosk.session_id,
        language=kiosk.language,
        transcript=turn.transcript,
        action=reply.action,
        answer=reply.answer,
        data=reply.data,
        audio_format=turn.audio.audio_format,
        timing=Timing(
            stt_ms=turn.stt_ms,
            agent_ms=reply.agent_ms,
            tool_ms=reply.tool_ms,
            tts_first_byte_ms=turn.audio.first_byte_ms,
            total_ms=request_timer.ms,  # until the first audio byte is ready
        ),
        tool_calls=debug_tool_calls(reply, settings),
    )
    log_extra = _log_extra(turn, payload, settings.log_transcripts)

    if response == "json":
        audio_bytes = b"".join([chunk async for chunk in turn.audio.chunks])
        payload.timing.tts_ms = turn.audio.started.ms
        payload.timing.total_ms = request_timer.ms
        payload.audio_base64 = base64.b64encode(audio_bytes).decode("ascii")
        logger.info(
            "voice_completed",
            extra={**log_extra, "tts_ms": payload.timing.tts_ms, "total_ms": request_timer.ms},
        )
        return JSONResponse(json.loads(payload.model_dump_json(exclude_none=True)))

    return audio_response(
        turn.audio,
        log_event="voice",
        log_extra=log_extra,
        headers={METADATA_HEADER: encode_metadata(payload), "X-Action": payload.action},
        request_timer=request_timer,
    )
