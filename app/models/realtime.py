"""Request/response models for the Realtime voice mode and kiosk configuration."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.agent import ID_PATTERN
from app.models.kiosk import Language


class RealtimeSessionRequest(BaseModel):
    kiosk_id: str = Field(pattern=ID_PATTERN, examples=["KIOSK-001"])
    session_id: str = Field(pattern=ID_PATTERN, examples=["SESSION-abc123"])
    language: Language = Field(examples=["my-MM"])
    # Optional and advisory: a registered kiosk's store comes from the KIOSKS registry.
    organization_id: str | None = Field(default=None, pattern=ID_PATTERN)
    store_id: str | None = Field(default=None, pattern=ID_PATTERN)
    # The kiosk's WebRTC SDP offer. The backend forwards it to OpenAI with the
    # server-built session; the kiosk never talks to OpenAI's API directly.
    sdp: str = Field(min_length=10, max_length=20_000)

    @field_validator("sdp")
    @classmethod
    def _looks_like_sdp(cls, value: str) -> str:
        if not value.lstrip().startswith("v=0"):
            raise ValueError("sdp must be a WebRTC SDP offer")
        return value


class RealtimeSessionResponse(BaseModel):
    success: bool = True
    session_id: str
    language: Language
    realtime_session_id: str  # reference for /end and /metrics
    sdp: str  # SDP answer for the kiosk's RTCPeerConnection
    model: str
    voice: str
    # When set, the kiosk sends this event on the data channel as soon as it opens, so
    # the mascot greets first (tools disabled for that one response).
    greeting_event: dict[str, Any] | None
    turn_detection: str
    idle_timeout_seconds: int
    max_session_seconds: int


class RealtimeEndRequest(BaseModel):
    kiosk_id: str = Field(pattern=ID_PATTERN)


class RealtimeClientMetrics(BaseModel):
    """Timings only the kiosk can observe (e.g. when audio actually started playing)."""

    model_config = ConfigDict(extra="forbid")

    kiosk_id: str = Field(pattern=ID_PATTERN)
    realtime_connection_ms: int | None = Field(default=None, ge=0, le=600_000)
    time_to_first_model_event_ms: int | None = Field(default=None, ge=0, le=600_000)
    time_to_first_audio_ms: int | None = Field(default=None, ge=0, le=600_000)
    conversation_turn_ms: int | None = Field(default=None, ge=0, le=600_000)
    interrupt_count: int | None = Field(default=None, ge=0, le=10_000)
    reconnect_count: int | None = Field(default=None, ge=0, le=10_000)
    session_duration_seconds: float | None = Field(default=None, ge=0, le=86_400)


class KioskConfigResponse(BaseModel):
    success: bool = True
    voice_mode: Literal["chained", "realtime"]
    realtime_available: bool
    chained_available: bool
    languages: list[Language]
