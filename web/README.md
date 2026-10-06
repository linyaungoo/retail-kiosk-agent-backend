# Kiosk API Console

A browser page for testing the kiosk backend by hand: enter any backend URL and
kiosk key, then try each part of the API.

| Tab | Endpoint | What you can do |
|---|---|---|
| Realtime voice | `POST /api/realtime/session` + WebRTC | Hands-free conversation as the kiosk has it: greeting, live transcripts, tool calls with their results, mascot state, mic/assistant levels, first-audio latency, interruptions, raw Realtime events. The customer can be the microphone, recordings ("Audio files") or typed text. |
| Push-to-talk | `POST /api/kiosk/voice` | Record or upload a question; shows transcript, answer, action, data, server timing, first audio byte at the browser, and plays the answer. Audio-stream or JSON response. |
| Agent | `POST /api/agent` | Text chat with the same session (follow-ups work), tool calls and data, optional spoken answers. |
| STT | `POST /api/stt` | Transcribe a recording or an upload. |
| TTS | `POST /api/tts` | Speak any text; provider and first-byte timings. |
| Admin | `/admin/cache`, `/admin/cache/refresh`, `/health/ready`, `/api/kiosk/config` | Cache status, reload the Google Sheet now, readiness. |

The right-hand panel logs every request with status, time and `X-Request-ID`
(match it against the backend logs). Keys are never logged or shown.

Plain HTML, CSS and JavaScript modules: no build step, no dependencies.

## Open it

**Same origin (simplest).** The backend serves the console at `/console` in
development:

```
http://127.0.0.1:8010/console/
```

On another environment (e.g. a staging Cloud Run service), set
`WEB_CONSOLE_ENABLED=true` there and open `https://<service-url>/console/`. Keep it
off in production.

**From another origin.** Serve this folder and allow its origin on the backend:

```bash
python -m http.server 5500 --directory web
```

Then set `CORS_ALLOWED_ORIGINS=http://localhost:5500` on the backend, restart it,
and open http://localhost:5500. Type the backend URL into **API URL**.

Opening `index.html` directly from disk (`file://`) doesn't work: browsers block
its API calls.

## Notes

- **Microphone and realtime voice need a secure page:** `https://`, or
  `http://localhost` / `http://127.0.0.1`. On a plain-HTTP LAN address the
  browser hides the microphone. Use "Audio files" or typed questions there, or open
  the console on the PC that runs the backend.
- **An https page can't call an http backend** (mixed content). Serve the console
  over http to test a local http backend.
- **Audio files as the customer** keep a faint noise floor on the line between
  recordings. Chrome sends no audio at all while a Web Audio track is silent, so the
  server would never hear the customer stop talking.
- **Keys** stay in this browser tab (session storage) unless you tick
  "Remember keys", which keeps them in local storage on this computer.
- **The store** is decided by the backend (KIOSKS tab). The Store ID field is only
  used for kiosks that aren't registered, in development.
- **Each realtime conversation costs OpenAI usage** while it's open. Press End, or
  the backend hangs up after `REALTIME_IDLE_TIMEOUT_SECONDS` of silence.
