from datetime import time

import pytest

from app.data.business_data import BusinessData
from app.data.parsing import RowError, parse_bool, parse_price, parse_time

PRODUCT_HEADER = {
    "product_id": "P1",
    "sku": "S1",
    "barcode": "1",
    "name": "Thing",
    "brand": "B",
    "category": "C",
    "store_id": "STORE-001",
    "zone": "Z",
    "aisle": "A1",
    "rack": "R1",
    "shelf": "S1",
    "availability": "AVAILABLE",
    "price": "100",
    "active": "TRUE",
}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("TRUE", True), ("true", True), ("Yes", True), ("1", True), ("FALSE", False), ("", None)],
)
def test_parse_bool(raw: str, expected: bool | None) -> None:
    assert parse_bool(raw) is expected


def test_parse_bool_invalid() -> None:
    with pytest.raises(RowError):
        parse_bool("maybe")


@pytest.mark.parametrize(
    ("raw", "expected"), [("1800", 1800), ("1,800", 1800), ("Ks 1,800", 1800), ("", None)]
)
def test_parse_price(raw: str, expected: int | None) -> None:
    assert parse_price(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("08:00", time(8)), ("8:00", time(8)), ("21:00:00", time(21)), ("9:00 PM", time(21))],
)
def test_parse_time(raw: str, expected: time) -> None:
    assert parse_time(raw) == expected


def test_bad_rows_skipped_good_rows_kept() -> None:
    rows = [
        PRODUCT_HEADER,
        {**PRODUCT_HEADER, "product_id": "P2", "name": ""},  # missing name
        {**PRODUCT_HEADER, "product_id": "P3", "availability": "SOMETIMES"},  # bad enum
        {**PRODUCT_HEADER, "product_id": "P4", "active": "FALSE"},  # inactive
        {**PRODUCT_HEADER, "name": "Duplicate"},  # duplicate (P1, STORE-001)
        {key: "" for key in PRODUCT_HEADER},  # blank line
    ]
    data = BusinessData.from_rows(products=rows, stores=[], faqs=[])
    products = data.products_by_store["STORE-001"]
    assert [p.product.product_id for p in products] == ["P1"]
    assert products[0].product.name == "Thing"


def test_headers_are_case_and_space_insensitive() -> None:
    row = {f" {key.upper()} ": f" {value} " for key, value in PRODUCT_HEADER.items()}
    data = BusinessData.from_rows(products=[row], stores=[], faqs=[])
    assert data.products_by_store["STORE-001"][0].product.aisle == "A1"


def test_sample_data_counts(business_data: BusinessData) -> None:
    # 38 product rows, 1 inactive; 4 stores, 1 inactive; 12 FAQ, 1 inactive.
    assert business_data.counts() == {"products": 37, "stores": 3, "faqs": 11}
