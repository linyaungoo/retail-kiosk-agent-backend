"""Production behaviour: readiness, hidden docs, Cloud Logging format."""

import json
import logging
import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app, create_app
from app.utils.logging import JsonFormatter, request_id_var


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_ready_when_everything_configured(client: TestClient) -> None:
    state = client.app.state  # type: ignore[attr-defined]
    state.stt = state.agent_service = state.tts = object()
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"business_data": True, "stt": True, "agent": True, "tts": True},
    }


def test_not_ready_reports_what_is_missing(client: TestClient) -> None:
    state = client.app.state  # type: ignore[attr-defined]
    state.stt = state.tts = object()
    state.agent_service = None
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["agent"] is False


def test_docs_hidden_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        prod = TestClient(create_app())
        assert prod.get("/docs").status_code == 404
        assert prod.get("/openapi.json").status_code == 404
        assert prod.get("/health").json() == {"status": "ok"}
    finally:
        get_settings.cache_clear()
    with TestClient(app) as dev:
        assert dev.get("/openapi.json").status_code == 200


def test_log_lines_use_cloud_logging_fields() -> None:
    record = logging.makeLogRecord(
        {"name": "app.test", "levelno": logging.ERROR, "levelname": "ERROR", "msg": "boom"}
    )
    record.stt_ms = 950
    try:
        raise ValueError("bad")
    except ValueError:
        record.exc_info = sys.exc_info()
    token = request_id_var.set("req-1")
    try:
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert line["severity"] == "ERROR" and line["message"] == "boom"
    assert line["request_id"] == "req-1" and line["stt_ms"] == 950
    assert "ValueError: bad" in line["stack_trace"]
    assert "time" in line


@pytest.mark.parametrize("raw", ["", "   ", "\n"])
def test_empty_secrets_mean_not_set(raw: str) -> None:
    settings = Settings(_env_file=None, admin_api_key=raw, openai_api_key=raw)
    assert settings.admin_api_key is None and settings.openai_api_key is None


def test_secrets_are_trimmed() -> None:
    settings = Settings(_env_file=None, openai_api_key="sk-abc\r\n", kiosk_api_keys=" k1, k2 \n")
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-abc"
    assert settings.kiosk_key_set() == {"k1", "k2"}


def test_empty_admin_key_keeps_admin_open_in_development(client: TestClient) -> None:
    settings = Settings(_env_file=None, app_env="development", admin_api_key="")
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        assert client.get("/admin/cache").status_code == 200
    finally:
        app.dependency_overrides.clear()
