"""Shared types for agent tools."""

from dataclasses import dataclass

from app.data.repository import BusinessRepository
from app.models.kiosk import KioskContext


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Trusted, backend-supplied context passed to every tool.

    The model never sees or sets these values; tools read store_id and language
    from here instead of accepting them as arguments.
    """

    kiosk: KioskContext
    repository: BusinessRepository


class ToolInputError(ValueError):
    """Invalid tool arguments. The message is safe to show to the model."""
