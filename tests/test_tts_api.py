"""POST /api/tts with the mock provider (no OpenAI calls)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.kiosk import Language
from app.services.tts_service import LanguageRoutedTTS, MockTextToSpeech, TTSError, TTSStream


class FailingTTS:
    name = "failing"

    async def stream(self, text: str, language: Language) -> TTSStream:
        raise TTSError()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        mock = MockTextToSpeech()
        test_client.app.state.tts = LanguageRoutedTTS({"my-MM": mock, "en-US": mock})  # type: ignore[attr-defined]
        yield test_client


@pytest.mark.parametrize(
    ("language", "text"),
    [("my-MM", "ဆိုင်က ည ၉ နာရီမှာ ပိတ်ပါတယ်။"), ("en-US", "We close at 9 PM.")],
)
def test_returns_audio(client: TestClient, language: str, text: str) -> None:
    response = client.post("/api/tts", json={"text": text, "language": language})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content.startswith(b"RIFF")
    assert int(response.headers["x-tts-first-byte-ms"]) >= 0
    assert response.headers["x-tts-provider"] == "mock"
    assert response.headers["cache-control"] == "no-store"


def test_invalid_language(client: TestClient) -> None:
    response = client.post("/api/tts", json={"text": "hi", "language": "fr-FR"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_LANGUAGE"


@pytest.mark.parametrize("text", ["", "   ", "x" * 1001])
def test_text_validated(client: TestClient, text: str) -> None:
    response = client.post("/api/tts", json={"text": text, "language": "en-US"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_provider_failure_is_json_error(client: TestClient) -> None:
    client.app.state.tts = FailingTTS()  # type: ignore[attr-defined]
    response = client.post("/api/tts", json={"text": "hi", "language": "en-US"})
    assert response.status_code == 502
    assert response.json() == {
        "success": False,
        "error": {"code": "TTS_FAILED", "message": "Unable to generate speech."},
    }


def test_not_configured(client: TestClient) -> None:
    client.app.state.tts = None  # type: ignore[attr-defined]
    response = client.post("/api/tts", json={"text": "hi", "language": "en-US"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TTS_NOT_CONFIGURED"
