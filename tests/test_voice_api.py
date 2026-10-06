"""POST /api/kiosk/voice with stub STT/agent and the mock TTS (no OpenAI calls)."""

import base64
import json
from collections.abc import Iterator, Sequence
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.errors import AppError, ErrorCode
from app.main import app
from app.models.kiosk import Language
from app.models.tools import ToolAction
from app.services.agent_service import AgentReply
from app.services.agent_tools import ToolCallRecord
from app.services.stt_service import TranscriptionResult
from app.services.tts_service import LanguageRoutedTTS, MockTextToSpeech, TTSError, TTSStream
from app.tools.context import ToolContext

FORM = {
    "organization_id": "ORG-001",
    "store_id": "STORE-001",
    "kiosk_id": "KIOSK-001",
    "session_id": "MOBILE-abc123",
    "language": "my-MM",
}
WAV = ("speech.wav", b"RIFF....WAVEfmt fake audio", "audio/wav")
ANSWER = "Coca Cola 1L ကို Aisle A03၊ Rack R02၊ Shelf S02 မှာ ရှာနိုင်ပါတယ်။"


class StubSTT:
    name = "stub"

    def __init__(self) -> None:
        self.text = "Coca Cola ဘယ်မှာရှိလဲ"
        self.calls = 0

    async def transcribe(
        self,
        audio: bytes,
        filename: str,
        language: Language | None = None,
        *,
        content_type: str | None = None,
        vocabulary: Sequence[str] = (),
    ) -> TranscriptionResult:
        self.calls += 1
        return TranscriptionResult(self.text, language, 950, self.name, "stub")


class StubAgent:
    def __init__(self) -> None:
        self.seen: list[tuple[ToolContext, str]] = []
        self.error: AppError | None = None

    async def reply(self, tools: ToolContext, message: str) -> AgentReply:
        self.seen.append((tools, message))
        if self.error:
            raise self.error
        record = ToolCallRecord(
            name="search_product",
            arguments={"query": "Coca Cola"},
            action=ToolAction.PRODUCT_LOCATION,
            data={"found": True, "products": [{"product_id": "P001", "aisle": "A03"}]},
            duration_ms=1,
        )
        return AgentReply(
            answer=ANSWER,
            action=ToolAction.PRODUCT_LOCATION,
            data=record.data,
            tool_calls=[record],
            agent_ms=820,
            tool_ms=1,
            llm_calls=2,
            input_tokens=100,
            output_tokens=20,
        )


class FailingTTS:
    name = "failing"

    async def stream(self, text: str, language: Language) -> TTSStream:
        raise TTSError()


@pytest.fixture
def stt() -> StubSTT:
    return StubSTT()


@pytest.fixture
def agent() -> StubAgent:
    return StubAgent()


@pytest.fixture
def client(stt: StubSTT, agent: StubAgent) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        state = test_client.app.state  # type: ignore[attr-defined]
        state.stt = stt
        state.agent_service = agent
        mock = MockTextToSpeech()
        state.tts = LanguageRoutedTTS({"my-MM": mock, "en-US": mock})
        yield test_client


def _metadata(response: Any) -> dict[str, Any]:
    decoded: dict[str, Any] = json.loads(
        base64.urlsafe_b64decode(response.headers["x-kiosk-response"])
    )
    return decoded


def test_streams_audio_with_metadata_header(client: TestClient, agent: StubAgent) -> None:
    response = client.post("/api/kiosk/voice", data=FORM, files={"audio": WAV})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content.startswith(b"RIFF")
    assert response.headers["x-action"] == "PRODUCT_LOCATION"

    meta = _metadata(response)
    assert meta["success"] is True and meta["session_id"] == "MOBILE-abc123"
    assert meta["transcript"] == "Coca Cola ဘယ်မှာရှိလဲ"
    assert meta["answer"] == ANSWER and meta["action"] == "PRODUCT_LOCATION"
    assert meta["data"]["products"][0]["aisle"] == "A03"
    assert meta["audio_format"] == "wav"
    timing = meta["timing"]
    assert timing["stt_ms"] == 950 and timing["agent_ms"] == 820 and timing["tool_ms"] == 1
    assert timing["tts_first_byte_ms"] >= 0 and timing["total_ms"] >= 0

    # The agent got the transcript and the trusted kiosk context.
    tools, message = agent.seen[0]
    assert message == "Coca Cola ဘယ်မှာရှိလဲ"
    assert tools.kiosk.store_id == "STORE-001" and tools.kiosk.session_id == "MOBILE-abc123"


def test_json_mode(client: TestClient) -> None:
    response = client.post("/api/kiosk/voice?response=json", data=FORM, files={"audio": WAV})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == ANSWER and body["transcript"] == "Coca Cola ဘယ်မှာရှိလဲ"
    assert base64.b64decode(body["audio_base64"]).startswith(b"RIFF")
    assert body["timing"]["tts_ms"] >= body["timing"]["tts_first_byte_ms"]


def test_large_data_dropped_from_header(client: TestClient, agent: StubAgent) -> None:
    original = agent.reply

    async def big_reply(tools: ToolContext, message: str) -> AgentReply:
        reply = await original(tools, message)
        return replace(reply, data={"products": [{"name": "x" * 500} for _ in range(20)]})

    agent.reply = big_reply  # type: ignore[method-assign]
    response = client.post("/api/kiosk/voice", data=FORM, files={"audio": WAV})
    meta = _metadata(response)
    assert meta["data"] == {} and meta["data_truncated"] is True
    assert len(response.headers["x-kiosk-response"]) <= 7000


def test_invalid_kiosk_rejected_before_stt(client: TestClient, stt: StubSTT) -> None:
    form = {**FORM, "kiosk_id": "KIOSK-UNKNOWN", "store_id": "STORE-999"}
    response = client.post("/api/kiosk/voice", data=form, files={"audio": WAV})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INVALID_KIOSK"
    assert stt.calls == 0  # no STT cost for a misconfigured kiosk


def test_no_speech(client: TestClient, stt: StubSTT, agent: StubAgent) -> None:
    stt.text = "..."
    response = client.post("/api/kiosk/voice", data=FORM, files={"audio": WAV})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_TRANSCRIPT"
    assert agent.seen == []


def test_agent_failure_includes_transcript(client: TestClient, agent: StubAgent) -> None:
    agent.error = AppError(ErrorCode.AGENT_TIMEOUT, "The assistant took too long to respond.", 504)
    response = client.post("/api/kiosk/voice", data=FORM, files={"audio": WAV})
    assert response.status_code == 504
    body = response.json()
    assert body["error"]["code"] == "AGENT_TIMEOUT"
    assert body["transcript"] == "Coca Cola ဘယ်မှာရှိလဲ"


def test_tts_failure_still_returns_answer_text(client: TestClient) -> None:
    client.app.state.tts = FailingTTS()  # type: ignore[attr-defined]
    response = client.post("/api/kiosk/voice", data=FORM, files={"audio": WAV})
    assert response.status_code == 502
    body = response.json()
    assert body["success"] is False and body["error"]["code"] == "TTS_FAILED"
    assert body["answer"] == ANSWER and body["action"] == "PRODUCT_LOCATION"
    assert body["transcript"] == "Coca Cola ဘယ်မှာရှိလဲ"
    assert body["data"]["products"][0]["aisle"] == "A03"


def test_invalid_language(client: TestClient) -> None:
    response = client.post(
        "/api/kiosk/voice", data={**FORM, "language": "zh-CN"}, files={"audio": WAV}
    )
    assert response.json()["error"]["code"] == "INVALID_LANGUAGE"


@pytest.mark.parametrize("field", ["kiosk_id", "session_id"])
def test_required_ids(client: TestClient, field: str) -> None:
    data = {k: v for k, v in FORM.items() if k != field}
    response = client.post("/api/kiosk/voice", data=data, files={"audio": WAV})
    assert response.status_code == 422
    assert field in response.json()["error"]["message"]


def test_unsupported_audio(client: TestClient, stt: StubSTT) -> None:
    response = client.post(
        "/api/kiosk/voice", data=FORM, files={"audio": ("x.txt", b"hello", "text/plain")}
    )
    assert response.status_code == 415 and stt.calls == 0
