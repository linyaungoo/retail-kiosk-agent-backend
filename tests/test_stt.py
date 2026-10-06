"""STT services: upload validation, prompt building, OpenAI provider, size limit."""

from collections.abc import Callable, Sequence

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from app.errors import AppError, ErrorCode, register_error_handlers
from app.middleware import BodySizeLimitMiddleware
from app.models.kiosk import Language
from app.services.openai_stt_service import (
    BURMESE_PROMPT,
    ENGLISH_PROMPT,
    MAX_VOCABULARY_CHARS,
    OpenAISpeechToText,
    build_prompt,
)
from app.services.stt_service import (
    EmptyTranscriptError,
    MockSpeechToText,
    STTError,
    STTTimeoutError,
    TranscriptionResult,
    is_empty_transcript,
    transcribe_upload,
    validate_audio,
)

# --- upload validation -------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "content_type", "expected"),
    [
        ("rec.wav", "audio/wav", "audio.wav"),
        ("rec.m4a", "audio/x-m4a", "audio.m4a"),
        ("rec.mp3", "application/octet-stream", "audio.mp3"),
        ("rec.WEBM", "video/webm", "audio.webm"),
        ("blob", "audio/webm;codecs=opus", "audio.webm"),
        ("", "audio/ogg", "audio.ogg"),
        ("../../etc/passwd.wav", "audio/wav", "audio.wav"),  # client names never forwarded
    ],
)
def test_accepted_audio(filename: str, content_type: str, expected: str) -> None:
    assert validate_audio(b"data", filename, content_type).filename == expected


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [("notes.txt", "text/plain"), ("rec.wav", "audio/mpeg"), ("blob", "application/octet-stream")],
)
def test_rejected_audio_type(filename: str, content_type: str) -> None:
    with pytest.raises(AppError) as err:
        validate_audio(b"data", filename, content_type)
    assert err.value.code == ErrorCode.UNSUPPORTED_AUDIO and err.value.status_code == 415


def test_empty_audio_rejected() -> None:
    with pytest.raises(AppError) as err:
        validate_audio(b"", "rec.wav", "audio/wav")
    assert err.value.code == ErrorCode.INVALID_AUDIO


@pytest.mark.parametrize(
    ("text", "empty"),
    [("", True), ("   ", True), ("...", True), ("။", True), ("?!", True),
     ("hi", False), ("ရှိလား", False), ("170", False)],
)  # fmt: skip
def test_empty_transcript_detection(text: str, empty: bool) -> None:
    assert is_empty_transcript(text) is empty


async def test_transcribe_upload_rejects_silence() -> None:
    class Silent(MockSpeechToText):
        async def transcribe(
            self,
            audio: bytes,
            filename: str,
            language: Language | None = None,
            *,
            content_type: str | None = None,
            vocabulary: Sequence[str] = (),
        ) -> TranscriptionResult:
            return TranscriptionResult(" . ", language, 5, "silent", "m")

    upload = validate_audio(b"x", "a.wav", "audio/wav")
    with pytest.raises(EmptyTranscriptError):
        await transcribe_upload(Silent(), upload, "my-MM")


# --- prompt ------------------------------------------------------------------


def test_prompt_per_language_with_brands() -> None:
    my = build_prompt("my-MM", ["Coca Cola", "Head & Shoulders"])
    assert my.startswith(BURMESE_PROMPT) and my.endswith("Brands: Coca Cola, Head & Shoulders.")
    assert build_prompt("en-US", []) == ENGLISH_PROMPT
    assert build_prompt(None, []) == BURMESE_PROMPT  # kiosk default: Burmese with English mixed


def test_prompt_vocabulary_is_capped() -> None:
    prompt = build_prompt("my-MM", [f"Brand{i:04d}" for i in range(1000)])
    assert len(prompt) < len(BURMESE_PROMPT) + MAX_VOCABULARY_CHARS + 20


# --- OpenAI provider (real SDK, simulated HTTP) -----------------------------------


def _openai_stt(handler: Callable[[httpx2.Request], httpx2.Response]) -> OpenAISpeechToText:
    client = AsyncOpenAI(
        api_key="sk-test",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return OpenAISpeechToText(client, model="gpt-4o-transcribe", request_timeout=5)


async def test_burmese_request_uses_prompt_not_language_code() -> None:
    bodies: list[bytes] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(request.read())
        return httpx2.Response(200, json={"text": " Coca Cola ဘယ်မှာရှိလဲ "})

    result = await _openai_stt(handler).transcribe(
        b"RIFFaudio", "audio.wav", "my-MM", content_type="audio/wav", vocabulary=["Coca Cola"]
    )
    assert result.text == "Coca Cola ဘယ်မှာရှိလဲ"
    assert result.provider == "openai" and result.model == "gpt-4o-transcribe"
    body = bodies[0].decode("utf-8", errors="replace")
    assert 'name="model"' in body and "gpt-4o-transcribe" in body
    assert BURMESE_PROMPT in body and "Brands: Coca Cola." in body
    assert 'name="language"' not in body  # the API rejects "my"
    assert 'name="temperature"' in body
    assert 'filename="audio.wav"' in body and "RIFFaudio" in body


async def test_english_request_sends_language() -> None:
    bodies: list[bytes] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(request.read())
        return httpx2.Response(200, json={"text": "Where is Coca Cola?"})

    await _openai_stt(handler).transcribe(b"x", "audio.mp3", "en-US", content_type="audio/mpeg")
    body = bodies[0].decode("utf-8", errors="replace")
    assert 'name="language"\r\n\r\nen\r\n' in body and ENGLISH_PROMPT in body


async def test_bad_audio_rejected_by_provider() -> None:
    error = {"error": {"message": "Audio file might be corrupted or unsupported"}}
    stt = _openai_stt(lambda _: httpx2.Response(400, json=error))
    with pytest.raises(STTError, match="could not be processed"):
        await stt.transcribe(b"x", "audio.wav", "my-MM")


async def test_provider_error_and_timeout() -> None:
    stt = _openai_stt(lambda _: httpx2.Response(500, json={"error": {"message": "boom"}}))
    with pytest.raises(STTError) as err:
        await stt.transcribe(b"x", "audio.wav", "my-MM")
    assert err.value.code == ErrorCode.STT_FAILED

    def slow(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    with pytest.raises(STTTimeoutError) as err2:
        await _openai_stt(slow).transcribe(b"x", "audio.wav", "my-MM")
    assert err2.value.code == ErrorCode.STT_TIMEOUT


# --- request body size limit ---------------------------------------------------------


@pytest.fixture
def small_app() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=100)

    @app.post("/echo")
    async def echo(request: dict[str, str]) -> dict[str, int]:
        return {"n": len(request["x"])}

    return TestClient(app)


def test_body_within_limit(small_app: TestClient) -> None:
    assert small_app.post("/echo", json={"x": "a" * 10}).json() == {"n": 10}


def test_declared_length_over_limit(small_app: TestClient) -> None:
    response = small_app.post("/echo", json={"x": "a" * 500})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_chunked_body_over_limit(small_app: TestClient) -> None:
    def chunks() -> Sequence[bytes]:
        return [b'{"x": "' + b"a" * 60, b"a" * 60 + b'"}']

    response = small_app.post(
        "/echo", content=iter(chunks()), headers={"content-type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
