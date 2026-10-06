"""POST /api/agent with the agent service stubbed (no OpenAI calls)."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import get_settings
from app.dependencies import get_agent_service
from app.main import app
from app.models.tools import ToolAction
from app.services.agent_service import AgentReply
from app.services.agent_tools import ToolCallRecord
from app.tools.context import ToolContext

VALID: dict[str, str] = {
    "organization_id": "ORG-001",
    "store_id": "STORE-001",
    "kiosk_id": "KIOSK-001",
    "session_id": "MOBILE-abc123",
    "language": "my-MM",
    "message": "Coca Cola ဘယ်မှာရှိလဲ",
}


class StubAgent:
    def __init__(self) -> None:
        self.seen: list[ToolContext] = []

    async def reply(self, tools: ToolContext, message: str) -> AgentReply:
        self.seen.append(tools)
        record = ToolCallRecord(
            name="search_product",
            arguments={"query": "Coca Cola"},
            action=ToolAction.PRODUCT_LOCATION,
            data={"found": True, "products": [{"product_id": "P001", "aisle": "A03"}]},
            duration_ms=1,
        )
        return AgentReply(
            answer="Coca Cola 1L ကို Aisle A03 မှာ ရှာနိုင်ပါတယ်။",
            action=ToolAction.PRODUCT_LOCATION,
            data=record.data,
            tool_calls=[record],
            agent_ms=850,
            tool_ms=1,
            llm_calls=2,
            input_tokens=100,
            output_tokens=20,
        )


@pytest.fixture
def stub() -> StubAgent:
    return StubAgent()


@pytest.fixture
def client(stub: StubAgent) -> Iterator[TestClient]:
    app.dependency_overrides[get_agent_service] = lambda: stub
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_success_response(client: TestClient, stub: StubAgent) -> None:
    response = client.post("/api/agent", json=VALID)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["session_id"] == "MOBILE-abc123" and body["language"] == "my-MM"
    assert body["action"] == "PRODUCT_LOCATION"
    assert body["data"]["products"][0]["aisle"] == "A03"
    assert body["timing"]["agent_ms"] == 850 and body["timing"]["total_ms"] >= 0
    assert "stt_ms" not in body["timing"]
    # Trusted context reaches the tools; the message never chooses the store.
    assert stub.seen[0].kiosk.store_id == "STORE-001"


def test_invalid_language(client: TestClient) -> None:
    response = client.post("/api/agent", json={**VALID, "language": "th-TH"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_LANGUAGE"


def test_missing_session_id(client: TestClient) -> None:
    body = {k: v for k, v in VALID.items() if k != "session_id"}
    response = client.post("/api/agent", json=body)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR" and "session_id" in error["message"]


@pytest.mark.parametrize("message", ["", "x" * 1001])
def test_message_length_validated(client: TestClient, message: str) -> None:
    response = client.post("/api/agent", json={**VALID, "message": message})
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_malformed_ids_rejected(client: TestClient) -> None:
    response = client.post("/api/agent", json={**VALID, "store_id": "STORE 001; DROP"})
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "overrides",
    [
        {"store_id": "STORE-999"},  # unknown
        {"store_id": "STORE-004"},  # inactive
        {"store_id": "STORE-003"},  # belongs to ORG-002
    ],
)
def test_invalid_kiosk_store(client: TestClient, overrides: dict[str, str]) -> None:
    response = client.post("/api/agent", json={**VALID, **overrides})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INVALID_KIOSK"


def test_agent_not_configured(stub: StubAgent) -> None:
    with TestClient(app) as test_client:
        test_client.app.state.agent_service = None  # type: ignore[attr-defined]
        response = test_client.post("/api/agent", json=VALID)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AGENT_NOT_CONFIGURED"


def test_tool_calls_hidden_outside_development(client: TestClient) -> None:
    settings = get_settings().model_copy(update={"app_env": "production"})
    app.dependency_overrides[get_settings] = lambda: settings
    body = client.post("/api/agent", json=VALID).json()
    assert "tool_calls" not in body


class TestKioskKey:
    @pytest.fixture(autouse=True)
    def _enable_auth(self, client: TestClient) -> None:
        settings = get_settings().model_copy(
            update={"kiosk_auth_enabled": True, "kiosk_api_keys": SecretStr("key-1, key-2")}
        )
        app.dependency_overrides[get_settings] = lambda: settings

    @pytest.mark.parametrize("headers", [{}, {"X-Kiosk-Key": "wrong"}])
    def test_rejected(self, client: TestClient, headers: dict[str, Any]) -> None:
        response = client.post("/api/agent", json=VALID, headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"

    def test_accepted(self, client: TestClient) -> None:
        response = client.post("/api/agent", json=VALID, headers={"X-Kiosk-Key": "key-2"})
        assert response.status_code == 200
