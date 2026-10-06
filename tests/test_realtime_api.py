"""/api/realtime/* and /api/kiosk/config over HTTP (simulated OpenAI, fake sideband)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.main import app
from app.realtime.service import RealtimeService
from tests.realtime_fakes import SDP_ANSWER, SDP_OFFER, FakeConnection, FakeOpenAI

BODY = {"kiosk_id": "KIOSK-001", "session_id": "SESSION-abc", "language": "my-MM", "sdp": SDP_OFFER}


@pytest.fixture
def fake_openai() -> FakeOpenAI:
    return FakeOpenAI()


@pytest.fixture
def client(fake_openai: FakeOpenAI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        state = test_client.app.state  # type: ignore[attr-defined]

        async def connect(call_id: str) -> FakeConnection:
            return FakeConnection()

        async def vocabulary() -> tuple[str, ...]:
            return ()

        settings = Settings(_env_file=None, openai_realtime_model="gpt-realtime-test")
        state.realtime = RealtimeService(
            fake_openai.client(), settings, state.repository, vocabulary, connect=connect
        )
        yield test_client
        app.dependency_overrides.clear()


def test_create_session_returns_only_client_safe_data(
    client: TestClient, fake_openai: FakeOpenAI
) -> None:
    response = client.post("/api/realtime/session", json=BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["sdp"] == SDP_ANSWER and body["realtime_session_id"] == "rtc_test1"
    assert body["greeting_event"]["response"]["tool_choice"] == "none"
    assert "instructions" not in body and "tools" not in body
    text = response.text
    assert "sk-" not in text  # no secret of any kind
    # The session OpenAI received was built by the backend, for the trusted store.
    assert "LIVE CONVERSATION" in fake_openai.created[0]["instructions"]


def test_client_store_id_ignored_for_registered_kiosk(
    client: TestClient, fake_openai: FakeOpenAI
) -> None:
    response = client.post("/api/realtime/session", json={**BODY, "store_id": "STORE-002"})
    assert response.status_code == 200
    service = client.app.state.realtime  # type: ignore[attr-defined]
    call = service.get_call(response.json()["realtime_session_id"])
    assert call.kiosk.store_id == "STORE-001"  # from the KIOSKS registry


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"sdp": "not an sdp offer"}, "VALIDATION_ERROR"),
        ({"language": "th-TH"}, "INVALID_LANGUAGE"),
        ({"kiosk_id": "KIOSK-NOPE", "store_id": "STORE-999"}, "INVALID_KIOSK"),
    ],
)
def test_create_session_validation(client: TestClient, change: dict[str, str], code: str) -> None:
    response = client.post("/api/realtime/session", json={**BODY, **change})
    assert response.json()["error"]["code"] == code


def test_end_session(client: TestClient, fake_openai: FakeOpenAI) -> None:
    call_id = client.post("/api/realtime/session", json=BODY).json()["realtime_session_id"]
    response = client.post(f"/api/realtime/session/{call_id}/end", json={"kiosk_id": "KIOSK-001"})
    assert response.json() == {"success": True}
    assert fake_openai.hung_up == [call_id]
    again = client.post(f"/api/realtime/session/{call_id}/end", json={"kiosk_id": "KIOSK-001"})
    assert again.status_code == 404


def test_other_kiosk_cannot_touch_session(client: TestClient) -> None:
    call_id = client.post("/api/realtime/session", json=BODY).json()["realtime_session_id"]
    for action in ("end", "metrics"):
        response = client.post(
            f"/api/realtime/session/{call_id}/{action}", json={"kiosk_id": "KIOSK-002"}
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "REALTIME_SESSION_NOT_FOUND"


def test_client_metrics(client: TestClient) -> None:
    call_id = client.post("/api/realtime/session", json=BODY).json()["realtime_session_id"]
    ok = client.post(
        f"/api/realtime/session/{call_id}/metrics",
        json={"kiosk_id": "KIOSK-001", "time_to_first_audio_ms": 900, "reconnect_count": 0},
    )
    assert ok.json() == {"success": True}
    service = client.app.state.realtime  # type: ignore[attr-defined]
    assert service.get_call(call_id).metrics.client["time_to_first_audio_ms"] == 900
    unknown = client.post(
        f"/api/realtime/session/{call_id}/metrics",
        json={"kiosk_id": "KIOSK-001", "api_key": "x"},
    )
    assert unknown.status_code == 422


def test_realtime_not_configured(client: TestClient) -> None:
    client.app.state.realtime = None  # type: ignore[attr-defined]
    response = client.post("/api/realtime/session", json=BODY)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "REALTIME_NOT_CONFIGURED"


def test_kiosk_config_modes(client: TestClient) -> None:
    def config(**updates: object) -> dict[str, object]:
        settings = get_settings().model_copy(update=updates)
        app.dependency_overrides[get_settings] = lambda: settings
        result: dict[str, object] = client.get("/api/kiosk/config").json()
        return result

    assert config(voice_mode="realtime")["voice_mode"] == "realtime"
    assert config(voice_mode="chained")["voice_mode"] == "chained"
    client.app.state.realtime = None  # type: ignore[attr-defined]
    fallback = config(voice_mode="realtime")
    assert fallback["voice_mode"] == "chained" and fallback["realtime_available"] is False


def test_bound_key_cannot_open_session_for_other_kiosk(client: TestClient) -> None:
    settings = get_settings().model_copy(
        update={"kiosk_auth_enabled": True, "kiosk_api_keys": SecretStr("KIOSK-002:k2")}
    )
    app.dependency_overrides[get_settings] = lambda: settings
    response = client.post("/api/realtime/session", json=BODY, headers={"X-Kiosk-Key": "k2"})
    assert response.status_code == 403
