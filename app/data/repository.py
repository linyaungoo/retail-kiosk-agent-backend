"""Business data access used by the agent tools.

Tools depend only on the BusinessRepository protocol. The in-memory implementation
reads a BusinessData snapshot from a provider; in Phase 5 the provider becomes the
Google Sheets cache, and later it could be Redis/PostgreSQL behind the same protocol.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol

from app.data.business_data import BusinessData
from app.data.search import ProductMatches, search_faqs, search_products
from app.models.business import FaqEntry, KioskRecord, Store

DataProvider = Callable[[], Awaitable[BusinessData]]


class BusinessRepository(Protocol):
    async def search_products(self, store_id: str, query: str, limit: int) -> ProductMatches: ...

    async def get_store(self, store_id: str) -> Store | None: ...

    async def get_kiosk(self, kiosk_id: str) -> KioskRecord | None: ...

    async def search_faqs(self, store_id: str, query: str, limit: int) -> list[FaqEntry]: ...


class InMemoryBusinessRepository:
    def __init__(self, provider: DataProvider) -> None:
        self._provider = provider

    @classmethod
    def from_data(cls, data: BusinessData) -> "InMemoryBusinessRepository":
        async def provide() -> BusinessData:
            return data

        return cls(provide)

    async def search_products(self, store_id: str, query: str, limit: int) -> ProductMatches:
        data = await self._provider()
        return search_products(data.products_by_store.get(store_id, ()), query, limit=limit)

    async def get_store(self, store_id: str) -> Store | None:
        data = await self._provider()
        return data.stores.get(store_id)

    async def get_kiosk(self, kiosk_id: str) -> KioskRecord | None:
        data = await self._provider()
        return data.kiosks.get(kiosk_id)

    async def search_faqs(self, store_id: str, query: str, limit: int) -> list[FaqEntry]:
        data = await self._provider()
        return search_faqs(data.faqs, store_id, query, limit=limit)
