"""Provider-neutral speech-to-text, plus audio upload validation.

Audio is only ever held in memory for the duration of a request; nothing is stored.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePath
from typing import Protocol

from app.errors import AppError, ErrorCode
from app.models.kiosk import Language
from app.utils.timing import Timer

# Formats the OpenAI transcription API accepts, by extension.
AUDIO_TYPES: dict[str, frozenset[str]] = {
    "wav": frozenset({"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"}),
    "mp3": frozenset({"audio/mpeg", "audio/mp3", "audio/mpeg3"}),
    "mpeg": frozenset({"audio/mpeg"}),
    "mpga": frozenset({"audio/mpeg"}),
    "m4a": frozenset({"audio/mp4", "audio/m4a", "audio/x-m4a", "audio/aac"}),
    "mp4": frozenset({"audio/mp4", "video/mp4"}),
    "webm": frozenset({"audio/webm", "video/webm"}),
    "ogg": frozenset({"audio/ogg", "application/ogg", "audio/opus"}),
    "oga": frozenset({"audio/ogg"}),
    "flac": frozenset({"audio/flac", "audio/x-flac"}),
}
# Clients often send these when they don't know better; the extension decides then.
_GENERIC_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_NO_SPEECH = re.compile(r"^[\s\W_]*$")


class STTError(AppError):
    def __init__(self, message: str = "Unable to process speech.") -> None:
        super().__init__(ErrorCode.STT_FAILED, message, 502)


class STTTimeoutError(AppError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.STT_TIMEOUT, "Speech recognition took too long.", 504)


class EmptyTranscriptError(AppError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.EMPTY_TRANSCRIPT, "No speech was detected.", 422)


@dataclass(frozen=True, slots=True)
class AudioUpload:
    data: bytes
    filename: str  # always has a supported extension
    content_type: str


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    language: Language | None
    duration_ms: int
    provider: str
    model: str


class SpeechToTextService(Protocol):
    name: str

    async def transcribe(
        self,
        audio: bytes,
        filename: str,
        language: Language | None = None,
        *,
        content_type: str | None = None,
        vocabulary: Sequence[str] = (),
    ) -> TranscriptionResult:
        """Transcribe one utterance.

        `language` is the kiosk's language; customers may still mix Burmese and English.
        `vocabulary` lists names (brands, products) to spell as given.
        Raises STTError / STTTimeoutError / EmptyTranscriptError.
        """
        ...


def validate_audio(data: bytes, filename: str | None, content_type: str | None) -> AudioUpload:
    """Check the upload is non-empty and a supported audio format.

    Size is enforced while reading (see read_upload); this checks content and type.
    """
    if not data:
        raise AppError(ErrorCode.INVALID_AUDIO, "The audio file is empty.", 400)
    mime = (content_type or "").split(";")[0].strip().lower()
    extension = PurePath(filename or "").suffix.lower().lstrip(".")

    if extension in AUDIO_TYPES:
        if mime not in _GENERIC_TYPES and mime not in AUDIO_TYPES[extension]:
            raise AppError(
                ErrorCode.UNSUPPORTED_AUDIO,
                f"Content type {mime} does not match a .{extension} file.",
                415,
            )
    else:
        # No usable extension: derive one from the content type.
        extension = next((ext for ext, types in AUDIO_TYPES.items() if mime in types), "")
        if not extension:
            allowed = ", ".join(sorted(AUDIO_TYPES))
            raise AppError(
                ErrorCode.UNSUPPORTED_AUDIO,
                f"Unsupported audio format. Use one of: {allowed}.",
                415,
            )
    safe_name = f"audio.{extension}"  # never forward client-supplied file names
    return AudioUpload(
        data=data, filename=safe_name, content_type=mime or "application/octet-stream"
    )


def is_empty_transcript(text: str) -> bool:
    return bool(_NO_SPEECH.match(text))


async def transcribe_upload(
    stt: SpeechToTextService,
    upload: AudioUpload,
    language: Language | None,
    *,
    vocabulary: Sequence[str] = (),
) -> TranscriptionResult:
    """Transcribe a validated upload with any provider; reject silence/noise-only results."""
    result = await stt.transcribe(
        upload.data,
        upload.filename,
        language,
        content_type=upload.content_type,
        vocabulary=vocabulary,
    )
    if is_empty_transcript(result.text):
        raise EmptyTranscriptError()
    return result


class MockSpeechToText:
    """Development provider: returns a canned utterance instead of real recognition."""

    name = "mock"
    TEXTS: dict[Language, str] = {"my-MM": "Coca Cola ဘယ်မှာရှိလဲ", "en-US": "Where is Coca Cola?"}

    async def transcribe(
        self,
        audio: bytes,
        filename: str,
        language: Language | None = None,
        *,
        content_type: str | None = None,
        vocabulary: Sequence[str] = (),
    ) -> TranscriptionResult:
        with Timer() as timer:
            text = self.TEXTS[language or "my-MM"]
        return TranscriptionResult(
            text=text, language=language, duration_ms=timer.ms, provider=self.name, model="mock"
        )
