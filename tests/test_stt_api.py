"""POST /api/stt with a stub provider (no OpenAI calls)."""

from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models.kiosk import Language
from app.services.stt_service import TranscriptionResult

FORM = {"language": "my-MM", "session_id": "MOBILE-abc123", "kiosk_id": "KIOSK-001"}
WAV = ("speech.wav", b"RIFF....WAVEfmt fake audio", "audio/wav")


class StubSTT:
    name = "stub"

    def __init__(self, text: str = "Coca Cola ဘယ်မှာရှိလဲ") -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    async def transcribe(
        self,
        audio: bytes,
        filename: str,
        language: Language | None = None,
        *,
        content_type: str | None = None,
        vocabulary: Sequence[str] = (),
    ) -> TranscriptionResult:
        self.calls.append(
            {"audio": audio, "filename": filename, "language": language, "vocabulary": vocabulary}
        )
        return TranscriptionResult(self.text, language, 1240, self.name, "stub-model")


@pytest.fixture
def stub() -> StubSTT:
    return StubSTT()


@pytest.fixture
def client(stub: StubSTT) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        test_client.app.state.stt = stub  # type: ignore[attr-defined]
        yield test_client
    app.dependency_overrides.clear()


def test_transcribes(client: TestClient, stub: StubSTT) -> None:
    response = client.post("/api/stt", data=FORM, files={"audio": WAV})
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "text": "Coca Cola ဘယ်မှာရှိလဲ",
        "language": "my-MM",
        "duration_ms": 1240,
    }
    call = stub.calls[0]
    assert call["audio"] == WAV[1] and call["filename"] == "audio.wav"
    assert call["language"] == "my-MM"
    # Brand names from the cached catalog are passed as recognition vocabulary.
    assert "Coca Cola" in call["vocabulary"] and "Head & Shoulders" in call["vocabulary"]


def test_invalid_language(client: TestClient) -> None:
    response = client.post("/api/stt", data={**FORM, "language": "th-TH"}, files={"audio": WAV})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_LANGUAGE"


@pytest.mark.parametrize("field", ["session_id", "kiosk_id", "language"])
def test_missing_field(client: TestClient, field: str) -> None:
    data = {k: v for k, v in FORM.items() if k != field}
    response = client.post("/api/stt", data=data, files={"audio": WAV})
    assert response.status_code == 422
    assert field in response.json()["error"]["message"] or field == "language"


def test_missing_audio(client: TestClient) -> None:
    response = client.post("/api/stt", data=FORM)
    assert response.status_code == 422
    assert "audio" in response.json()["error"]["message"]


def test_empty_audio(client: TestClient) -> None:
    response = client.post("/api/stt", data=FORM, files={"audio": ("a.wav", b"", "audio/wav")})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_AUDIO"


def test_unsupported_type(client: TestClient) -> None:
    response = client.post(
        "/api/stt", data=FORM, files={"audio": ("notes.txt", b"hello", "text/plain")}
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_AUDIO"


def test_audio_too_large(client: TestClient) -> None:
    settings = get_settings().model_copy(update={"max_audio_bytes": 10})
    app.dependency_overrides[get_settings] = lambda: settings
    response = client.post("/api/stt", data=FORM, files={"audio": WAV})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "AUDIO_TOO_LARGE"


def test_no_speech(client: TestClient, stub: StubSTT) -> None:
    stub.text = " ... "
    response = client.post("/api/stt", data=FORM, files={"audio": WAV})
    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "EMPTY_TRANSCRIPT",
        "message": "No speech was detected.",
    }


def test_not_configured(client: TestClient) -> None:
    client.app.state.stt = None  # type: ignore[attr-defined]
    response = client.post("/api/stt", data=FORM, files={"audio": WAV})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STT_NOT_CONFIGURED"


def test_kiosk_key_enforced(client: TestClient) -> None:
    from pydantic import SecretStr

    settings = get_settings().model_copy(
        update={"kiosk_auth_enabled": True, "kiosk_api_keys": SecretStr("k1")}
    )
    app.dependency_overrides[get_settings] = lambda: settings
    assert client.post("/api/stt", data=FORM, files={"audio": WAV}).status_code == 401
    ok = client.post("/api/stt", data=FORM, files={"audio": WAV}, headers={"X-Kiosk-Key": "k1"})
    assert ok.status_code == 200
