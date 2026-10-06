"""Live: create a real OpenAI Realtime call through /api/realtime/session and hang up.

Run with:  RUN_LIVE_TESTS=1 pytest -m live tests/test_realtime_live.py
Full voice scenarios (speech in/out, tools, interruption): python -m scripts.try_realtime
"""

import os

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1"),
]


async def _offer() -> str:
    aiortc = pytest.importorskip("aiortc")
    from aiortc.mediastreams import AudioStreamTrack

    pc = aiortc.RTCPeerConnection()
    pc.addTrack(AudioStreamTrack())
    pc.createDataChannel("oai-events")
    await pc.setLocalDescription(await pc.createOffer())
    sdp: str = pc.localDescription.sdp
    await pc.close()
    return sdp


async def test_real_call_created_and_ended() -> None:
    if not get_settings().openai_realtime_model:
        pytest.skip("OPENAI_REALTIME_MODEL not set")
    sdp = await _offer()
    with TestClient(app) as client:
        response = client.post(
            "/api/realtime/session",
            json={
                "kiosk_id": "KIOSK-001",
                "session_id": "LIVE-rt",
                "language": "en-US",
                "sdp": sdp,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["sdp"].startswith("v=0") and body["realtime_session_id"].startswith("rtc_")
        end = client.post(
            f"/api/realtime/session/{body['realtime_session_id']}/end",
            json={"kiosk_id": "KIOSK-001"},
        )
        assert end.json() == {"success": True}
