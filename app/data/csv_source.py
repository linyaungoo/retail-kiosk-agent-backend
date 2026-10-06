"""Mock data source: the CSV files in sample_data/ (same layout as the Google Sheet)."""

import asyncio
import csv
from pathlib import Path

from app.data.business_data import (
    FAQ_TABLE,
    KIOSKS_TABLE,
    PRODUCTS_TABLE,
    STORES_TABLE,
    BusinessData,
)
from app.data.sources import DataSourceError


def _read_csv(path: Path) -> list[dict[str | None, str]]:
    # utf-8-sig tolerates the BOM that Excel adds when saving CSV.
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_business_data_from_csv(directory: str | Path) -> BusinessData:
    """Load PRODUCTS.csv, STORES.csv, FAQ.csv and (optional) KIOSKS.csv.
    Call once at startup, not per request."""
    base = Path(directory)
    kiosks = base / f"{KIOSKS_TABLE}.csv"
    return BusinessData.from_rows(
        products=_read_csv(base / f"{PRODUCTS_TABLE}.csv"),
        stores=_read_csv(base / f"{STORES_TABLE}.csv"),
        faqs=_read_csv(base / f"{FAQ_TABLE}.csv"),
        kiosks=_read_csv(kiosks) if kiosks.exists() else [],
    )


class CsvDataSource:
    """BusinessDataSource backed by local CSV files (development / tests)."""

    name = "mock"

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)

    async def load(self) -> BusinessData:
        try:
            return await asyncio.to_thread(load_business_data_from_csv, self._directory)
        except OSError as exc:
            raise DataSourceError(f"cannot read CSV data in {self._directory}: {exc}") from exc
