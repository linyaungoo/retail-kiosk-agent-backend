"""GET /api/kiosk/config: which voice mode the kiosk should use (switchable without
rebuilding the app)."""

from fastapi import APIRouter, Depends, Request

from app.dependencies import SettingsDep, verify_kiosk_key
from app.models.realtime import KioskConfigResponse

router = APIRouter(prefix="/api/kiosk", tags=["kiosk"], dependencies=[Depends(verify_kiosk_key)])


@router.get("/config", response_model=KioskConfigResponse)
async def kiosk_config(request: Request, settings: SettingsDep) -> KioskConfigResponse:
    state = request.app.state
    realtime = getattr(state, "realtime", None) is not None
    chained = all(
        getattr(state, name, None) is not None for name in ("stt", "agent_service", "tts")
    )
    # Asked for realtime but it isn't configured: tell the kiosk to use chained.
    mode = settings.voice_mode if (settings.voice_mode == "chained" or realtime) else "chained"
    return KioskConfigResponse(
        voice_mode=mode,
        realtime_available=realtime,
        chained_available=chained,
        languages=["my-MM", "en-US"],
    )
