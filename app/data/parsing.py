"""Convert raw worksheet rows (header -> string) into validated domain models.

Shared by every data source (CSV now, Google Sheets later) so the same rules apply
wherever the data comes from. A bad row is skipped and logged; it never takes down
the whole catalog.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, time
from typing import Any

from pydantic import ValidationError

from app.models.business import Availability, FaqEntry, FaqScope, Product, Store
from app.utils.logging import get_logger

logger = get_logger(__name__)

# A row as delivered by a source (csv.DictReader, Sheets API, ...): header -> cell.
RawRow = Mapping[Any, object]
Row = Mapping[str, str]

_TRUE = frozenset({"true", "yes", "y", "t", "1"})
_FALSE = frozenset({"false", "no", "n", "f", "0"})
_TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p")
_KEYWORD_SPLIT = re.compile(r"[;\n]+")


class RowError(ValueError):
    pass


def normalize_row(row: RawRow) -> dict[str, str]:
    """Lower-case/strip headers and strip values. Ignores columns without a header."""
    return {
        key.strip().lower(): "" if value is None else str(value).strip()
        for key, value in row.items()
        if isinstance(key, str) and key.strip()
    }


def _required(row: Row, field: str) -> str:
    value = row.get(field, "")
    if not value:
        raise RowError(f"missing {field}")
    return value


def parse_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise RowError(f"invalid boolean {value!r}")


def _flag(row: Row, field: str, *, default: bool) -> bool:
    parsed = parse_bool(row.get(field, ""))
    return default if parsed is None else parsed


def parse_price(value: str) -> int | None:
    cleaned = re.sub(r"[^\d.]", "", value)
    if not cleaned:
        return None
    try:
        return round(float(cleaned))
    except ValueError:
        raise RowError(f"invalid price {value!r}") from None


def parse_time(value: str) -> time | None:
    normalized = value.strip().upper()
    if not normalized:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(normalized, fmt).time()
        except ValueError:
            continue
    raise RowError(f"invalid time {value!r}")


def _availability(value: str) -> Availability:
    normalized = value.strip().upper().replace(" ", "_")
    try:
        return Availability(normalized)
    except ValueError:
        raise RowError(f"invalid availability {value!r}") from None


def parse_product(row: Row) -> Product:
    return Product(
        product_id=_required(row, "product_id"),
        sku=row.get("sku", ""),
        barcode=row.get("barcode", ""),
        name=_required(row, "name"),
        brand=row.get("brand", ""),
        category=row.get("category", ""),
        store_id=_required(row, "store_id"),
        zone=row.get("zone", ""),
        aisle=row.get("aisle", ""),
        rack=row.get("rack", ""),
        shelf=row.get("shelf", ""),
        availability=_availability(row.get("availability", "")),
        price=parse_price(row.get("price", "")),
        active=_flag(row, "active", default=True),
    )


def parse_store(row: Row) -> Store:
    return Store(
        store_id=_required(row, "store_id"),
        organization_id=_required(row, "organization_id"),
        name=_required(row, "name"),
        opening_time=parse_time(row.get("opening_time", "")),
        closing_time=parse_time(row.get("closing_time", "")),
        customer_service_location=row.get("customer_service_location", ""),
        parking_available=parse_bool(row.get("parking_available", "")),
        restroom_available=parse_bool(row.get("restroom_available", "")),
        active=_flag(row, "active", default=True),
    )


def parse_faq(row: Row) -> FaqEntry:
    scope_value = _required(row, "scope").upper()
    try:
        scope = FaqScope(scope_value)
    except ValueError:
        raise RowError(f"invalid scope {scope_value!r}") from None
    store_id = row.get("store_id", "") or None
    if scope is FaqScope.STORE and store_id is None:
        raise RowError("STORE scope requires store_id")
    answer_en, answer_mm = row.get("answer_en", ""), row.get("answer_mm", "")
    if not answer_en and not answer_mm:
        raise RowError("missing answer_en and answer_mm")
    keywords = tuple(k.strip() for k in _KEYWORD_SPLIT.split(row.get("keywords", "")) if k.strip())
    return FaqEntry(
        faq_id=_required(row, "faq_id"),
        scope=scope,
        store_id=store_id if scope is FaqScope.STORE else None,
        category=_required(row, "category").upper(),
        question=_required(row, "question"),
        answer_en=answer_en,
        answer_mm=answer_mm,
        keywords=keywords,
        active=_flag(row, "active", default=True),
    )


def parse_rows[T](rows: Iterable[RawRow], parser: Callable[[Row], T], table: str) -> list[T]:
    parsed: list[T] = []
    for line, raw in enumerate(rows, start=2):  # line 1 is the header
        row = normalize_row(raw)
        if not any(row.values()):
            continue
        try:
            parsed.append(parser(row))
        except RowError as exc:
            logger.warning("row_skipped", extra={"table": table, "row": line, "reason": str(exc)})
        except ValidationError as exc:
            reason = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            logger.warning("row_skipped", extra={"table": table, "row": line, "reason": reason})
    return parsed
