"""Live Google Sheets test (real API).

Run with:  RUN_LIVE_TESTS=1 pytest -m live tests/test_sheets_live.py
"""

import os
import time

import httpx
import pytest
from dotenv import dotenv_values

from app.data.search import search_products
from app.services.google_sheets_service import GoogleSheetsDataSource, GoogleTokenProvider

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1"),
]

# conftest forces BUSINESS_DATA_SOURCE=mock, so read the sheet settings straight from .env.
ENV = {**dotenv_values(".env"), **{k: v for k, v in os.environ.items() if k.startswith("GOOGLE_")}}


async def test_load_from_real_sheet() -> None:
    sheet_id = ENV.get("GOOGLE_SHEETS_ID") or ""
    if not sheet_id:
        pytest.skip("GOOGLE_SHEETS_ID not set")
    async with httpx.AsyncClient() as client:
        source = GoogleSheetsDataSource(
            spreadsheet_id=sheet_id,
            tokens=GoogleTokenProvider(ENV.get("GOOGLE_SERVICE_ACCOUNT_FILE") or ""),
            http_client=client,
            timeout_seconds=10,
        )
        data = await source.load()

    counts = data.counts()
    assert counts["products"] > 0 and counts["stores"] > 0 and counts["faqs"] > 0
    assert "STORE-001" in data.stores

    items = data.products_by_store["STORE-001"]
    start = time.perf_counter()
    matches = search_products(items, "Coca Cola", limit=5)
    assert (time.perf_counter() - start) * 1000 < 100
    assert matches.products
