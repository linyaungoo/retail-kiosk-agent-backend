import os
from collections.abc import Callable
from pathlib import Path

# Hermetic tests: environment variables override .env, so the app under test uses the
# local CSV data even when .env points at Google Sheets. Must run before app imports.
os.environ["BUSINESS_DATA_SOURCE"] = "mock"
os.environ["APP_ENV"] = "development"
os.environ["OPENAI_WARMUP"] = "false"  # no network calls when tests start the app
os.environ.pop("ADMIN_API_KEY", None)

import pytest  # noqa: E402

from app.data.business_data import BusinessData
from app.data.csv_source import load_business_data_from_csv
from app.data.repository import InMemoryBusinessRepository
from app.models.kiosk import KioskContext, Language
from app.tools.context import ToolContext

# Tests use the same sample data that gets imported into the Google Sheet.
SAMPLE_DATA_DIR = Path(__file__).resolve().parent.parent / "sample_data"

MakeContext = Callable[..., ToolContext]


@pytest.fixture(scope="session")
def business_data() -> BusinessData:
    return load_business_data_from_csv(SAMPLE_DATA_DIR)


@pytest.fixture
def repository(business_data: BusinessData) -> InMemoryBusinessRepository:
    return InMemoryBusinessRepository.from_data(business_data)


@pytest.fixture
def make_ctx(repository: InMemoryBusinessRepository) -> MakeContext:
    def _make(
        store_id: str = "STORE-001",
        language: Language = "en-US",
        organization_id: str = "ORG-001",
    ) -> ToolContext:
        kiosk = KioskContext(
            organization_id=organization_id,
            store_id=store_id,
            kiosk_id="KIOSK-001",
            session_id="TEST-session",
            language=language,
        )
        return ToolContext(kiosk=kiosk, repository=repository)

    return _make
