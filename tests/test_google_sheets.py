"""GoogleSheetsDataSource against a simulated Sheets API (no network)."""

import csv
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.data.business_data import BusinessData
from app.data.sources import DataSourceError
from app.services.google_sheets_service import GoogleSheetsDataSource, rows_to_dicts
from tests.conftest import SAMPLE_DATA_DIR

SHEET_ID = "sheet-123"


class FakeTokens:
    async def token(self) -> str:
        return "test-token"


def _as_unformatted(cell: str) -> Any:
    """What UNFORMATTED_VALUE returns: booleans and numbers typed, text as text."""
    if cell in ("TRUE", "FALSE"):
        return cell == "TRUE"
    if cell.isdigit():
        return int(cell)
    return cell


def _sheet_values(table: str) -> list[list[Any]]:
    with (SAMPLE_DATA_DIR / f"{table}.csv").open(encoding="utf-8", newline="") as fh:
        rows = [[_as_unformatted(c) for c in row] for row in csv.reader(fh)]
    # Sheets omits trailing empty cells.
    for row in rows:
        while row and row[-1] == "":
            row.pop()
    return rows


def _sample_response() -> dict[str, Any]:
    return {
        "spreadsheetId": SHEET_ID,
        "valueRanges": [
            {"range": f"{t}!A1:Z1000", "majorDimension": "ROWS", "values": _sheet_values(t)}
            for t in ("PRODUCTS", "STORES", "FAQ", "KIOSKS")
        ],
    }


def _source(handler: Callable[[httpx.Request], httpx.Response]) -> GoogleSheetsDataSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GoogleSheetsDataSource(
        spreadsheet_id=SHEET_ID, tokens=FakeTokens(), http_client=client, timeout_seconds=5
    )


async def test_loads_same_data_as_csv(business_data: BusinessData) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_sample_response())

    data = await _source(handler).load()

    assert data.counts() == business_data.counts()
    assert data.counts() == {"products": 37, "stores": 3, "faqs": 11, "kiosks": 3}
    assert data.kiosks["KIOSK-002"].store_id == "STORE-002"
    coke = next(i.product for i in data.products_by_store["STORE-001"] if i.product.sku == "CC-1L")
    assert (coke.barcode, coke.price, coke.aisle) == ("100001", 1800, "A03")
    assert data.stores["STORE-002"].parking_available is False
    assert str(data.stores["STORE-001"].closing_time) == "21:00:00"

    # One API call for all three tabs, raw values, times as text, bearer auth.
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == f"/v4/spreadsheets/{SHEET_ID}/values:batchGet"
    assert request.url.params.get_list("ranges") == ["PRODUCTS", "STORES", "FAQ", "KIOSKS"]
    assert request.url.params["valueRenderOption"] == "UNFORMATTED_VALUE"
    assert request.url.params["dateTimeRenderOption"] == "FORMATTED_STRING"
    assert request.headers["Authorization"] == "Bearer test-token"


def test_rows_to_dicts_pads_short_rows() -> None:
    rows = rows_to_dicts([["a", "b", "c"], [1], [1, 2, 3]])
    assert rows == [{"a": 1, "b": "", "c": ""}, {"a": 1, "b": 2, "c": 3}]
    assert rows_to_dicts([]) == []


async def test_api_error_is_reported_with_google_message() -> None:
    body = {"error": {"code": 403, "message": "Google Sheets API has not been used..."}}
    source = _source(lambda _: httpx.Response(403, json=body))
    with pytest.raises(DataSourceError, match="403: Google Sheets API has not been used"):
        await source.load()


async def test_missing_tab_is_reported() -> None:
    body = {"error": {"code": 400, "message": "Unable to parse range: FAQ"}}
    with pytest.raises(DataSourceError, match="Unable to parse range: FAQ"):
        await _source(lambda _: httpx.Response(400, json=body)).load()


async def test_network_error_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(DataSourceError, match="ConnectTimeout"):
        await _source(handler).load()


async def test_unexpected_tab_count() -> None:
    body = json.dumps({"valueRanges": [{"values": [["product_id"]]}]})
    with pytest.raises(DataSourceError, match="expected 4 tabs"):
        await _source(lambda _: httpx.Response(200, text=body)).load()


def test_sheet_id_required() -> None:
    with pytest.raises(ValueError, match="GOOGLE_SHEETS_ID"):
        GoogleSheetsDataSource(
            spreadsheet_id="",
            tokens=FakeTokens(),
            http_client=httpx.AsyncClient(),
            timeout_seconds=5,
        )


async def test_sheet_without_kiosks_tab_still_loads() -> None:
    requests: list[httpx.Request] = []
    body = _sample_response()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "KIOSKS" in request.url.params.get_list("ranges"):
            error = {"error": {"code": 400, "message": "Unable to parse range: KIOSKS"}}
            return httpx.Response(400, json=error)
        return httpx.Response(200, json={"valueRanges": body["valueRanges"][:3]})

    data = await _source(handler).load()
    assert len(requests) == 2  # one rejected attempt with KIOSKS, one without
    assert data.counts()["products"] == 37 and data.kiosks == {}
