"""/admin cache endpoints and DATA_UNAVAILABLE behaviour."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import get_settings
from app.data.business_data import BusinessData
from app.data.repository import InMemoryBusinessRepository
from app.main import app
from app.services.cache_service import BusinessDataCache


class FailingSource:
    name = "google_sheets"

    async def load(self) -> BusinessData:
        raise ConnectionError("403: Google Sheets API has not been used in project")


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _override_settings(**updates: object) -> None:
    settings = get_settings().model_copy(update=updates)
    app.dependency_overrides[get_settings] = lambda: settings


def test_cache_status(client: TestClient) -> None:
    body = client.get("/admin/cache").json()
    assert body["success"] is True and body["source"] == "mock" and body["loaded"] is True
    assert body["counts"] == {"products": 37, "stores": 3, "faqs": 11}


def test_manual_refresh(client: TestClient) -> None:
    response = client.post("/admin/cache/refresh")
    assert response.status_code == 200
    assert response.json()["last_refresh_ms"] is not None


def test_refresh_failure_reports_source_error(client: TestClient) -> None:
    client.app.state.cache = BusinessDataCache(FailingSource(), ttl_seconds=300)  # type: ignore[attr-defined]
    response = client.post("/admin/cache/refresh")
    assert response.status_code == 502
    error = response.json()["error"]
    assert (
        error["code"] == "DATA_SOURCE_ERROR" and "Sheets API has not been used" in error["message"]
    )


def test_admin_hidden_outside_development_without_key(client: TestClient) -> None:
    _override_settings(app_env="production")
    assert client.get("/admin/cache").status_code == 404


def test_admin_key_required_when_configured(client: TestClient) -> None:
    _override_settings(app_env="production", admin_api_key=SecretStr("adm-1"))
    assert client.get("/admin/cache").status_code == 401
    assert client.get("/admin/cache", headers={"X-Admin-Key": "nope"}).status_code == 401
    assert client.get("/admin/cache", headers={"X-Admin-Key": "adm-1"}).status_code == 200


def test_agent_returns_data_unavailable_when_never_loaded(client: TestClient) -> None:
    cache = BusinessDataCache(FailingSource(), ttl_seconds=300)
    client.app.state.repository = InMemoryBusinessRepository(cache.get)  # type: ignore[attr-defined]
    response = client.post(
        "/api/agent",
        json={
            "organization_id": "ORG-001",
            "store_id": "STORE-001",
            "kiosk_id": "KIOSK-001",
            "session_id": "MOBILE-abc",
            "language": "en-US",
            "message": "Where is Coca Cola?",
        },
    )
    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "DATA_UNAVAILABLE",
        "message": "Store information is temporarily unavailable.",
    }
