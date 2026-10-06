"""Realtime voice test client: behaves like a kiosk over real WebRTC (aiortc).

Plays recordings into the call as if a customer were speaking, receives the model's
audio, and measures what the customer would experience. Needs the dev dependency
`aiortc`.

Examples:
    python -m scripts.try_realtime samples/stt_synthetic/01_my.mp3
    python -m scripts.try_realtime q1.mp3 q2.mp3 --lang en-US        # one conversation
    python -m scripts.try_realtime q.mp3 --interrupt barge_in.mp3     # talk over the answer
    python -m scripts.try_realtime --greeting-only

Per turn it prints what OpenAI heard, the answer transcript, tool calls and timings:
  first audio = end of the customer's speech -> first audible model audio received.
Answers are saved to samples/realtime_out/.
"""

import argparse
import asyncio
import fractions
import json
import statistics
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import av
import httpx
import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription

RATE = 48_000
FRAME_SAMPLES = 960  # 20 ms
VOICE_RMS = 300.0  # decoded s16 audio louder than this counts as speech


def load_pcm(path: Path) -> np.ndarray:
    """Decode any audio file to 48 kHz mono int16."""
    resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    chunks: list[np.ndarray] = []
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    return np.concatenate(chunks).astype(np.int16) if chunks else np.zeros(0, np.int16)


class CustomerMicrophone(MediaStreamTrack):
    """Sends silence, or a queued recording, paced in real time like a microphone."""

    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._pts = 0
        self._start: float | None = None
        self._pending: np.ndarray | None = None
        self._offset = 0
        self._done: asyncio.Future[float] | None = None

    def speak(self, samples: np.ndarray) -> "asyncio.Future[float]":
        """Queue a recording; the future resolves with the time its last frame was sent."""
        self._pending, self._offset = samples, 0
        self._done = asyncio.get_running_loop().create_future()
        return self._done

    async def recv(self) -> av.AudioFrame:
        if self._start is None:
            self._start = time.perf_counter()
        target = self._start + self._pts / RATE
        delay = target - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        chunk = np.zeros(FRAME_SAMPLES, np.int16)
        if self._pending is not None:
            piece = self._pending[self._offset : self._offset + FRAME_SAMPLES]
            chunk[: len(piece)] = piece
            self._offset += FRAME_SAMPLES
            if self._offset >= len(self._pending):
                self._pending = None
                if self._done is not None and not self._done.done():
                    self._done.set_result(time.perf_counter())
        frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = RATE
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, RATE)
        self._pts += FRAME_SAMPLES
        return frame


@dataclass
class Turn:
    name: str
    speech_end: float = 0.0
    first_audio: float | None = None
    events: dict[str, float] = field(default_factory=dict)
    heard: str = ""
    answer: str = ""
    tools: list[str] = field(default_factory=list)
    interrupted: bool = False


class Session:
    def __init__(self) -> None:
        self.turn: Turn | None = None
        self.turns: list[Turn] = []
        self.idle = asyncio.Event()  # assistant not responding/speaking
        self.idle.set()
        self.audio_started = asyncio.Event()
        self.responding = False
        self.speaking = False
        self.awaiting_followup = False  # tool results sent; spoken answer still to come
        self.audio: list[np.ndarray] = []
        self.event_times: dict[str, float] = {}

    def on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type", "")
        now = time.perf_counter()
        self.event_times.setdefault(kind, now)
        turn = self.turn
        if turn is not None:
            turn.events.setdefault(kind, now)
        if kind == "response.created":
            self.responding = True
            self.awaiting_followup = False
            self.idle.clear()
        elif kind == "output_audio_buffer.started":
            self.speaking = True
            self.audio_started.set()
        elif kind in ("output_audio_buffer.stopped", "output_audio_buffer.cleared"):
            self.speaking = False
            if kind == "output_audio_buffer.cleared" and turn is not None:
                turn.interrupted = True
        elif kind == "response.done":
            self.responding = False
            response = event.get("response", {})
            outputs = response.get("output", [])
            if response.get("status") == "completed" and any(
                item.get("type") == "function_call" for item in outputs
            ):
                self.awaiting_followup = True
            if turn is not None:
                for item in outputs:
                    if item.get("type") == "function_call":
                        turn.tools.append(f"{item.get('name')}({item.get('arguments')})")
                    for content in item.get("content", []) or []:
                        text = content.get("transcript") or content.get("text")
                        if text:
                            turn.answer = (turn.answer + " " + text).strip()
        elif kind == "conversation.item.input_audio_transcription.completed" and turn is not None:
            turn.heard = (turn.heard + " " + event.get("transcript", "")).strip()
        elif kind == "error":
            print(f"   !! error event: {json.dumps(event.get('error'), ensure_ascii=False)}")
        if not (self.responding or self.speaking or self.awaiting_followup):
            self.idle.set()


async def consume_audio(track: MediaStreamTrack, session: Session) -> None:
    while True:
        try:
            frame = await track.recv()
        except Exception:
            return
        if not isinstance(frame, av.AudioFrame):
            continue
        samples = frame.to_ndarray().reshape(-1)
        session.audio.append(samples.astype(np.int16))
        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) if samples.size else 0.0
        turn = session.turn
        if turn is not None and turn.first_audio is None and rms > VOICE_RMS:
            now = time.perf_counter()
            if turn.speech_end and now > turn.speech_end:
                turn.first_audio = now


async def wait_until_idle(session: Session, limit_seconds: float) -> None:
    deadline = time.perf_counter() + limit_seconds
    while time.perf_counter() < deadline:
        await asyncio.wait_for(
            session.idle.wait(), timeout=max(0.1, deadline - time.perf_counter())
        )
        await asyncio.sleep(1.2)  # stays idle (no tool follow-up response starting)?
        if session.idle.is_set():
            return


def report(turn: Turn) -> None:
    def rel(kind: str) -> str:
        t = turn.events.get(kind)
        return f"{(t - turn.speech_end) * 1000:+.0f}" if t is not None else "-"

    first = (
        f"{(turn.first_audio - turn.speech_end) * 1000:.0f} ms"
        if turn.first_audio is not None
        else "no audio"
    )
    print(f"\n> {turn.name}")
    print(f"  heard  : {turn.heard or '(no transcript)'}")
    print(f"  answer : {turn.answer or '(none)'}")
    print(
        f"  tools  : {', '.join(turn.tools) or '-'}{'   [interrupted]' if turn.interrupted else ''}"
    )
    print(
        f"  timing : first audio {first} after speech end "
        f"(speech_stopped {rel('input_audio_buffer.speech_stopped')}, "
        f"response.created {rel('response.created')}, "
        f"audio_started {rel('output_audio_buffer.started')} ms)"
    )


async def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out)
    headers = {"X-Kiosk-Key": args.kiosk_key} if args.kiosk_key else {}
    session = Session()
    mic = CustomerMicrophone()
    pc = RTCPeerConnection()
    pc.addTrack(mic)
    channel = pc.createDataChannel("oai-events")
    channel_open = asyncio.Event()
    channel.on("open", channel_open.set)
    channel.on("message", lambda message: session.on_event(json.loads(message)))

    @pc.on("track")
    def on_track(track: MediaStreamTrack) -> None:
        if track.kind == "audio":
            asyncio.ensure_future(consume_audio(track, session))

    await pc.setLocalDescription(await pc.createOffer())
    session_id = f"CLI-RT-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=args.url, timeout=30, headers=headers) as client:
        t0 = time.perf_counter()
        response = await client.post(
            "/api/realtime/session",
            json={
                "kiosk_id": args.kiosk,
                "session_id": session_id,
                "language": args.lang,
                "store_id": args.store,
                "sdp": pc.localDescription.sdp,
            },
        )
        if response.status_code != 200:
            raise SystemExit(f"session failed: {response.status_code} {response.text}")
        body = response.json()
        await pc.setRemoteDescription(RTCSessionDescription(sdp=body["sdp"], type="answer"))
        await asyncio.wait_for(channel_open.wait(), timeout=15)
        connect_ms = (time.perf_counter() - t0) * 1000
        print(
            f"connected in {connect_ms:.0f} ms  model={body['model']} voice={body['voice']} "
            f"vad={body['turn_detection']} call={body['realtime_session_id']}"
        )

        firsts: list[float] = []
        try:
            if body.get("greeting_event") and not args.no_greeting:
                session.turn = Turn(name="(greeting)", speech_end=time.perf_counter())
                channel.send(json.dumps(body["greeting_event"]))
                await asyncio.sleep(0.5)
                await wait_until_idle(session, 30)
                report(session.turn)
                session.turns.append(session.turn)
            for path in [Path(p) for p in args.files]:
                turn = Turn(name=path.name)
                session.turn = turn
                session.audio_started.clear()
                done = mic.speak(load_pcm(path))
                if args.interrupt:
                    # Barge in: once the answer starts, talk over it.
                    turn.speech_end = await done
                    await asyncio.wait_for(session.audio_started.wait(), timeout=20)
                    if turn.first_audio is None:
                        turn.first_audio = time.perf_counter()
                    report(turn)
                    session.turns.append(turn)
                    await asyncio.sleep(args.interrupt_after)
                    turn = Turn(name=f"{Path(args.interrupt).name} (interrupting)")
                    session.turn = turn
                    done = mic.speak(load_pcm(Path(args.interrupt)))
                turn.speech_end = await done
                await asyncio.sleep(0.3)
                await wait_until_idle(session, 40)
                report(turn)
                session.turns.append(turn)
                if turn.first_audio is not None:
                    firsts.append((turn.first_audio - turn.speech_end) * 1000)
        finally:
            await client.post(
                f"/api/realtime/session/{body['realtime_session_id']}/end",
                json={"kiosk_id": args.kiosk},
            )
            await pc.close()

    if session.audio:
        path = out_dir / f"{session_id}.wav"
        pcm = np.concatenate(session.audio)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(pcm.tobytes())
        print(f"\nassistant audio saved to {path}")
    if len(firsts) > 1:
        print(
            f"first audio over {len(firsts)} turns: median {statistics.median(firsts):.0f} ms, "
            f"max {max(firsts):.0f} ms"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*")
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    parser.add_argument("--lang", default="my-MM", choices=["my-MM", "en-US"])
    parser.add_argument("--kiosk", default="KIOSK-001")
    parser.add_argument("--store", default="STORE-001")
    parser.add_argument("--kiosk-key")
    parser.add_argument("--no-greeting", action="store_true")
    parser.add_argument("--greeting-only", action="store_true")
    parser.add_argument("--interrupt", help="recording to play over the first answer")
    parser.add_argument("--interrupt-after", type=float, default=1.0)
    parser.add_argument("--out", default="samples/realtime_out")
    cli = parser.parse_args()
    Path(cli.out).mkdir(parents=True, exist_ok=True)
    if not cli.files and not cli.greeting_only:
        parser.error("give recordings, or --greeting-only")
    asyncio.run(run(cli))
