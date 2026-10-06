from pydantic import BaseModel

from app.models.kiosk import Language


class STTResponse(BaseModel):
    success: bool = True
    text: str
    language: Language
    duration_ms: int  # speech-to-text latency
