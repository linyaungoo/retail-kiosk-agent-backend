from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness: business data loaded and STT / agent / TTS configured (503 otherwise)."""
    state = request.app.state
    cache = getattr(state, "cache", None)
    checks = {
        "business_data": bool(cache and cache.status()["loaded"]),
        "stt": getattr(state, "stt", None) is not None,
        "agent": getattr(state, "agent_service", None) is not None,
        "tts": getattr(state, "tts", None) is not None,
    }
    ok = all(checks.values())
    return JSONResponse(
        {"status": "ready" if ok else "not_ready", "checks": checks},
        status_code=200 if ok else 503,
    )
