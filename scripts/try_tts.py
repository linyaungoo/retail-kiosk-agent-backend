"""Call POST /api/tts on a running server, time it, and save the audio to listen to.

Examples:
    python -m scripts.try_tts
    python -m scripts.try_tts --url http://127.0.0.1:8010 --runs 3
    python -m scripts.try_tts --lang en-US "We close at 9 PM."

Audio files are written to samples/tts/ (git-ignored).
"""

import argparse
import asyncio
import statistics
import time
from pathlib import Path

import httpx

DEFAULT_TEXTS: list[tuple[str, str]] = [
    ("my-MM", "Coca Cola 1L ကို Aisle A03၊ Rack R02၊ Shelf S02 မှာ ရှာနိုင်ပါတယ်။"),
    ("my-MM", "ဆိုင်က ည ၉ နာရီမှာ ပိတ်ပါတယ်။"),
    ("my-MM", "BMI က 24.2 ဖြစ်ပြီး ပုံမှန်အလေးချိန် ဖြစ်ပါတယ်။"),
    ("en-US", "Coca Cola 1L is in aisle A03, rack R02, shelf S02."),
    ("en-US", "We close at 9 PM."),
]
EXTENSIONS = {"audio/mpeg": "mp3", "audio/wav": "wav", "audio/ogg": "ogg", "audio/aac": "aac"}


async def main(args: argparse.Namespace, out: Path) -> None:
    items = [(args.lang, t) for t in args.texts] if args.texts else DEFAULT_TEXTS
    async with httpx.AsyncClient(base_url=args.url, timeout=30) as client:
        for index, (language, text) in enumerate(items, start=1):
            ttfb: list[float] = []
            totals: list[float] = []
            server_fb: list[int] = []
            audio = b""
            media_type = ""
            for _ in range(args.runs):
                start = time.perf_counter()
                async with client.stream(
                    "POST", "/api/tts", json={"text": text, "language": language}
                ) as response:
                    if response.status_code != 200:
                        await response.aread()
                        raise SystemExit(f"{response.status_code}: {response.text}")
                    chunks: list[bytes] = []
                    async for chunk in response.aiter_bytes():
                        if not chunks:
                            ttfb.append((time.perf_counter() - start) * 1000)
                        chunks.append(chunk)
                    totals.append((time.perf_counter() - start) * 1000)
                    server_fb.append(int(response.headers.get("x-tts-first-byte-ms", "0")))
                    media_type = response.headers["content-type"].split(";")[0]
                    audio = b"".join(chunks)
            ext = EXTENSIONS.get(media_type, "bin")
            path = out / f"{index:02d}_{language}.{ext}"
            path.write_bytes(audio)
            print(
                f"{language} first audio {statistics.median(ttfb):5.0f} ms "
                f"(provider {statistics.median(server_fb):5.0f} ms), "
                f"complete {statistics.median(totals):5.0f} ms, {len(audio) / 1024:5.1f} KB "
                f"-> {path}\n      {text}"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("texts", nargs="*")
    parser.add_argument("--lang", default="my-MM", choices=["my-MM", "en-US"])
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    parser.add_argument("--runs", type=int, default=1, help="repeat each text (timing)")
    parser.add_argument("--out", default="samples/tts")
    cli_args = parser.parse_args()
    out_dir = Path(cli_args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(cli_args, out_dir))
