"""Voice turn: speech -> transcript -> Retail Store Assistant -> speech.

Each stage is timed. If a later stage fails, the error carries what earlier
stages produced (transcript, answer) so the kiosk can still show it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.data.repository import BusinessRepository
from app.errors import AppError
from app.models.kiosk import KioskContext
from app.services.agent_service import AgentReply
from app.services.stt_service import AudioUpload, SpeechToTextService, transcribe_upload
from app.services.tts_service import TextToSpeechService, TTSStream
from app.tools.context import ToolContext


class Assistant(Protocol):
    async def reply(self, tools: ToolContext, message: str) -> AgentReply: ...


@dataclass(frozen=True, slots=True)
class VoiceTurn:
    transcript: str
    stt_ms: int
    reply: AgentReply
    audio: TTSStream  # first bytes already received; the rest is streamed


async def run_voice_turn(
    *,
    upload: AudioUpload,
    kiosk: KioskContext,
    stt: SpeechToTextService,
    assistant: Assistant,
    tts: TextToSpeechService,
    repository: BusinessRepository,
    vocabulary: Sequence[str] = (),
) -> VoiceTurn:
    transcription = await transcribe_upload(stt, upload, kiosk.language, vocabulary=vocabulary)
    transcript = transcription.text

    try:
        reply = await assistant.reply(ToolContext(kiosk=kiosk, repository=repository), transcript)
    except AppError as exc:
        exc.context.setdefault("transcript", transcript)
        raise

    try:
        audio = await tts.stream(reply.answer, kiosk.language)
    except AppError as exc:
        # The answer exists; only the voice failed. Let the kiosk display it.
        exc.context.update(
            transcript=transcript,
            answer=reply.answer,
            action=reply.action,
            data=reply.data,
        )
        raise

    return VoiceTurn(
        transcript=transcript, stt_ms=transcription.duration_ms, reply=reply, audio=audio
    )
