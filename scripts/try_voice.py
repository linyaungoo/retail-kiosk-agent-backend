"""Send recordings to POST /api/kiosk/voice on a running server.

Prints transcript, answer, action and per-stage timings; saves the spoken
answers to samples/voice_out/.

Examples:
    python -m scripts.try_voice samples/
    python -m scripts.try_voice q1.m4a q2.m4a --conversation     # one session, in order
    python -m scripts.try_voice samples/ --lang en-US --store STORE-002

Language comes from --lang, or from the file name: *_en*.* -> en-US, otherwise my-MM.
"""

import argparse
import asyncio
import base64
import json
import mimetypes
import statistics
import time
import uuid
from pathlib import Path

import httpx

from scripts.try_stt import _files

EXTENSIONS = {"audio/mpeg": "mp3", "audio/wav": "wav", "audio/ogg": "ogg", "audio/aac": "aac"}


async def main(files: list[Path], args: argparse.Namespace, out: Path) -> None:
    session_id = f"CLI-{uuid.uuid4().hex[:8]}"
    first_audio: list[float] = []
    stages: dict[str, list[int]] = {"stt_ms": [], "agent_ms": [], "tts_first_byte_ms": []}
    headers = {"X-Kiosk-Key": args.kiosk_key} if args.kiosk_key else {}
    async with httpx.AsyncClient(base_url=args.url, timeout=60, headers=headers) as client:
        for index, path in enumerate(files, start=1):
            if index > 1 and args.pause:
                await asyncio.sleep(args.pause)  # idle time between customers
            if not args.conversation:
                session_id = f"CLI-{uuid.uuid4().hex[:8]}"
            language = args.lang or ("en-US" if "_en" in path.stem.lower() else "my-MM")
            audio = path.read_bytes()  # noqa: ASYNC240 - small local test files
            form = {
                "organization_id": args.org,
                "store_id": args.store,
                "kiosk_id": "KIOSK-CLI",
                "session_id": session_id,
                "language": language,
            }
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            start = time.perf_counter()
            first_audio_ms = 0.0
            async with client.stream(
                "POST",
                "/api/kiosk/voice",
                data=form,
                files={"audio": (path.name, audio, content_type)},
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    print(f"\n{path.name} -> {response.status_code} {response.text}")
                    continue
                meta = json.loads(base64.urlsafe_b64decode(response.headers["x-kiosk-response"]))
                chunks: list[bytes] = []
                async for chunk in response.aiter_bytes():
                    if not chunks:
                        first_audio_ms = (time.perf_counter() - start) * 1000
                    chunks.append(chunk)
                done_ms = (time.perf_counter() - start) * 1000
                media_type = response.headers["content-type"].split(";")[0]

            ext = EXTENSIONS.get(media_type, "bin")
            saved = out / f"{index:02d}_{path.stem}_answer.{ext}"
            saved.write_bytes(b"".join(chunks))  # noqa: ASYNC240
            t = meta["timing"]
            first_audio.append(first_audio_ms)
            for key, values in stages.items():
                values.append(t[key])
            tools = ", ".join(
                f"{c['name']}({json.dumps(c['arguments'], ensure_ascii=False)})"
                for c in meta.get("tool_calls", [])
            )
            if args.quiet:
                continue
            print(
                f"\n{path.name} [{language}, session {session_id}]\n"
                f"  heard  : {meta['transcript']}\n"
                f"  answer : {meta['answer']}\n"
                f"  action : {meta['action']}   tools: {tools or '-'}\n"
                f"  timing : stt {t['stt_ms']} + agent {t['agent_ms']} (tools {t['tool_ms']}) "
                f"+ tts first byte {t['tts_first_byte_ms']} = server {t['total_ms']} ms\n"
                f"           client: first audio {first_audio_ms:.0f} ms, "
                f"audio complete {done_ms:.0f} ms -> {saved}"
            )

    if len(first_audio) > 1:
        print(f"\nSUMMARY over {len(first_audio)} turns (median / max):")
        for key, values in stages.items():
            print(f"  {key:18} {statistics.median(values):6.0f} / {max(values):6.0f} ms")
        print(
            f"  {'first audio':18} {statistics.median(first_audio):6.0f} / "
            f"{max(first_audio):6.0f} ms  (client-side, what the customer waits)"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--lang", choices=["my-MM", "en-US"])
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    parser.add_argument("--store", default="STORE-001")
    parser.add_argument("--org", default="ORG-001")
    parser.add_argument("--conversation", action="store_true", help="same session for all files")
    parser.add_argument("--out", default="samples/voice_out")
    parser.add_argument("--pause", type=float, default=0, help="seconds idle between turns")
    parser.add_argument("--quiet", action="store_true", help="summary only")
    parser.add_argument("--kiosk-key", help="X-Kiosk-Key (when KIOSK_AUTH_ENABLED=true)")
    cli_args = parser.parse_args()
    out_dir = Path(cli_args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(_files(cli_args.paths), cli_args, out_dir))
