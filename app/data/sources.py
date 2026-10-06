"""Where business data comes from. The cache calls load(); nothing else does."""

from typing import Protocol

from app.data.business_data import BusinessData


class DataSourceError(Exception):
    """The source could not be read. The message is for logs/admins, not customers."""


class BusinessDataSource(Protocol):
    name: str

    async def load(self) -> BusinessData: ...
