"""Request/response models for the agent endpoint."""

from typing import Any

from pydantic import BaseModel, Field

from app.models.kiosk import Language
from app.models.tools import ToolAction

ID_PATTERN = r"^[A-Za-z0-9._:-]{1,100}$"


class AgentRequest(BaseModel):
    # Optional and advisory: a registered kiosk's store comes from the KIOSKS registry.
    organization_id: str | None = Field(default=None, pattern=ID_PATTERN, examples=["ORG-001"])
    store_id: str | None = Field(default=None, pattern=ID_PATTERN, examples=["STORE-001"])
    kiosk_id: str = Field(pattern=ID_PATTERN, examples=["KIOSK-001"])
    session_id: str = Field(pattern=ID_PATTERN, examples=["MOBILE-abc123"])
    language: Language = Field(examples=["my-MM"])
    message: str = Field(min_length=1, max_length=1000, examples=["Coca Cola ဘယ်မှာရှိလဲ"])


class Timing(BaseModel):
    stt_ms: int | None = None
    agent_ms: int | None = None
    tool_ms: int | None = None
    tts_first_byte_ms: int | None = None
    tts_ms: int | None = None
    total_ms: int


class ToolCallInfo(BaseModel):
    """Debug view of one tool call (development only)."""

    name: str
    arguments: dict[str, Any]
    action: ToolAction | None
    duration_ms: int
    error: str | None = None


class AgentResponse(BaseModel):
    success: bool = True
    session_id: str
    language: Language
    action: ToolAction
    answer: str
    data: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int
    timing: Timing
    tool_calls: list[ToolCallInfo] | None = None
