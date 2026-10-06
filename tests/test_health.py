from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_response_has_generated_request_id() -> None:
    response = client.get("/health")
    request_id = response.headers.get("x-request-id")
    assert request_id and len(request_id) == 32


def test_valid_incoming_request_id_is_propagated() -> None:
    response = client.get("/health", headers={"X-Request-ID": "kiosk-req-123"})
    assert response.headers["x-request-id"] == "kiosk-req-123"


def test_malformed_incoming_request_id_is_replaced() -> None:
    response = client.get("/health", headers={"X-Request-ID": "bad id with spaces"})
    assert response.headers["x-request-id"] != "bad id with spaces"


def test_unknown_route_returns_structured_error() -> None:
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"
