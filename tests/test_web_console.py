"""Browser test console (web/ at /console) and CORS for cross-origin testers."""

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app

ORIGIN = "http://localhost:5500"
MakeClient = Callable[..., TestClient]


@pytest.fixture
def make_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[MakeClient]:
    def _make(**env: str) -> TestClient:
        # Explicit values so a developer's .env can't change the outcome.
        defaults = {"APP_ENV": "development", "WEB_CONSOLE_ENABLED": "", "CORS_ALLOWED_ORIGINS": ""}
        for key, value in {**defaults, **env}.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        return TestClient(create_app())

    yield _make
    get_settings.cache_clear()


def test_console_served_in_development(make_client: MakeClient) -> None:
    client = make_client()
    page = client.get("/console/")
    assert page.status_code == 200
    assert "Kiosk API Console" in page.text
    assert client.get("/console", follow_redirects=False).status_code in (307, 308)
    script = client.get("/console/js/main.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]


def test_console_off_in_production(make_client: MakeClient) -> None:
    assert make_client(APP_ENV="production").get("/console/").status_code == 404


def test_console_can_be_enabled_for_staging(make_client: MakeClient) -> None:
    client = make_client(APP_ENV="production", WEB_CONSOLE_ENABLED="true")
    assert client.get("/console/").status_code == 200


def test_no_cors_by_default(make_client: MakeClient) -> None:
    response = make_client().get("/health", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_allows_kiosk_headers(make_client: MakeClient) -> None:
    client = make_client(CORS_ALLOWED_ORIGINS=f"{ORIGIN}/, https://console.example.com")
    response = client.options(
        "/api/realtime/session",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-kiosk-key",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "x-kiosk-key" in response.headers["access-control-allow-headers"].lower()


def test_cors_errors_are_readable_and_voice_headers_exposed(make_client: MakeClient) -> None:
    client = make_client(
        CORS_ALLOWED_ORIGINS=ORIGIN, KIOSK_AUTH_ENABLED="true", KIOSK_API_KEYS="KIOSK-001:k1"
    )
    response = client.get("/api/kiosk/config", headers={"Origin": ORIGIN})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == ORIGIN
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "x-kiosk-response" in exposed and "x-request-id" in exposed


def test_unlisted_origin_gets_no_cors(make_client: MakeClient) -> None:
    client = make_client(CORS_ALLOWED_ORIGINS=ORIGIN)
    response = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_settings_parsing() -> None:
    settings = Settings(_env_file=None, cors_allowed_origins=" http://a:1/ ,, https://b ")
    assert settings.cors_origins() == ["http://a:1", "https://b"]
    assert Settings(_env_file=None, app_env="staging").console_enabled() is False
    assert Settings(_env_file=None, app_env="development").console_enabled() is True
    turned_off = Settings(_env_file=None, app_env="development", web_console_enabled=False)
    assert turned_off.console_enabled() is False
