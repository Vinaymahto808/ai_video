# Railway deployment — bot + knowledge base

Two services from this one repo. The widget stays on Vercel (static files); Railway runs the two servers
that need a real host.

| Service | Dockerfile | Listens on | Public? | Talks to |
|---|---|---|---|---|
| `bot` | `deploy/railway/bot.Dockerfile` | `8080` (`PORT`) | yes, one domain | Groq / Gemini, Edge TTS, `kb` over the private network |
| `kb` | `deploy/railway/kb.Dockerfile` | `4100` (`KB_PORT`) | no domain needed | Gemini embeddings, `/app/data/kb.sqlite` |

Railway builds the whole repo as the build context; `RAILWAY_DOCKERFILE_PATH` (a **service variable**) picks
the file. `.railwayignore` trims the upload, `.dockerignore` trims the build.

## Live (2026-11-04, Free plan, region Southeast Asia)

| | |
|---|---|
| bot | https://bot-production-a55d.up.railway.app |
| kb | private only: `http://kb.railway.internal:4100` |
| project | `motivated-light` (the Free plan allows one project; the three older projects were deleted with consent) |
| kb volume | `kb-volume-yEaR` at `/app/data`, 500 MB ceiling |
| widget | https://avatarwidget-five.vercel.app/avatar_widget/panel.html?api=https://bot-production-a55d.up.railway.app |
| embed | `.../avatar_widget/loader.js` with `data-api="https://bot-production-a55d.up.railway.app"` |

Verified after deploy: `/health` → `ok`, no degraded providers; a real turn retrieved Q&A sources (score 0.796)
and produced audio for every sentence (11–39 KB MP3 clips); four turns measured **p50 1045 ms** server-side
(1026–1498) and a 286-page Q&A import on boot in ~1.9 s. Both services run in `southeast-asia`; the first
deploy landed in US East, and the kb volume had to be recreated in the new region (volumes are region-bound).

Two production notes from the first deploy:

- Railway's builder **rejects the Docker `VOLUME` instruction** (`dockerfile invalid: docker VOLUME at Line 28
  is not supported, use Railway Volumes`). Attach storage with `railway volume add` instead.
- The shared Groq key hit `429 Too Many Requests` once and the bot disabled that provider for 60 s (Gemini was
  briefly `503` at the same moment); it recovered on its own. On the free Groq tier expect this under bursts —
  a paid tier or a second key is the fix.

## 1. Create the project and services

```powershell
railway login                      # or: railway login --browserless  (device code)
railway init -n neha-voice

railway add -s bot -v RAILWAY_DOCKERFILE_PATH=deploy/railway/bot.Dockerfile -v PORT=8080
railway add -s kb  -v RAILWAY_DOCKERFILE_PATH=deploy/railway/kb.Dockerfile
```

`PORT` is for the bot's HTTP server and its domain target; the kb reads `KB_PORT` (baked into the image).

## 2. Variables

Secrets stay out of the repo. `--stdin` keeps values out of the shell history and the command line.

```powershell
# keys from the local .env files, piped straight into Railway
function Push-Var($service, $key, $file, $name) {
  $line = (Select-String -Path $file -Pattern "^$name\s*=" | Select-Object -First 1).Line
  ($line -split '=', 2)[1].Trim().Trim('"') | railway variable set $key --stdin -s $service
}
Push-Var bot GROQ_API_KEY   .env groq_api_key
Push-Var bot GEMINI_API_KEY .env gemini_api_key
Push-Var kb  GEMINI_API_KEY .env gemini_api_key
```

### `bot`

| Variable | Value | Why |
|---|---|---|
| `PORT` | `8080` | set at service creation; the CMD binds `${PORT:-8080}` |
| `HUMAN_BOT_BEHIND_PROXY` | `1` | Railway terminates TLS; trust `X-Forwarded-*` |
| `HUMAN_BOT_SITE_MIRROR` | `0` | `/` returns the bot's own demo page, not a proxied website |
| `HUMAN_BOT_ALLOWED_ORIGINS` | `https://avatarwidget-five.vercel.app` | the widget origin; add the custom domain if the widget moves |
| `HUMAN_BOT_AVATAR_PROVIDER` | `puppet` | 2D puppet from `tran_moment/path_b` (no Simli quota, no key) |
| `HUMAN_BOT_VOICE_STYLE` | `hindi` | hi-IN Swara reads transliterated Hindi; English stays Indian English |
| `HUMAN_BOT_TTS_PROVIDER` | `edge` | default; no quota. `gemini,edge` on a paid Gemini key |
| `HUMAN_BOT_RECORD` | `1` | transcripts + audio under `human_bot/data` (ephemeral without a volume) |
| `HUMAN_BOT_ADMIN_KEY` | random, 32+ chars | `/conversations` and the admin routes off loopback |
| `HUMAN_BOT_PUBLIC_URL` | `https://<bot-domain>` | recording links in Bitrix/lead payloads; set after step 4 |
| `KB_URL` | `http://kb.railway.internal:4100` | private-network DNS, no egress cost |
| `ACR_LEADS_MODE` | `dry` | log the lead payload, do not POST to the CRM until it is signed off |
| `HUMAN_BOT_LLM_ORDER` | `groq,gemini` | skip the unconfigured OpenAI-compatible hop |

Optional: `HUMAN_BOT_RATE_PER_MIN` (default 240), `HUMAN_BOT_SESSIONS_PER_MIN` (20), `HUMAN_BOT_LLM_TIMEOUT_S`
(8), `HUMAN_BOT_EDGE_VOICE_EN`/`_HI`, `HUMAN_BOT_EDGE_RATE`. Add `SIMLI_API_KEY` + `SIMLI_FACE_ID` and set
`HUMAN_BOT_AVATAR_PROVIDER=simli` only if live video is wanted (one session per call, needs quota).

### `kb`

| Variable | Value | Why |
|---|---|---|
| `KB_HOST` | `0.0.0.0` | reachable from the bot's container |
| `KB_PORT` | `4100` | matches `KB_URL`; domain target port is 4100 |
| `KB_DATA_DIR` | `/app/data` | `seed.js` copies `seed/kb.sqlite` here on first boot |
| `KB_TRUST_PROXY` | `1` | rate-limit per real client IP |
| `KB_QUERY_PROVIDER` | `gemini` | 512 MB-safe: no local ONNX model in RAM |
| `KB_LOCAL_MODEL` | `off` | do not download the 130 MB model on a small instance |
| `KB_ADMIN_KEY` | random, 32+ chars | management endpoints (crawl, reindex); the bot's search route stays open |
| `KB_QA_BUNDLE` | `/app/seed/qa_pages.jsonl.gz.b64` | the 286 customer-call Q&A pages, imported at boot |

If the service gets ≥ 1 GB RAM: `KB_QUERY_PROVIDER=local`, `KB_LOCAL_MODEL=Xenova/multilingual-e5-small`
(~200 ms search instead of ~600 ms, plus a 19 s warm-up and a one-time vector build).

## 3. Deploy

```powershell
railway up -s kb      # attached: streams build + deploy logs
railway up -s bot
```

The upload includes `kb/seed/qa_pages.jsonl.gz.b64`, which is **not in git** by design (internal customer
data). Deploying from a GitHub connection instead of `railway up` will silently skip the Q&A bundle.

## 4. Domain

```powershell
railway domain -s bot --port 8080
railway variable set HUMAN_BOT_PUBLIC_URL=https://<the-domain-returned> -s bot
```

No public domain for `kb`: the bot reaches it at `kb.railway.internal:4100`. Add one temporarily only to
debug search by hand (it is keyed and rate-limited, but there is no reason to expose it).

Point the widget at the bot: `https://avatarwidget-five.vercel.app/panel.html?api=https://<bot-domain>`.

## 5. Verify

```powershell
Invoke-RestMethod https://<bot-domain>/health        # ok / avatar / tts / kb
Invoke-WebRequest  https://<bot-domain>/            # demo page (mirror off)
railway logs -s kb                                  # "seed copied", "qa bundle: 286 imported" or 0 unchanged
```

Then one real turn: open the widget URL with `?api=https://<bot-domain>`, speak a service question, and
confirm the answer stream ends with a `final` event and audio starts in under ~1 s.

## 6. Volume (optional, one on Free/Trial)

Free/Trial allows **1 volume per project**, 0.5 GB. Mount it on `kb` (the live project already has one) so the
crawled + imported knowledge survives redeploys. `volume add` takes no `--service` flag — it uses the linked
service:

```powershell
railway service link kb
railway volume add -m /app/data
```

Volumes are region-bound. Changing a service's region means deleting and recreating its volume; the kb data is
rebuilt from `seed/kb.sqlite` + the Q&A bundle, so that is safe.

Without a volume the bot's recordings and `conversations.sqlite` are lost on every redeploy; that is the
right trade-off for a demo, not for production retention.

## Notes

- No Docker on the dev machine: Railway builds the images, so the first `railway up` is the first real
  build. Watch the log for `Using detected Dockerfile` (path picked up) and for the `COPY` lines.
- The bot's `/health` is 503 whenever a provider is degraded (no LLM key, TTS failure). Do **not** wire it
  as a Railway healthcheck — a temporary key problem would restart-loop the service. `/` is the cheap
  liveness check.
- `edge-tts` is pinned to `7.3.1`, the version the latency numbers were measured on. It talks to an
  unofficial Microsoft endpoint; for production SLAs, move the same two voices to Azure Speech
  (`HUMAN_BOT_TTS_PROVIDER` would need an Azure branch).
- Live avatar (`simli`/`d-id`) needs its own key and quota. `puppet` is the default because it cannot run
  out mid-call.
- `/health` publishes `stt` and `tts` (provider strings, no keys) to **every** client, not just loopback. The
  card reads `stt` to choose between recording for `/stt` and the browser recogniser; hiding it made every
  deployed call use the browser recogniser and the mic went deaf after the first turn. Keep those two fields
  public if the payload is trimmed again.
