"""Business data domain models (products, stores, FAQ)."""

from datetime import time
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Availability(StrEnum):
    AVAILABLE = "AVAILABLE"
    LOW_STOCK = "LOW_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"


class FaqScope(StrEnum):
    GLOBAL = "GLOBAL"
    STORE = "STORE"


class Product(BaseModel):
    """One product at one store; (product_id, store_id) is unique."""

    model_config = ConfigDict(frozen=True)

    product_id: str
    sku: str = ""
    barcode: str = ""
    name: str
    brand: str = ""
    category: str = ""
    store_id: str
    zone: str = ""
    aisle: str = ""
    rack: str = ""
    shelf: str = ""
    availability: Availability
    price: int | None = None  # whole kyat
    active: bool = True


class Store(BaseModel):
    model_config = ConfigDict(frozen=True)

    store_id: str
    organization_id: str
    name: str
    opening_time: time | None = None
    closing_time: time | None = None
    customer_service_location: str = ""
    parking_available: bool | None = None
    restroom_available: bool | None = None
    active: bool = True


class KioskRecord(BaseModel):
    """A registered kiosk device and the store it is installed in (KIOSKS tab).

    The store a kiosk serves is decided here, server-side, never by the client.
    """

    model_config = ConfigDict(frozen=True)

    kiosk_id: str
    store_id: str
    name: str = ""
    active: bool = True


class FaqEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    faq_id: str
    scope: FaqScope
    store_id: str | None = None
    category: str
    question: str
    answer_en: str = ""
    answer_mm: str = ""
    keywords: tuple[str, ...] = ()
    active: bool = True
