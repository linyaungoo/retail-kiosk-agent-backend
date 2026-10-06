"""Operator endpoints: business data cache status and manual refresh."""

from typing import Any

from fastapi import APIRouter, Depends

from app.dependencies import CacheDep, verify_admin_access
from app.errors import AppError, ErrorCode
from app.services.cache_service import DataUnavailableError

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(verify_admin_access)])


@router.get("/cache")
async def cache_status(cache: CacheDep) -> dict[str, Any]:
    return {"success": True, **cache.status()}


@router.post("/cache/refresh")
async def refresh_cache(cache: CacheDep) -> dict[str, Any]:
    """Reload PRODUCTS/STORES/FAQ now, e.g. right after editing the sheet."""
    try:
        await cache.refresh()
    except DataUnavailableError:
        # Admin-only endpoint: the source's error (e.g. "API disabled", "tab missing")
        # is what the operator needs. Last good data is still being served.
        message = cache.status()["last_error"] or "Refresh failed."
        raise AppError(ErrorCode.DATA_SOURCE_ERROR, message, 502) from None
    return {"success": True, **cache.status()}
