from typing import Any

from pydantic import BaseModel, Field

from app.models.agent import Timing, ToolCallInfo
from app.models.kiosk import Language
from app.models.tools import ToolAction


class VoiceResponse(BaseModel):
    """Result of one voice turn.

    In the default audio mode this is sent base64url-encoded in the
    `X-Kiosk-Response` header while the body streams the spoken answer.
    In `?response=json` mode it is the body, with the audio in `audio_base64`.
    """

    success: bool = True
    session_id: str
    language: Language
    transcript: str  # what the customer said
    action: ToolAction
    answer: str  # what the mascot says
    data: dict[str, Any] = Field(default_factory=dict)
    data_truncated: bool | None = None  # header mode: data omitted to fit the header
    audio_format: str
    timing: Timing
    audio_base64: str | None = None  # json mode only
    tool_calls: list[ToolCallInfo] | None = None  # development only
