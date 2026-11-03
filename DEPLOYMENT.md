# Deployment guide (production)

Three processes, one box (or three small ones), all behind one HTTPS reverse proxy:

| Process | Start | Port | Talks to |
|---|---|---|---|
| `human_bot` | `python human_bot/server/app.py --host 127.0.0.1 --port 4001 --persona auto-car-repair` | 4001 | Groq / OpenAI / Gemini (LLM), Groq Whisper (STT), Edge TTS, Simli or D-ID, `kb` |
| `kb` | `cd kb && node src/server.js` | 4100 | Gemini embeddings, `data/kb.sqlite` |
| widget files | any static host / CDN (`avatar_widget/loader.js`, `panel.html`, `src/`) | 4300 in dev | `human_bot` over HTTPS |

Everything was audited on 2026-11-02 (three read-only audits, then fixes). This file is the checklist that
came out of it. Items marked **open** are not done and need a decision or an account.

## 1. Secrets

- All keys live in `voice_clone/.env` on the laptop. In production pass them as environment variables to
  the service (systemd `EnvironmentFile=`, Docker secrets); the code reads env first, the file second.
- **Rotate every key in that file before going live** if the dev server was ever reachable through a tunnel
  or proxy: until 2026-11-02 the static route served `/web/../../voice_clone/.env` (fixed, see audit).
- Never log, print or return a key. `/health` only shows provider *names*; full detail only on loopback.

## 2. human_bot environment

| Variable | Production value | Why |
|---|---|---|
| `HUMAN_BOT_ALLOWED_ORIGINS` | `https://autocarrepair.in,https://www.autocarrepair.in` (comma list) | CORS and `frame-ancestors`. Default `*` is dev only; the server warns when `*` is used off loopback. |
| `HUMAN_BOT_BEHIND_PROXY` | `1` | Trust `X-Forwarded-For/Host/Proto` from the reverse proxy (client IP for rate limits, public URL for the widget tag). Leave unset without a proxy. |
| `HUMAN_BOT_SITE_MIRROR` | `0` | The "/" site preview that proxies the dealer website through the bot origin is a demo tool. Default: on only on loopback. |
| `HUMAN_BOT_TTS_OPEN` | unset | `/tts` only speaks sentences the server itself produced. Set `1` only on a dev box to synthesize arbitrary text. |
| `HUMAN_BOT_RATE_PER_MIN` / `HUMAN_BOT_SESSIONS_PER_MIN` | `240` / `20` | Per-client-IP fixed window. Loopback is exempt unless behind a proxy. |
| `HUMAN_BOT_LLM_ORDER` | `groq,openai-compatible,gemini` | Provider order; a failing provider is skipped 30 s (network), 60 s (429), 1 h (401/403/404). |
| `HUMAN_BOT_VOICE_STYLE` | `hindi` | Devanagari + hi-IN Swara (see human_bot/README "Voice"). `neerja` / `expressive` are the Roman-script alternatives. |
| `HUMAN_BOT_TTS_PROVIDER` | `edge` today | **open:** Edge is an unofficial endpoint. For a paid, supported path use the same voices on Azure Speech (one region close to Delhi, persistent connection). |
| `KB_URL` / `KB_TIMEOUT_S` | `http://127.0.0.1:4100` / `2.5` | Knowledge service. |
| `HUMAN_BOT_RECORD` | `1` | Store every conversation (transcript, facts, analysis, audio) in `human_bot/data/`; the greeting discloses it. `0` turns storage and the disclosure off. Put `human_bot/data/` on a backed-up volume. |
| `HUMAN_BOT_ADMIN_KEY` | long random string | `GET /conversations…` (the CRM feed) off loopback needs `X-Admin-Key`. |
| `BITRIX24_WEBHOOK_URL` | `https://<portal>.bitrix24.in/rest/<user>/<code>/` | Inbound webhook with `crm` scope. Each ended call becomes one Lead (+ transcript comment), pushed by the outbox worker with retries; idempotent per conversation. Create the custom lead field `UF_CRM_CONVERSATION_ID` (string) once. |
| `BITRIX24_LEAD_STATUS` / `BITRIX24_ASSIGNED_BY` | `NEW` / user id | Funnel stage for new leads and (optional) the advisor to assign. |
| `HUMAN_BOT_PUBLIC_URL` | `https://bot.example.in` | Used to put a recording link into the lead comment. |
| `HUMAN_BOT_LLM_TIMEOUT_S` | `4` (code default 8) | Per-request socket deadline for an LLM provider before the next one is tried. 4 s since the 2026-11-03 latency review: a stalled provider cost 8 s per turn before the fallback. |
| `HUMAN_BOT_AVATAR_PROVIDER` | `puppet` until the custom face exists | `simli` opens the Simli WebRTC face and streams our TTS audio into it, with the 2D puppet as automatic fallback. **Do not set `simli` while `SIMLI_FACE_ID` is a stock face** (tried 2026-11-03 with "Anna": the stock face replaces Neha's whole frame and the user rejected it). Generate the face from the grid portrait first: `python tran_moment/simli_face.py generate` (needs a Trinity-2 generation on the Simli plan), then switch. `did` = D-ID (untested lately). |
| `SIMLI_API_KEY`, `SIMLI_FACE_ID` | as issued | The server picks the face; clients can no longer choose one. |

Built-in limits now enforced: JSON bodies 64 KB (413), `/stt` audio 8 MB, `/tts` text 600 chars, sessions
dropped after 30 min idle and capped at 5000, TTS clip cache 400 entries, provider circuit breakers for
LLM and TTS, request-time type checks (a malformed body gets a 4xx, never a dropped socket).

## 3. kb environment

| Variable | Production value | Why |
|---|---|---|
| `GEMINI_API_KEY` | separate key from indexing if possible | Search and crawling share one quota; a nightly re-embed can exhaust the free tier for customer search. Use a paid key for queries. |
| `KB_HOST` | `127.0.0.1` | The server refuses to start on any other host unless `KB_ADMIN_KEY` is set. |
| `KB_ADMIN_KEY` | long random string | Required for crawl / refresh / reindex when not on loopback. Send as `x-api-key`. |
| `KB_TRUST_PROXY` | `loopback` when behind nginx/Caddy | Otherwise every caller looks like 127.0.0.1 and management routes would be open. |
| `KB_QUERY_TIMEOUT_MS` | `2000` | A query embedding slower than this (or a Gemini outage, circuit open 30 s after 3 failures) answers from the FTS5 lexical index instead; the response carries `mode: "lexical"`. |
| `KB_DATA_DIR` | a backed-up volume | `kb.sqlite` is the only copy of the crawl. |
| `KB_QUERY_PROVIDER` | `local` | Hybrid local-vector + lexical search (~30 ms). `gemini` forces the API path (~550 ms). |
| `KB_LOCAL_MODEL` | `Xenova/multilingual-e5-small` | Downloaded once into `data/models/` (needs outbound HTTPS to huggingface.co the first time; ~120 MB). Model load ~20 s at start, ~670 MB RSS: give the kb process 1 GB. First start builds the vectors (~3 min for 900 chunks), search uses Gemini until then. |

Backups: `reindex` now writes `data/backups/kb-before-reindex-<site>-<ts>.sqlite` (VACUUM INTO, consistent)
before wiping a site; every finished job checkpoints the WAL. For scheduled backups copy with
`sqlite3 data/kb.sqlite "VACUUM INTO 'backup.sqlite'"` or the same call through node:sqlite; never copy the
`.sqlite` file alone while the server runs (the WAL holds the last hours).

Re-crawl: `node src/cli.js crawl auto_car_repair` (incremental) weekly, `refresh` monthly. Crawl concurrency
is capped at 2 tabs through the API.

## 4. Reverse proxy (HTTPS)

`deploy/Caddyfile` is a working example. The two things that matter:

1. **No buffering on `/chat` (SSE) and `/tts` (chunked MP3).** Caddy: `flush_interval -1`. nginx:
   `proxy_buffering off; proxy_http_version 1.1; proxy_set_header Connection "";` on those locations. If
   the proxy buffers, replies arrive all at once and the 1 s first-audio win disappears.
2. **Microphone needs a secure context.** `getUserMedia` only works on `https://` (or `localhost`), and the
   dealer page must not send `Permissions-Policy: microphone=()`.

The bot sends `X-Accel-Buffering: no` on both streaming routes and `Connection: close` on SSE, speaks
HTTP/1.1 with keep-alive, and answers `Range` requests for cached clips (iOS probes with `bytes=0-1`).

## 5. Process supervision

- Linux: `deploy/human_bot.service` and `deploy/kb.service` (systemd). Both handle `SIGTERM` (the bot
  leaves `serve_forever` cleanly; kb stops the crawl, checkpoints, and exits within 10 s).
- Windows: NSSM or a Scheduled Task with "restart on failure"; note `SIGTERM` is not delivered on Windows,
  so stop with the service manager, not `taskkill /F`, when a crawl is running.
- Logs go to stderr: `[YYYY-MM-DD HH:MM:SS] <client-ip> <request line>` for the bot, JSON lines for kb.
- Health: `GET /health` on both returns `ok` plus a `degraded` list and 503 when degraded (bot: no live
  LLM or a TTS error; kb: no key, embedding circuit open, last crawl failed, no FTS5). Point the load
  balancer or uptime monitor at these.

## 6. Widget hosting

- Ship `avatar_widget/loader.js`, `panel.html` and `src/` on the widget origin (CDN or the bot host).
  **Live on Vercel since 2026-11-03:** `https://avatarwidget-five.vercel.app` (project `avatar_widget`, static, `vercel.json`
  sets CORS + cache headers for the widget paths; see also the root `vercel.json`). **Layout (final, 2026-11-03):** the
  Vercel project's Root Directory is the repo root (`.`), `index.html` at the root is the landing page (bot URL box,
  embed snippet, links), the widget lives under `/avatar_widget/` (`/widget` and `/demo` rewrite to its demo), and the
  root `.vercelignore` uploads only `index.html`, `vercel.json` and `avatar_widget/`. **Redeploy from the repo root:**
  `vercel deploy --prod --yes`. A git push to `main` also deploys through the Vercel git integration.
  The bot itself cannot run on Vercel (long-lived SSE server, in-memory sessions, SQLite, local embedding model):
  it stays on a VPS behind Caddy (section 1); the embed tag points at it with `data-api`:
  `<script src="https://avatarwidget-five.vercel.app/loader.js" data-api="https://BOT-HOST" data-persona="auto-car-repair" defer></script>`
  Demo: `https://avatarwidget-five.vercel.app/?api=https://BOT-HOST` (the bot's `HUMAN_BOT_ALLOWED_ORIGINS` must include the Vercel origin).
  `data-api="https://bot.example.in"` is required when the loader is not served from the bot origin.
- The loader appends `?v=<VERSION>` to the panel URL (see `avatar_widget/loader.js`); bump `VERSION` on
  every release so CDN caches never mix old and new modules.
- Dealer page CSP: `script-src` and `frame-src` the widget origin; the panel needs `connect-src`,
  `media-src` and `img-src` for the bot origin, and `script-src` for the Simli host while the SDK is
  loaded from esm.sh (**open:** self-host a built `simli-client` bundle on the widget origin).
- `avatar_widget/serve.py` and `human_bot/web/demo.html` are dev-only.

## 7. Pre-launch checklist

- [ ] Keys rotated and supplied as env vars; `voice_clone/.env` not on the production box.
- [ ] `HUMAN_BOT_ALLOWED_ORIGINS` set to the dealer origins; bot started with `--host 127.0.0.1` behind the proxy.
- [ ] `HUMAN_BOT_BEHIND_PROXY=1`, `HUMAN_BOT_SITE_MIRROR=0`, `HUMAN_BOT_TTS_OPEN` unset.
- [ ] `KB_ADMIN_KEY` set, `KB_TRUST_PROXY=loopback`, `data/` on a backed-up volume, first backup taken.
- [ ] Proxy streams `/chat` and `/tts` (test: first bytes of a sentence clip arrive before the clip ends).
- [ ] `/health` on both services returns `ok: true`; uptime monitor attached.
- [ ] A real phone test on Android Chrome and iPhone Safari: greeting audible after the first tap, mic
      works on speakerphone without self-interruption, barge-in works, call ends cleanly. (**open:** not
      yet done on real devices; the audits list the iOS risks.)
- [ ] Consent and disclosure: the card says it is an AI in the first line; no transcripts are stored
      (see memory rule); DPDP consent text reviewed by the business.
- [ ] Voice provider decision: Edge (unofficial, free) vs Azure Speech (supported, paid). Same voices.

## 8. Still open after the 2026-11-02 pass

- Azure Speech or another supported TTS for production traffic.
- Self-hosted Simli SDK bundle; D-ID path untested since Simli became the default.
- Barge-in truncation is client-side only: an interrupted answer is stored whole in the session
  transcript (P2 in the server audit).
- Local query embeddings (would cut the 0.7 to 1.2 s knowledge lookup; needs a full re-embed).
- Real-device test matrix (see checklist).

## Service requests filed by the bot (2026-11-03)

Neha collects name, phone, car, problem and area (one question at a time), reads the request back, and on the
customer's "haan" the **server** submits it to the website backend, the same endpoint the Contact form uses
(`POST https://autocarrepair.in/api/v1/leads`, multipart, returns `lead_id`). The model never claims a
submission; the spoken result comes from the backend's answer.

| Variable | Production value | Why |
|---|---|---|
| `ACR_LEADS_MODE` | `live` (default `dry`) | `dry` = nothing is sent, the request is kept in the conversation record / CRM outbox and Neha says the team will call (no number invented). Switch to `live` after the website developer confirms the field names and allows the bot's server. |
| `ACR_LEADS_URL` | `https://autocarrepair.in/api/v1/leads` | The site's lead endpoint. |
| `ACR_LEADS_KEY` | if the developer issues one | Sent as `X-Api-Key`. |
| `ACR_LEADS_SOURCE` | `neha_bot` | `source` field, so bot leads are distinguishable in the backend/CRM. |

Fields sent: `name, phone, location, service (site category slug), source, message, custom_data[make|model|registration|conversation_id]`
(see `human_bot/crm/acr_leads.py`). `/health` (loopback) shows `leads.mode` and the number of dry runs.

## Vercel (static) + Render (servers) — the split that works (2026-11-03)

A Vercel deploy of this repo showed `404 NOT_FOUND` because the repository root had no page and Vercel can
only serve files. The repo now has `index.html` (landing page: enter the bot's HTTPS address once, open the
widget demo or the embed card, copy the embed snippet) and `vercel.json` (`/widget` → the demo). Import the
repo in Vercel with **no build command and the root directory as output**; that is all.

The brain cannot run on Vercel: `human_bot` (Python) and `kb` (Node + 1 GB for the local embedding model)
need a real host. `render.yaml` is a Render blueprint for both (region Singapore, persistent disks for
`human_bot/data` and `kb/data`, keys entered in the dashboard, never committed). After the first deploy:

1. Put the bot URL (e.g. `https://neha-bot.onrender.com`) into the Vercel landing page, or as
   `data-api` on the dealer site's script tag.
2. Add the Vercel origin and the dealer origins to `HUMAN_BOT_ALLOWED_ORIGINS` on the bot service.
3. Upload `kb/data/kb.sqlite` (the only copy of the crawl) to the kb disk once, or run a crawl there with
   `KB_ADMIN_KEY`.
Any VPS with Caddy (section 4) works the same way; Render is just the no-ops option.
