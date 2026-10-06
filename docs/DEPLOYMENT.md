# Deploying to Google Cloud Run

This guide deploys the backend as one Cloud Run service. Flutter kiosks call it over
HTTPS with an `X-Kiosk-Key`. The service calls OpenAI and reads the Google Sheet. API
keys stay in Secret Manager and never reach the kiosk.

```
Flutter kiosk ──HTTPS + X-Kiosk-Key──▶ Cloud Run: retail-kiosk-backend ──▶ OpenAI (STT, agent, TTS,
     │                                    │  identity: kiosk-sheets-reader@…    Realtime calls + sideband)
     │                                    ├──▶ Google Sheets (PRODUCTS / STORES / FAQ / KIOSKS)
     │                                    └──◀ Secret Manager (OpenAI key, kiosk keys, admin key)
     └──WebRTC audio (realtime mode)──▶ OpenAI Realtime
```

In realtime mode the audio goes straight from the kiosk to OpenAI over WebRTC
(UDP, TCP/TLS 443 fallback). Cloud Run only creates the call and runs the tools
over an outbound WebSocket, so request timeouts don't limit a conversation. If a
store network blocks WebRTC, the app falls back to push-to-talk over HTTPS.

Commands are for **PowerShell** on Windows with the
[gcloud CLI](https://cloud.google.com/sdk/docs/install). You can also run them in
**Google Cloud Shell** (browser, gcloud preinstalled). In Cloud Shell, use bash syntax:
`PROJECT=light-485709` instead of `$PROJECT = "light-485709"`, and `\` instead of the
backtick to continue a line.

## 0. Decisions baked into this setup

| Choice | Why |
|---|---|
| **Region `asia-southeast1` (Singapore)** | Closest region to Myanmar. Each voice turn makes 3 sequential OpenAI calls, and from the development network one OpenAI round trip took ~670 ms. Running the backend in Google's network shortens those calls, while the kiosk crosses the slow international link only once per turn. Measure it (step 8). |
| **1 instance, always on** (`--min-instances 1 --max-instances 1`) | No cold starts for customers. Startup takes ~5–9 s, mostly importing the OpenAI Agents SDK plus the sheet load. Sessions, the data cache and the TTS clip cache live in process memory, so more than one instance would split a conversation across machines. Scaling out needs a shared session store such as Redis, which is designed for but not built yet. |
| **CPU always allocated** (`--no-cpu-throttling`) | Lets the background cache refresh and the OpenAI keep-warm run between requests. |
| **Startup CPU boost** (`--cpu-boost`) | Faster imports on start and redeploy. |
| **Build in the cloud** (`--source .`) | Cloud Build builds the `Dockerfile`, so no Docker or large uploads are needed locally. |
| **No key file in production** | The service runs as `kiosk-sheets-reader@…`, which can already read the sheet. |
| **`--allow-unauthenticated`** | Kiosks aren't Google accounts. Access is controlled by `X-Kiosk-Key` (`KIOSK_AUTH_ENABLED=true`), and admin endpoints by `X-Admin-Key`. |

## 1. Variables

```powershell
$PROJECT = "light-485709"
$REGION  = "asia-southeast1"
$SERVICE = "retail-kiosk-backend"
$SA      = "kiosk-sheets-reader@$PROJECT.iam.gserviceaccount.com"

gcloud auth login
gcloud config set project $PROJECT
gcloud config set run/region $REGION
```

Billing must be enabled on the project.

## 2. Enable the APIs (one time)

```powershell
gcloud services enable run.googleapis.com cloudbuild.googleapis.com `
  artifactregistry.googleapis.com secretmanager.googleapis.com sheets.googleapis.com
```

## 3. Secrets (one time)

1. **OpenAI key.** Use a new key for production, and revoke the one that was pasted
   into chat during development:
   ```powershell
   Read-Host "OpenAI API key" | gcloud secrets create openai-api-key --data-file=-
   ```
2. **Kiosk keys.** Create one random key per kiosk and bind it to the kiosk ID
   (`KIOSK-001:<key>`, comma-separated). Each kiosk sends its key as `X-Kiosk-Key`.
   A bound key can't be used to act as another kiosk:
   ```powershell
   $k1 = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
   $k1   # put this on KIOSK-001
   "KIOSK-001:$k1" | gcloud secrets create kiosk-api-keys --data-file=-
   ```
3. **Admin key** for `/admin/cache` and `/admin/cache/refresh`:
   ```powershell
   $admin = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
   $admin  # keep it somewhere safe
   "$admin" | gcloud secrets create admin-api-key --data-file=-
   ```

PowerShell adds a newline when piping. The backend trims whitespace from these values,
so that's harmless.

Let the runtime identity read the secrets:

```powershell
foreach ($s in "openai-api-key", "kiosk-api-keys", "admin-api-key") {
  gcloud secrets add-iam-policy-binding $s `
    --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor
}
```

## 4. Google Sheet access

`kiosk-sheets-reader@light-485709.iam.gserviceaccount.com` is already shared on the sheet
as Viewer, and Cloud Run will run as that account. Nothing more to do.

If you use a different service account, share the sheet with it as **Viewer**.

**Add a `KIOSKS` tab** before deploying. `deploy/cloudrun.env.yaml` sets
`KIOSK_REGISTRY_REQUIRED=true`, so only kiosks listed there can connect. Columns:
`kiosk_id, store_id, name, active` (import [sample_data/KIOSKS.csv](../sample_data/KIOSKS.csv)
to start). The kiosk's store comes from this tab; the app can't choose it.

## 5. Deploy

Run this from the project folder (the one containing `Dockerfile`):

```powershell
gcloud run deploy $SERVICE `
  --source . `
  --region $REGION `
  --service-account $SA `
  --env-vars-file deploy/cloudrun.env.yaml `
  --set-secrets "OPENAI_API_KEY=openai-api-key:latest,KIOSK_API_KEYS=kiosk-api-keys:latest,ADMIN_API_KEY=admin-api-key:latest" `
  --cpu 1 --memory 512Mi `
  --min-instances 1 --max-instances 1 `
  --no-cpu-throttling --cpu-boost `
  --timeout 60 `
  --allow-unauthenticated
```

- **First run:** gcloud offers to create an Artifact Registry repository. Answer **Y**.
  The build takes a few minutes.
- **What gets uploaded:** `.gcloudignore` controls this. `.env`, `credentials/`,
  `samples/` and `.venv/` are never uploaded.
- **Build permission errors:** grant the build's service account the Cloud Run Builder
  role, then deploy again:
  ```powershell
  $NUM = gcloud projects describe $PROJECT --format "value(projectNumber)"
  gcloud projects add-iam-policy-binding $PROJECT `
    --member "serviceAccount:$NUM-compute@developer.gserviceaccount.com" --role roles/run.builder
  ```
- **Memory:** 512 MiB is enough. The container used ~135 MB in testing, and the TTS clip
  cache adds at most ~30 MB.

## 6. Verify

```powershell
$URL = gcloud run services describe $SERVICE --region $REGION --format "value(status.url)"
Invoke-RestMethod "$URL/health"          # {"status":"ok"}
Invoke-RestMethod "$URL/health/ready"    # all checks true
```

`/health/ready` returns 503 and lists what's missing (business data, STT, agent or TTS)
if something isn't configured.

Then run a full voice turn from your PC, with a kiosk key from step 3:

```powershell
.\.venv\Scripts\Activate.ps1
python -m scripts.try_voice samples/stt_synthetic --url $URL --kiosk-key "<kiosk key>" --pause 15 --quiet
```

And a realtime call (WebRTC from your PC; needs `pip install -r requirements-dev.txt`):

```powershell
python -m scripts.try_realtime samples/realtime_tests/t3_hs_where_my.mp3 --url $URL --kiosk-key "<kiosk key>"
```

Expected behaviour in production:

- `/docs` and `/openapi.json` return 404.
- `/admin/*` needs `X-Admin-Key`.
- A request without `X-Kiosk-Key` gets 401.
- A kiosk that isn't in the `KIOSKS` tab, or a key bound to another kiosk, gets 403.
- `GET /api/kiosk/config` reports `"voice_mode": "realtime"`.

## 7. Operate

| Task | How |
|---|---|
| **Logs** | `gcloud run services logs read $SERVICE --region $REGION --limit 50`. In Logs Explorer, filter with `resource.type="cloud_run_revision" jsonPayload.message="voice_completed"`. Each turn logs `stt_ms`, `agent_ms`, `tts_first_byte_ms`, `first_audio_ms`, token counts and `templated`. |
| **Realtime calls** | `jsonPayload.message="realtime_call_ended"`: one line per conversation with `reason` (`client_ended`, `idle_timeout`, `max_duration`, `replaced`, `remote_closed`, `session_tampered`), duration, turns, tool calls, interruptions and the app's first-audio metrics. `realtime_tool` logs each tool call with its backend time. |
| **Cost control** | `REALTIME_IDLE_TIMEOUT_SECONDS` (hang up after silence), `REALTIME_MAX_SESSION_SECONDS`, `REALTIME_MAX_CONCURRENT_CALLS`. To switch every kiosk to push-to-talk: `--update-env-vars VOICE_MODE=chained`; apps pick it up at the next start. |
| **Errors** | Logs are JSON with `severity`, so filter `severity>=ERROR`. Exceptions carry `stack_trace` and are picked up by Error Reporting. |
| **Sheet edited** | The data is picked up within `CACHE_TTL_SECONDS` (5 min). To reload now: `Invoke-RestMethod "$URL/admin/cache/refresh" -Method Post -Headers @{"X-Admin-Key"="<admin key>"}`. |
| **Cache status** | `Invoke-RestMethod "$URL/admin/cache" -Headers @{"X-Admin-Key"="<admin key>"}` |
| **New version** | Re-run the step 5 command. Cloud Run starts the new revision and switches traffic once it's listening. |
| **Roll back** | `gcloud run revisions list --service $SERVICE --region $REGION`, then `gcloud run services update-traffic $SERVICE --region $REGION --to-revisions <REVISION>=100`. |
| **Change config** | Edit `deploy/cloudrun.env.yaml` and redeploy, or for one value: `gcloud run services update $SERVICE --region $REGION --update-env-vars KEY=value`. |
| **Rotate a secret** | Add a version, e.g. `"oldkey,newkey" \| gcloud secrets versions add kiosk-api-keys --data-file=-`. Then create a new revision (re-run the step 5 command). Instances read `:latest` when they start. Remove the old key the same way once kiosks are updated. |
| **Monitoring** | Add an uptime check on `$URL/health/ready` (Cloud Monitoring → Uptime checks) and an alert on log entries with `severity>=ERROR`. |

## 8. Choose the region by measurement

The best region depends on the store's internet route to Google and Google's route to
OpenAI, and neither is known in advance. Measure from the store's network:

1. Deploy as above (Singapore). Run the step 6 `try_voice` command 2–3 times and note
   the `first audio` median.
2. Deploy a temporary copy in the US. This is a second, separate service:
   ```powershell
   gcloud run deploy "$SERVICE-us" --source . --region us-central1 <same flags as step 5>
   ```
   Run the same measurement against its URL.
3. Keep the faster one, and delete the other with `gcloud run services delete`.

## 9. Flutter integration

The [kiosk-mobile](https://github.com/linyaungoo/kiosk-mobile) app's GitHub Actions
workflow builds the APK with these as repository variables and secrets
(`BACKEND_URL`, `VOICE_MODE`, `KIOSK_ID`, `KIOSK_API_KEY`).

| Setting | Value |
|---|---|
| Base URL | `$URL` from step 6, over HTTPS |
| Header on every API call | `X-Kiosk-Key: <this kiosk's key>`. Never in source control. |
| Mode | `GET /api/kiosk/config` returns `voice_mode`, `realtime_available` and `chained_available` |
| Realtime | `POST /api/realtime/session` with `kiosk_id`, `session_id`, `language` and the WebRTC `sdp` offer. Returns the SDP answer and the greeting event. End with `POST /api/realtime/session/{id}/end`. |
| Push-to-talk | `POST /api/kiosk/voice` (multipart): `audio`, `kiosk_id`, `session_id`, `language`. Returns a streamed audio body; metadata is in `X-Kiosk-Response` (base64url JSON). See the README. |
| Never on the device | OpenAI or Google credentials |

## 10. Cost notes

- **Cloud Run.** One always-on instance with 1 vCPU and 512 MiB is billed for every
  second it runs, roughly tens of USD per month. Check the
  [Cloud Run pricing calculator](https://cloud.google.com/products/calculator) for
  `asia-southeast1`.
- **OpenAI, push-to-talk.** Each turn makes 1 transcription, 1 agent call (~1,100
  input and ~50 output tokens) and 1 speech clip. The TTS clip cache removes repeated
  clips. Exact token counts per turn are in the `agent_completed` logs.
- **OpenAI, realtime.** Billed per audio token for as long as the call is open,
  including the greeting and silence that the model hears. Keep the idle timeout
  short (60 s default) and end the call as soon as the customer walks away.
  Compare a week of realtime usage against push-to-talk on the OpenAI usage page
  before rolling out widely; `VOICE_MODE=chained` switches back without an app
  update.
- **Google Sheets and Secret Manager.** At this volume they fall within free tiers or
  close to them.

## 11. Run the container locally

```powershell
docker build -t retail-kiosk-backend .
docker run --rm -p 8081:8080 --env-file .env `
  -v "${PWD}\credentials:/srv/credentials:ro" retail-kiosk-backend
```

- **Development mode:** the `.env` file sets `APP_ENV=development`, which keeps `/docs`
  and the admin endpoints open.
- **Production mode:** add `-e APP_ENV=production -e KIOSK_AUTH_ENABLED=true -e KIOSK_API_KEYS=<test key>`.
- **What's in the image:** it runs as a non-root user and contains no `.env`, credentials
  or recordings. Dependency versions are pinned by `requirements.lock`; regenerate it
  with `python -m scripts.lock_requirements` after upgrading packages.

## Not covered yet (before a wider rollout)

- **More than one instance** needs a shared session store such as Redis. The code has a
  `SessionStore` interface ready for it.
- **Per-kiosk rate limiting** (realtime has a global call cap and one call per kiosk).
- **On-device testing** of realtime mode on the store network (WebRTC through the
  store's firewall, echo cancellation with the kiosk's speaker).
- **Burmese TTS provider evaluation** (e.g. Azure `my-MM` neural voices).
- **Testing with real store recordings** in place of synthetic ones.
- **A CI pipeline** to run `pytest`, `ruff` and `mypy` and deploy on merge.
