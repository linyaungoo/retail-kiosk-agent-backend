"""Live end-to-end voice turn: real STT, agent and TTS (costs a little).

Run with:  RUN_LIVE_TESTS=1 pytest -m live tests/test_voice_live.py
"""

import base64
import json
import os

import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

from app.config import get_settings
from app.main import app

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1"),
]


def test_voice_turn_end_to_end() -> None:
    settings = get_settings()
    if not (settings.openai_api_key and settings.openai_stt_model and settings.openai_agent_model):
        pytest.skip("OpenAI STT/agent not configured")
    with OpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=30) as openai_client:
        question = openai_client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="ash",
            input="What time does the store close?",
            response_format="mp3",
        ).content

    with TestClient(app) as client:
        response = client.post(
            "/api/kiosk/voice",
            data={
                "organization_id": "ORG-001",
                "store_id": "STORE-001",
                "kiosk_id": "KIOSK-TEST",
                "session_id": "LIVE-voice",
                "language": "en-US",
            },
            files={"audio": ("question.mp3", question, "audio/mpeg")},
        )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/mpeg"
    assert len(response.content) > 5_000
    meta = json.loads(base64.urlsafe_b64decode(response.headers["x-kiosk-response"]))
    assert "close" in meta["transcript"].lower()
    assert meta["action"] == "STORE_INFO" and "9" in meta["answer"]
    timing = meta["timing"]
    assert timing["total_ms"] >= timing["stt_ms"] + timing["agent_ms"]
