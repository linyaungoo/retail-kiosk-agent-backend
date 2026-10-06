"""Structured tool results.

Every result carries a deterministic `action`, so the API response never depends
on parsing the model's natural-language answer.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.business import Availability, Product


class ToolAction(StrEnum):
    GENERAL = "GENERAL"
    PRODUCT_LOCATION = "PRODUCT_LOCATION"
    PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"
    STORE_INFO = "STORE_INFO"
    FAQ = "FAQ"
    BMI = "BMI"
    NEED_MORE_INFORMATION = "NEED_MORE_INFORMATION"
    ERROR = "ERROR"


class ToolResult(BaseModel):
    """Base for every tool result: the deterministic outcome of the call."""

    action: ToolAction


# --- search_product ---------------------------------------------------------


class ProductInfo(BaseModel):
    product_id: str
    product_name: str
    brand: str
    category: str
    availability: Availability
    price: int | None
    zone: str
    aisle: str
    rack: str
    shelf: str

    @classmethod
    def from_product(cls, product: Product) -> "ProductInfo":
        return cls(
            product_id=product.product_id,
            product_name=product.name,
            brand=product.brand,
            category=product.category,
            availability=product.availability,
            price=product.price,
            zone=product.zone,
            aisle=product.aisle,
            rack=product.rack,
            shelf=product.shelf,
        )


class ProductSearchResult(ToolResult):
    found: bool
    total_matches: int
    products: list[ProductInfo] = Field(default_factory=list)
    # Close-but-not-exact matches, offered only when nothing matched fully.
    suggestions: list[ProductInfo] = Field(default_factory=list)


# --- get_store_info ---------------------------------------------------------


class StoreTopic(StrEnum):
    OPENING_TIME = "OPENING_TIME"
    CLOSING_TIME = "CLOSING_TIME"
    PARKING = "PARKING"
    RESTROOM = "RESTROOM"
    CUSTOMER_SERVICE = "CUSTOMER_SERVICE"
    GENERAL = "GENERAL"


class StoreInfoResult(ToolResult):
    found: bool
    topic: StoreTopic
    store_name: str | None = None
    opening_time: str | None = None  # "HH:MM", 24-hour
    closing_time: str | None = None
    parking_available: bool | None = None
    restroom_available: bool | None = None
    customer_service_location: str | None = None


# --- search_faq -------------------------------------------------------------


class FaqAnswer(BaseModel):
    faq_id: str
    category: str
    question: str
    answer: str


class FaqSearchResult(ToolResult):
    found: bool
    answers: list[FaqAnswer] = Field(default_factory=list)


# --- calculate_bmi ----------------------------------------------------------


class BmiCategory(StrEnum):
    UNDERWEIGHT = "UNDERWEIGHT"
    NORMAL = "NORMAL"
    OVERWEIGHT = "OVERWEIGHT"
    OBESE = "OBESE"


class BmiResult(ToolResult):
    bmi: float
    category: BmiCategory
    height_cm: float
    weight_kg: float


class MissingInformationResult(ToolResult):
    """A tool could not run because the customer has not given required values yet."""

    action: ToolAction = ToolAction.NEED_MORE_INFORMATION
    missing: list[str]
