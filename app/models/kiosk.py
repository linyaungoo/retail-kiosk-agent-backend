"""Trusted kiosk context."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["my-MM", "en-US"]

_ID_PATTERN = r"^[A-Za-z0-9._:-]{1,100}$"


class KioskContext(BaseModel):
    """Identity of the kiosk handling a request.

    Resolved by the backend from the kiosk request, never chosen by the model.
    Tools read store_id/organization_id from here, not from tool arguments.
    """

    model_config = ConfigDict(frozen=True)

    organization_id: str = Field(pattern=_ID_PATTERN)
    store_id: str = Field(pattern=_ID_PATTERN)
    kiosk_id: str = Field(pattern=_ID_PATTERN)
    session_id: str = Field(pattern=_ID_PATTERN)
    language: Language
