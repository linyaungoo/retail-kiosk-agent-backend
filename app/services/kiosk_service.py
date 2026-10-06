"""Resolve the trusted kiosk context for a request."""

from app.data.repository import BusinessRepository
from app.errors import AppError, ErrorCode
from app.models.kiosk import KioskContext, Language
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def resolve_kiosk(
    repository: BusinessRepository,
    *,
    organization_id: str,
    store_id: str,
    kiosk_id: str,
    session_id: str,
    language: Language,
) -> KioskContext:
    """Validate that the kiosk's store exists, is active, and belongs to its organization.

    POC: store/organization come from the kiosk request and are checked here.
    Later: look kiosk_id up server-side (KIOSK -> STORE -> ORG) and ignore client values.
    """
    store = await repository.get_store(store_id)
    if store is None or store.organization_id != organization_id:
        logger.warning(
            "invalid_kiosk",
            extra={"kiosk_id": kiosk_id, "store_id": store_id, "organization_id": organization_id},
        )
        raise AppError(ErrorCode.INVALID_KIOSK, "This kiosk is not set up for a valid store.", 403)
    return KioskContext(
        organization_id=organization_id,
        store_id=store_id,
        kiosk_id=kiosk_id,
        session_id=session_id,
        language=language,
    )
