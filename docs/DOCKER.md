# Run the backend in Docker, test it from this machine

```
browser: web test console (http://localhost:5500)
   │  HTTP + X-Kiosk-Key (CORS)          WebRTC audio (realtime)
   ▼                                          ▼
Docker: retail-kiosk-backend (127.0.0.1:8010) ──▶ OpenAI, Google Sheets
```

The container runs the production image with production settings: kiosk keys
required, `/docs` and `/console` hidden, no transcripts in the logs. The test
console runs outside it, on this machine.

Commands are for PowerShell in the project folder. Requires Docker Desktop.

## 1. One-time setup

1. **`.env`**: your development settings (OpenAI key, Google Sheet ID, models).
   The container reads them, then `deploy/docker.env` overrides them with production
   settings.
2. **`credentials/google-service-account.json`**: mounted read-only into the
   container. It is never copied into the image.
3. **`.env.docker`** (gitignored): the container's kiosk and admin keys. Create it
   once:

   ```powershell
   $k = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)).Replace('+','-').Replace('/','_')
   $a = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)).Replace('+','-').Replace('/','_')
   [IO.File]::WriteAllText("$PWD\.env.docker", "KIOSK_API_KEYS=KIOSK-001:$k`nADMIN_API_KEY=$a`n")
   ```

   `KIOSK-001:<key>` binds the key to kiosk KIOSK-001. For more kiosks, add
   comma-separated entries.

## 2. Start the backend

```powershell
docker compose up -d --build
docker compose ps        # STATUS shows (healthy) after ~10 s
```

The API is on http://127.0.0.1:8010. If the build fails in `pip install`, it's
usually the network; run the same command again.

## 3. Start the test console

In a second terminal:

```powershell
python -m http.server 5500 --directory web
```

Open http://localhost:5500 and fill in:

| Field | Value |
|---|---|
| API URL | `http://127.0.0.1:8010` |
| Kiosk key | the part after `KIOSK-001:` in `.env.docker` |
| Kiosk ID | `KIOSK-001` |
| Admin key | `ADMIN_API_KEY` from `.env.docker` (Admin tab only) |

Press **Check connection**. You should see Live, Ready, Mode: realtime, Realtime and
Push-to-talk. Then try the tabs; see [web/README.md](../web/README.md).

Use `localhost`, not the PC's network address: the browser only allows the
microphone on `localhost` or `https://`. The backend accepts the console only from
the origins in `CORS_ALLOWED_ORIGINS` (`deploy/docker.env`). Add others there if you
serve it elsewhere.

## 4. Operate

| Task | Command |
|---|---|
| Live logs (JSON) | `docker compose logs -f` |
| One conversation's summary | `docker compose logs \| Select-String realtime_call_ended` |
| After changing code | `docker compose up -d --build` |
| After changing `.env`, `deploy/docker.env` or `.env.docker` | `docker compose up -d` (recreates the container) |
| Reload the Google Sheet now | console Admin tab → Reload sheet now |
| Stop | `docker compose down` |

The container restarts automatically with Docker Desktop (`restart: unless-stopped`)
until you run `docker compose down`.

## 5. Point the Flutter app at it

- **Android emulator:** `--dart-define=BACKEND_URL=http://10.0.2.2:8010`
  `--dart-define=KIOSK_API_KEY=<kiosk key>`
- **Phone on the same Wi-Fi:** `BACKEND_URL=http://<this PC's IP>:8010`. This
  needs a debug build (cleartext HTTP), and Windows Firewall must allow inbound TCP
  8010.

## Settings worth knowing

`deploy/docker.env`:

- `KIOSK_REGISTRY_REQUIRED=false` until the Google Sheet has a `KIOSKS` tab. Until
  then every kiosk uses `DEFAULT_STORE_ID` (STORE-001). Switch it to `true` after
  adding the tab.
- `VOICE_MODE=realtime`: what `/api/kiosk/config` tells the app. `chained` switches
  every app to push-to-talk.
- `LOG_TRANSCRIPTS=false`: customer speech stays out of the logs. Set `true` only
  while debugging.
