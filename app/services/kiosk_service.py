"""Resolve the trusted kiosk context for a request."""

from app.data.repository import BusinessRepository
from app.errors import AppError, ErrorCode
from app.models.kiosk import KioskContext, KioskPrincipal, Language
from app.utils.logging import get_logger

logger = get_logger(__name__)


def _invalid(message: str = "This kiosk is not set up for a valid store.") -> AppError:
    return AppError(ErrorCode.INVALID_KIOSK, message, 403)


async def resolve_kiosk(
    repository: BusinessRepository,
    *,
    principal: KioskPrincipal,
    kiosk_id: str,
    session_id: str,
    language: Language,
    organization_id: str | None = None,
    store_id: str | None = None,
    registry_required: bool = False,
    fallback_store_id: str | None = None,
) -> KioskContext:
    """Decide which store/organization this kiosk serves; never trust the client for it.

    1. A key bound to a kiosk can only act as that kiosk.
    2. A kiosk in the KIOSKS registry serves the registry's store; any store_id /
       organization_id the client sends is ignored (and logged if different).
    3. Unregistered kiosks are rejected when `registry_required` (production);
       otherwise (development) the client's store_id is validated and used, or
       `fallback_store_id` when the client sends none (the kiosk app doesn't).
    """
    if principal.kiosk_id is not None and principal.kiosk_id != kiosk_id:
        logger.warning(
            "kiosk_key_mismatch", extra={"kiosk_id": kiosk_id, "key_kiosk_id": principal.kiosk_id}
        )
        raise _invalid("This kiosk key belongs to a different kiosk.")

    registered = await repository.get_kiosk(kiosk_id)
    if registered is not None:
        trusted_store_id = registered.store_id
    elif registry_required:
        logger.warning("kiosk_not_registered", extra={"kiosk_id": kiosk_id})
        raise _invalid("This kiosk is not registered.")
    elif store_id or fallback_store_id:
        trusted_store_id = store_id or fallback_store_id or ""
    else:
        raise _invalid()

    store = await repository.get_store(trusted_store_id)
    if store is None:
        logger.warning("invalid_kiosk", extra={"kiosk_id": kiosk_id, "store_id": trusted_store_id})
        raise _invalid()
    if registered is None and organization_id and store.organization_id != organization_id:
        logger.warning(
            "invalid_kiosk",
            extra={"kiosk_id": kiosk_id, "store_id": store_id, "organization_id": organization_id},
        )
        raise _invalid()
    if registered is not None and (
        (store_id and store_id != store.store_id)
        or (organization_id and organization_id != store.organization_id)
    ):
        logger.warning(
            "client_store_ignored",
            extra={
                "kiosk_id": kiosk_id,
                "client_store_id": store_id,
                "client_organization_id": organization_id,
                "store_id": store.store_id,
            },
        )

    return KioskContext(
        organization_id=store.organization_id,
        store_id=store.store_id,
        kiosk_id=kiosk_id,
        session_id=session_id,
        language=language,
    )
