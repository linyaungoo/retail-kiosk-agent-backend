"""Google Sheets data source: PRODUCTS, STORES and FAQ tabs in one API call.

Only the cache calls this (on startup and every CACHE_TTL_SECONDS), never a
customer request directly.
"""

import asyncio
from collections.abc import Sequence
from typing import Any, Protocol

import google.auth
import httpx
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account

from app.data.business_data import FAQ_TABLE, PRODUCTS_TABLE, STORES_TABLE, BusinessData
from app.data.sources import DataSourceError
from app.utils.logging import get_logger
from app.utils.timing import Timer

logger = get_logger(__name__)

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
TABLES = (PRODUCTS_TABLE, STORES_TABLE, FAQ_TABLE)


class TokenProvider(Protocol):
    async def token(self) -> str: ...


class GoogleTokenProvider:
    """OAuth access tokens for the Sheets API, refreshed only when expired (~hourly).

    Uses the service-account JSON file if given, otherwise Application Default
    Credentials (the attached service account on Cloud Run: no key file needed).
    """

    def __init__(self, credentials_file: str = "") -> None:
        credentials: Credentials
        if credentials_file:
            from_file = service_account.Credentials.from_service_account_file
            credentials = from_file(credentials_file, scopes=[READONLY_SCOPE])  # type: ignore[no-untyped-call]
        else:
            credentials, _ = google.auth.default(scopes=[READONLY_SCOPE])
        self._credentials = credentials
        self._request = GoogleAuthRequest()
        self._lock = asyncio.Lock()

    async def token(self) -> str:
        if not self._credentials.valid:
            async with self._lock:
                if not self._credentials.valid:
                    # google-auth is synchronous; keep the event loop free.
                    await asyncio.to_thread(self._credentials.refresh, self._request)
        token = self._credentials.token
        if not token:
            raise DataSourceError("Google credentials returned no access token")
        return str(token)


def rows_to_dicts(values: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    """First row is the header. Sheets omits trailing empty cells, so pad short rows."""
    if not values:
        return []
    header = [str(cell) for cell in values[0]]
    return [
        dict(zip(header, [*row, *[""] * (len(header) - len(row))], strict=False))
        for row in values[1:]
    ]


def _google_error(response: httpx.Response) -> str:
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = response.text[:200]
    return str(message)


class GoogleSheetsDataSource:
    name = "google_sheets"

    def __init__(
        self,
        *,
        spreadsheet_id: str,
        tokens: TokenProvider,
        http_client: httpx.AsyncClient,
        timeout_seconds: float,
    ) -> None:
        if not spreadsheet_id:
            raise ValueError("GOOGLE_SHEETS_ID is required for BUSINESS_DATA_SOURCE=google_sheets")
        self._url = f"{SHEETS_API}/{spreadsheet_id}/values:batchGet"
        self._tokens = tokens
        self._http = http_client
        # Connecting normally takes well under a second; fail fast so a retry can follow.
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds))

    async def fetch_tables(self) -> dict[str, list[dict[str, Any]]]:
        params: list[tuple[str, str | int | float | bool | None]] = [
            ("ranges", table) for table in TABLES
        ]
        params += [
            ("majorDimension", "ROWS"),
            # Raw values: long barcodes stay digits (no "1.2E+12"), booleans stay booleans.
            ("valueRenderOption", "UNFORMATTED_VALUE"),
            # ...but dates/times as displayed ("21:00"), not spreadsheet serial numbers.
            ("dateTimeRenderOption", "FORMATTED_STRING"),
        ]
        with Timer() as timer:
            try:
                token = await self._tokens.token()
                response = await self._http.get(
                    self._url,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=self._timeout,
                )
            except DataSourceError:
                raise
            except Exception as exc:  # network errors, auth refresh failures
                raise DataSourceError(
                    f"Google Sheets request failed: {type(exc).__name__}"
                ) from exc

        if response.status_code != 200:
            raise DataSourceError(
                f"Google Sheets returned {response.status_code}: {_google_error(response)}"
            )
        value_ranges = response.json().get("valueRanges", [])
        if len(value_ranges) != len(TABLES):
            raise DataSourceError(f"expected {len(TABLES)} tabs, got {len(value_ranges)}")

        tables = {
            table: rows_to_dicts(value_range.get("values", []))
            for table, value_range in zip(TABLES, value_ranges, strict=True)
        }
        row_counts = {f"{table.lower()}_rows": len(rows) for table, rows in tables.items()}
        logger.info("sheets_fetched", extra={"sheets_ms": timer.ms, **row_counts})
        return tables

    async def load(self) -> BusinessData:
        tables = await self.fetch_tables()
        # Parsing/indexing is CPU work; keep it off the event loop for large catalogs.
        return await asyncio.to_thread(
            BusinessData.from_rows,
            products=tables[PRODUCTS_TABLE],
            stores=tables[STORES_TABLE],
            faqs=tables[FAQ_TABLE],
        )
