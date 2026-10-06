# Retail Kiosk Backend

FastAPI backend for a voice-first retail store kiosk (Flutter client).

Target pipeline: audio → Speech-to-Text → Retail Store Assistant (OpenAI, with
function tools) → answer text → Text-to-Speech → audio.

## Status

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | FastAPI skeleton, `GET /health` | ✅ Done |
| 2 | OpenAI STT, `POST /api/stt` | ✅ Done (tested on synthetic audio; real recordings pending) |
| 3 | Mock business data + 4 Python tools | ✅ Done |
| 4 | Retail Store Assistant, `POST /api/agent` | ✅ Done |
| 5 | Google Sheets + cache | ✅ Done (verified against the live sheet) |
| 6 | TTS abstraction, `POST /api/tts` | ✅ Done (Burmese voice quality to be evaluated) |
| 7 | `POST /api/kiosk/voice` orchestration | ✅ Done |
| 8 | Latency optimisation | ✅ Done |
| 9 | Docker / Cloud Run | ✅ Image built and tested locally; deploy steps in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |

## Local setup (Windows PowerShell)

Requires Python 3.12+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env   # then fill in values
```

macOS/Linux: `source .venv/bin/activate` and `cp .env.example .env`.

## Run

```powershell
uvicorn app.main:app --reload
```

Check it:

```powershell
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

Interactive API docs: http://127.0.0.1:8000/docs. They are disabled when
`APP_ENV=production`.

Health endpoints:

- `GET /health` is liveness: the process is up.
- `GET /health/ready` is readiness. It returns 200 when the business data is loaded and
  STT, the agent and TTS are configured. Otherwise it returns 503 with the failing checks.

## Docker and Cloud Run

```powershell
docker build -t retail-kiosk-backend .
docker run --rm -p 8081:8080 --env-file .env -v "${PWD}\credentials:/srv/credentials:ro" retail-kiosk-backend
```

The image is about 360 MB and runs as a non-root user. It holds no secrets: all
configuration comes from environment variables. Dependency versions are pinned by
`requirements.lock`; regenerate it with `python -m scripts.lock_requirements`.

The step-by-step Cloud Run guide (secrets, service account, deploy, verify, logs,
rollback, region choice, costs) is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). The
non-secret production config is [deploy/cloudrun.env.yaml](deploy/cloudrun.env.yaml).

Logs are JSON lines with `time`, `severity`, `message` and `stack_trace`, the fields
Google Cloud Logging and Error Reporting read natively.

## Try the agent

```powershell
# HTTP (server running on port 8010)
$body = '{"organization_id":"ORG-001","store_id":"STORE-001","kiosk_id":"KIOSK-001","session_id":"MOBILE-abc123","language":"my-MM","message":"Coca Cola ဘယ်မှာရှိလဲ"}'
Invoke-RestMethod http://127.0.0.1:8010/api/agent -Method Post -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))

# Command line, no server (prints answer, tool calls, latency, tokens)
python -m scripts.try_agent
python -m scripts.try_agent --conversation "အရပ် 170 cm ပါ" "70 kg"
python -m scripts.try_agent --lang en-US --store STORE-002 "Where is the Coca Cola?"
python -m scripts.try_agent --model gpt-4.1-mini --effort ""   # compare models
```

`tool_calls` (debug) appears in `/api/agent` responses only when `APP_ENV=development`.

## Voice turn (the kiosk endpoint)

`POST /api/kiosk/voice` (multipart): `audio`, `organization_id`, `store_id`, `kiosk_id`,
`session_id`, `language`.

Pipeline: audio → STT → Retail Store Assistant (tools) → answer → TTS → streamed audio.

**Default response.** The body streams the spoken answer (`audio/mpeg`) as soon as the
first bytes exist. The `X-Kiosk-Response` header carries the metadata as base64url-encoded
JSON:

```json
{"success": true, "session_id": "...", "language": "my-MM",
 "transcript": "Coca Cola ဘယ်မှာရှိလဲ", "action": "PRODUCT_LOCATION",
 "answer": "Coca Cola 1L ကို Aisle A03 ...", "data": {"products": [...]},
 "audio_format": "mp3",
 "timing": {"stt_ms": 1050, "agent_ms": 2400, "tool_ms": 0,
            "tts_first_byte_ms": 1100, "total_ms": 4600}}
```

Flutter: `jsonDecode(utf8.decode(base64Url.decode(response.headers['x-kiosk-response'])))`,
then play the body. `X-Action` repeats the action in plain text.

**JSON response.** `?response=json` returns the same JSON as the body, plus
`audio_base64` and `timing.tts_ms`. It's simpler to integrate, but audio can only start
once synthesis has finished.

**Errors** are JSON with `success: false`:

- `INVALID_KIOSK` is checked before STT runs.
- `EMPTY_TRANSCRIPT` means no speech was heard. Play a pre-recorded "please say that
  again" clip.
- `STT_*` and `AGENT_*` errors include the `transcript` when one exists.
- `TTS_*` errors include `transcript`, `answer`, `action` and `data`, so the kiosk can
  still show the answer as text.

```powershell
python -m scripts.try_voice samples/                          # one turn per file
python -m scripts.try_voice a.m4a b.m4a --conversation        # same session, in order
```

## Performance

Time from the end of the customer's speech to the first audio byte, measured on
`/api/kiosk/voice` with 15–60 s idle between customers (2026-10-06):

| | Before Phase 8 | After |
|---|---|---|
| Agent | 2.9 s (2 LLM calls) | 1.2–1.4 s (1 LLM call + template) |
| STT after idle | 1.9 s (reconnect) | 1.1–1.2 s |
| **First audio, new question** | **6.1 s** (worst 9 s) | **3.8–4.0 s** |
| **First audio, repeated question** | — | **2.4 s** (TTS clip cache) |

What changed:

- **Template answers.** For product location, store hours, parking, restroom, BMI and
  "need height/weight", Python renders the answer from the tool result, and the
  agent's second LLM call is skipped (`AGENT_TEMPLATE_ANSWERS`). FAQ and anything
  unusual still go to the LLM.
- **Connections.** The keep-alive was raised from 5 s to 120 s, a connection is warmed
  at startup, and STT, the agent and TTS share one connection pool.
- **Stall protection.** If TTS sends no audio within 4 s, a fresh request is made. Agent
  and STT calls are cut off at 6 s and retried. OpenAI stalls hit about 1 in 8 TTS
  requests on the test network.
- **TTS clip cache.** Identical answer text is served from memory (`TTS_CACHE_ENTRIES`).

Recommended on the Flutter side:

- **Instant acknowledgement.** Play a short pre-recorded acknowledgement ("ခဏလေးနော်")
  or animate the mascot the moment recording stops. It covers the remaining wait.
- **Start playback early.** Play the response stream as it arrives instead of waiting
  for the full download.
- **Fast end-of-speech detection.** Use voice activity detection (VAD) so recording stops
  about 0.5 s after the customer finishes speaking. A fixed recording length wastes time.

## Speech-to-text

`POST /api/stt` (multipart) takes `audio` (wav, m4a, mp3, webm, ogg, flac; max 10 MB)
plus `language`, `session_id` and `kiosk_id`. It returns
`{"success": true, "text": "...", "language": "my-MM", "duration_ms": 1100}`.

- **Storage:** audio is held in memory for the request only and never stored.
- **Burmese:** OpenAI rejects `language="my"`, and without a hint gpt-4o-transcribe
  misreads Burmese as other languages. Burmese is therefore signalled by a
  Burmese-script prompt, which also carries the catalog's brand names. English
  sends `language=en`.
- **Errors:** `INVALID_AUDIO` (empty), `UNSUPPORTED_AUDIO` (415), `AUDIO_TOO_LARGE` /
  `REQUEST_TOO_LARGE` (413), `EMPTY_TRANSCRIPT` (422, no speech heard), `STT_FAILED`,
  `STT_TIMEOUT`.

```powershell
python -m scripts.try_stt samples/ --runs 3   # every audio file in samples/; *_en* = English
```

## Text-to-speech

`POST /api/tts` with `{"text": "...", "language": "my-MM"}` streams audio back
(MP3 by default) as soon as the first bytes arrive.

- **Headers:** `X-TTS-First-Byte-Ms` gives the provider's time to first audio, and
  `X-TTS-Provider` names the provider used.
- **Errors:** an error before any audio is sent comes back as JSON, for example
  `{"success": false, "error": {"code": "TTS_FAILED"}}`.
- **Providers:** each language has its own provider (`TTS_PROVIDER_MY_MM`,
  `TTS_PROVIDER_EN_US`). A new vendor only needs a `TextToSpeechService.stream()`
  implementation in `app/services/`.

```powershell
python -m scripts.try_tts --runs 3   # timings + audio files in samples/tts/
```

## Business data (Google Sheets + cache)

Set `BUSINESS_DATA_SOURCE=google_sheets` to read the `PRODUCTS`, `STORES` and `FAQ` tabs.
Setup steps and the column layout are in [sample_data/README.md](sample_data/README.md).

- **Startup:** the whole sheet is loaded once with a single API call.
- **Requests:** customer requests read only from memory. Google is never on the request path.
- **Refresh:** after `CACHE_TTL_SECONDS` (default 300), the next request triggers one
  background refresh. Customers keep getting the cached copy meanwhile.
- **Failures:** if a refresh fails, the last good data is kept and retries wait
  `CACHE_RETRY_SECONDS`. If the sheet has never loaded, requests get `503 DATA_UNAVAILABLE`.
- **Bad rows:** invalid rows are skipped and logged (`row_skipped`) without affecting the rest.

```powershell
Invoke-RestMethod http://127.0.0.1:8010/admin/cache                       # status / last error
Invoke-RestMethod http://127.0.0.1:8010/admin/cache/refresh -Method Post  # reload after editing
```

Admin endpoints are open in development. Elsewhere they need `X-Admin-Key` (`ADMIN_API_KEY`).

## Test and lint

```powershell
pytest                                   # offline tests, no API calls
$env:RUN_LIVE_TESTS="1"; pytest -m live  # real OpenAI agent behaviour tests
ruff check app tests scripts
ruff format --check app tests scripts
mypy app tests scripts
```

## Code layout

```
app/
  api/        HTTP routes: health, stt, agent, tts, kiosk voice, admin
  models/     request/response, kiosk context, business and tool result models
  data/       row parsing, in-memory index, search, repository, data sources
  services/   agent, STT/TTS providers, Google Sheets, cache, sessions, voice pipeline
  tools/      search_product, get_store_info, search_faq, calculate_bmi
  utils/      logging, timing
deploy/       Cloud Run environment config
docs/         deployment guide
scripts/      try_agent / try_stt / try_tts / try_voice, lock_requirements
sample_data/  PRODUCTS / STORES / FAQ CSVs (Google Sheet import + test fixtures)
tests/        offline tests; *_live.py run real APIs with RUN_LIVE_TESTS=1
```

Tools read `store_id`, `organization_id` and `language` from a trusted `ToolContext`.
The model only supplies the query, topic, or measurements.

## Conventions

- **Config:** All config comes from environment variables (`app/config.py`). Secrets
  such as `OPENAI_API_KEY` stay on the backend and are never sent to Flutter.
- **Request IDs:** Every response carries an `X-Request-ID` header. A valid incoming
  header is reused; otherwise the backend generates one.
- **Logs:** Logs are structured JSON on stdout. Each request logs one
  `request_completed` line with `total_ms`.
- **Errors:** Every error uses one envelope, and stack traces are never returned:

  ```json
  {"success": false, "error": {"code": "VALIDATION_ERROR", "message": "..."}}
  ```
