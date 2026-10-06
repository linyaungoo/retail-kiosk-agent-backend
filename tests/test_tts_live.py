"""Live OpenAI TTS (costs a little). Run with: RUN_LIVE_TESTS=1 pytest -m live"""

import os
from collections.abc import AsyncIterator

import pytest
from openai import AsyncOpenAI

from app.config import get_settings
from app.models.kiosk import Language
from app.services.openai_tts_service import OpenAITextToSpeech
from app.services.tts_service import synthesize

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1"),
]


@pytest.fixture
async def tts() -> AsyncIterator[OpenAITextToSpeech]:
    settings = get_settings()
    if not settings.openai_api_key or not settings.openai_tts_model:
        pytest.skip("OPENAI_API_KEY / OPENAI_TTS_MODEL not set")
    client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
    yield OpenAITextToSpeech(
        client,
        model=settings.openai_tts_model,
        voice=settings.openai_tts_voice,
        audio_format="mp3",
        speed=1.0,
        request_timeout=20,
        first_byte_timeout=10,
    )
    await client.close()


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("my-MM", "Coca Cola 1L ကို Aisle A03၊ Rack R02၊ Shelf S02 မှာ ရှာနိုင်ပါတယ်။"),
        ("en-US", "Coca Cola 1L is in aisle A03, rack R02, shelf S02."),
    ],
)
async def test_synthesizes_mp3(tts: OpenAITextToSpeech, language: Language, text: str) -> None:
    result = await synthesize(tts, text, language)
    assert len(result.audio) > 5_000
    assert result.audio[:3] == b"ID3" or result.audio[0] == 0xFF  # MP3 header / frame sync
    assert 0 < result.first_byte_ms <= result.total_ms
