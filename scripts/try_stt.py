"""Send audio files to POST /api/stt on a running server and time them.

Examples:
    python -m scripts.try_stt samples/                    # every audio file in a folder
    python -m scripts.try_stt recording.m4a --lang en-US
    python -m scripts.try_stt samples/ --runs 3

Language comes from --lang, or from the file name: *_en*.* -> en-US, otherwise my-MM.
"""

import argparse
import asyncio
import mimetypes
import statistics
import time
from pathlib import Path

import httpx

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg", ".oga", ".flac", ".mpeg"}


def _files(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            found += sorted(p for p in path.rglob("*") if p.suffix.lower() in AUDIO_EXTENSIONS)
        else:
            found.append(path)
    return found


async def main(files: list[Path], args: argparse.Namespace) -> None:
    async with httpx.AsyncClient(base_url=args.url, timeout=60) as client:
        for path in files:
            language = args.lang or ("en-US" if "_en" in path.stem.lower() else "my-MM")
            audio = path.read_bytes()  # noqa: ASYNC240 - small local test files
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            server_ms: list[int] = []
            client_ms: list[float] = []
            text = ""
            for _ in range(args.runs):
                start = time.perf_counter()
                response = await client.post(
                    "/api/stt",
                    data={"language": language, "session_id": "CLI-stt", "kiosk_id": "KIOSK-CLI"},
                    files={"audio": (path.name, audio, content_type)},
                )
                client_ms.append((time.perf_counter() - start) * 1000)
                body = response.json()
                if not body.get("success"):
                    text = f"ERROR {body['error']['code']}: {body['error']['message']}"
                    break
                text = body["text"]
                server_ms.append(body["duration_ms"])
            timing = (
                f"stt {statistics.median(server_ms):.0f} ms, "
                f"round trip {statistics.median(client_ms):.0f} ms"
                if server_ms
                else ""
            )
            print(f"{path.name} [{language}, {len(audio) / 1024:.0f} KB] {timing}\n    -> {text}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--lang", choices=["my-MM", "en-US"])
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    parser.add_argument("--runs", type=int, default=1)
    cli_args = parser.parse_args()
    asyncio.run(main(_files(cli_args.paths), cli_args))
