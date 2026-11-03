# Code map: what every file does, where the time goes, and how it scales

Written 2026-11-03 for the owner and the team. Three services, one repository:

```
VIDEO/
├── human_bot/       the brain and the call loop (Python, stdlib server)      port 4001
├── kb/              website knowledge: crawler + search (Node)                port 4100
├── avatar_widget/   the voice-only video widget dealers embed (browser JS)    port 4300 in dev
├── tran_moment/     avatar face + motion learned from footage (offline tools)
├── voice_clone/     consent-gated voice cloning scripts + the shared .env (keys)
├── deploy/          Caddyfile + systemd units
├── ARCHITECTURE.md  latency design, sequence, measurements, phased plan
├── DEPLOYMENT.md    environment variables, proxy, backups, pre-launch checklist
└── CODE_MAP.md      this file
```

## 1. One call, end to end (which file runs when)

```
dealer page  → avatar_widget/loader.js (launcher, iframe) → panel.html → src/widget.js (orchestrator)
   tap mic   → src/audio.js Mic (noise gate) → POST /stt ─┐
                                                          ▼
                              human_bot/server/app.py  _stt() → llm/client.py transcribe() (Groq Whisper)
   text      → POST /chat → app.py _chat():
                 1. memory/store.py recall block already in the prompt (returning customer)
                 2. backchannel "acha" (cached clip)                 persona/humanizer.py pick_backchannel
                 3. kb search ──► kb/src/server.js /api/knowledge/search (hybrid-local, ~30 ms)
                 4. llm/client.py stream_reply() (Groq → OpenAI → Gemini → OfflineServiceBrain)
                 5. each sentence: humanizer.humanize → SSE `sentence` → tts_job() starts synthesis now
   audio     → GET /tts?text&emo → app.py TtsJob streams MP3 from llm/client.py _edge_stream()
                 (hindi_script.py turns Hinglish into Devanagari for the hi-IN voice)
   face      → src/avatar.js: Simli live stream (lips from our audio) or the 2D puppet (motion-data.js)
   end       → POST /end → memory/store.py save + analysis (postcall/analyze.py) → crm/outbox.py → crm/bitrix24.py
```

## 2. human_bot/ (Python 3.12, no framework)

| File | Lines | What it does | Touch it when |
|---|---:|---|---|
| `server/app.py` | 1260 | The HTTP server. Routes: `/session`, `/chat` (SSE), `/tts` (chunked MP3, server-produced text only), `/stt`, `/end`, `/conversations` (CRM feed), `/avatar/*` (Simli/D-ID relay), `/health`, static files from `web/` (path-confined). Also: TTS job cache + prefetch + warm-up, per-IP rate limits, origin allow-list, body caps, session reaper, context cleaning, the greeting (returning-customer variant + recording disclosure). | Adding an endpoint, changing limits, changing what a session knows. |
| `llm/client.py` | 798 | Everything that talks to AI providers. `stream_reply()` with provider order and circuit breakers; `transcribe()` (Groq Whisper); `synthesize_stream()` (Edge TTS streamed, per-emotion rate/pitch, TTS breaker); `OfflineBrain` (showroom script) and `OfflineServiceBrain` (workshop script: car → problem → area → time → pickup; quotes website passages); voice style selection. | Changing providers, prompts to providers, timeouts, the fallback script, voices. |
| `llm/hindi_script.py` | 123 | Dictionary (~450 words) that turns Roman Hinglish into Devanagari for the Hindi voice. English words stay English. | A Hindi word is pronounced wrong: add it to `WORDS`. |
| `llm/knowledge.py` | 83 | Client for the kb service: `retrieve()` with a 2.5 s timeout and `knowledge_block()` (the strict-KB instruction + passages put into the system prompt). | Changing how much knowledge goes to the model. |
| `llm/simli_avatar.py`, `llm/did_avatar.py` | 100, 137 | Server-side relays to the two talking-head vendors; keys never reach the browser. | Switching avatar vendor. |
| `persona/persona_*.json` | | One file per brand: name, company, business type, discovery order, catalogue, guardrails, backchannels, gestures per emotion, knowledge website id. | Tone, questions order, what counts as hand-off. **This is the first place to change behaviour.** |
| `persona/prompting.py` | 187 | Builds the system prompt from the persona (language mirror policy, female verb forms, strict-KB rule, one question per turn). | Prompt wording. |
| `persona/humanizer.py` | 283 | Makes model text sound spoken: word caps, fillers budget, sentence splitting for TTS, `emotion_of()` tags, backchannel choice, gesture choice. | Delivery style, emotions, splitting. |
| `postcall/analyze.py` | 193 | After a call: sentiment, lead status (HOT/WARM/COLD), action items, unanswered questions. | Lead scoring rules. |
| `memory/store.py` | 327 | Conversation memory: SQLite `data/conversations.sqlite` + `data/audio/<id>/`. Saves the structured record (facts from the customer's words, intent, summary, transcript, analysis, audio), recalls earlier conversations by visitor id or phone, builds the "Returning customer" prompt block, merges the call audio. | What is stored, what the CRM gets, recall rules. |
| `crm/outbox.py` | | Delivery queue: one row per ended conversation, worker thread with backoff retries, idempotent, survives restarts. Works without a sink (rows wait). | Retry policy. |
| `crm/bitrix24.py` | | Bitrix24 inbound-webhook sink: finds or creates the Lead (`UF_CRM_CONVERSATION_ID`, phone fallback), fills title/name/phone/comments/source, adds the transcript as a timeline comment. | Field mapping, funnel stage, assignment. |
| `tools/loadtest.py` | 107 | N simulated customers; prints p50/p90 for session, knowledge, first sentence, final. | Before raising traffic. |
| `tests/test_crm_outbox.py` | | Outbox idempotency/retry and Bitrix24 mapping tests (no network). | |
| `web/embed.html` | 1179 | The older **text + voice card** (single file): mic gate, SSE chat, streamed TTS on one shared audio element, 2D puppet with photo mouth shapes, Simli composite, host postMessage (origin-pinned), visitor id, recording note. | The card dealers currently see on the demo page. |
| `web/widget.js` | 241 | Host-page launcher for the card (iframe + bubble), escapes dealer-supplied attributes. | |
| `web/demo.html`, `web/clips.html`, `web/index.html` | | Demo page, gesture trainer page, legacy full page. Dev only. | |
| `web/shot_compact.mjs` | 53 | Playwright check: typed turn + screenshots. | |

## 3. kb/ (Node 24, Express, node:sqlite)

| File | Lines | What it does | Touch it when |
|---|---:|---|---|
| `src/server.js` | 244 | REST API. `/api/knowledge/search` picks the path: **hybrid-local** (default) → hybrid-gemini → lexical; crawl management routes (admin key or loopback); `/health` with `degraded[]` and local-index coverage; builds local vectors at start and after each crawl; startup refuses non-loopback without `KB_ADMIN_KEY`. | Search behaviour, auth. |
| `src/embed/local.js` | 70 | The CPU embedding model (`Xenova/multilingual-e5-small`, int8): 10 ms per query, loads in ~20 s, ~670 MB RSS. | Changing the local model. |
| `src/embed/gemini.js` | 158 | Gemini embeddings for indexing and the quality query path: batching, retries for indexing, 2 s deadline + circuit breaker + in-flight coalescing for queries, keep-alive. | Quota handling. |
| `src/search/hinglish.js` | 30 | Hinglish → English automotive terms added to the query ("awaaz" → "noise sound"). | A Hinglish phrase retrieves the wrong page: add the term. |
| `src/store/sqlite.js` | 405 | The store: pages, chunks (Gemini vectors), `chunk_vectors` (local vectors), FTS5 index with triggers, cosine search, lexical search, rank fusion, backups (VACUUM INTO), WAL checkpoint, cross-process cache invalidation. | Schema, ranking. |
| `src/jobs.js` | 129 | One crawl job at a time: crawl → extract → chunk → embed → store; backup before a reindex wipe; deactivation guard. | Crawl policy. |
| `src/crawler/*.js`, `src/extract/extract.js`, `src/chunk/chunk.js`, `src/pipeline.js` | | Crawlee/Playwright crawl with robots/sitemap/URL rules; HTML → markdown-ish text; metadata chunking (1200 chars, 200 overlap); per-page pipeline. | Adding a site, chunk size. |
| `src/config.js` | 171 | Env + the website registry (ACR, Galaxy Toyota, Hans Hyundai); private-host guard. | New site, limits. |
| `src/cli.js`, `src/index.js` | | CLI (`crawl`, `search`) and the library entry. | |
| `test/*.test.js` | | 17 tests: URL rules, extraction/chunking, store API, hybrid fusion + local vectors. | |

## 4. avatar_widget/ (browser, ES modules, no build)

| File | Lines | What it does | Touch it when |
|---|---:|---|---|
| `loader.js` | 161 | The one `<script>` tag dealers add: shadow-root launcher, iframe to `panel.html?v=VERSION`, page context + visitor id, origin-pinned postMessage, `AvatarWidget.{open,close,…}` API. | Launcher look, versioning (`VERSION` on each release). |
| `panel.html` | 125 | The panel inside the iframe: portrait/video, status, mic and end buttons, tap-to-start overlay, eyelid blink band, safe-area padding, import map with `?v=`. | Panel layout. |
| `src/widget.js` | 204 | Orchestrator: session ↔ speaker ↔ mic ↔ avatar ↔ bridge; barge-in wiring (session owns the generation), wake lock, visibility handling, close-during-connect guards. | Call flow. |
| `src/session.js` | 166 | Turn state machine (idle → connecting → greeting → listening → thinking → speaking), transcript in memory, recovery line on a dead turn, `end()` → `/end`. | States, recovery. |
| `src/transport.js` | 368 | `SseTransport` (REST + SSE with a 20 s inactivity deadline, health cache, `ttsUrl(text, voice, emotion)`, `/stt?session_id`, `endSession`) and `WebSocketTransport` (same contract over WS). | Protocol. |
| `src/audio.js` | 362 | `Speaker`: serial clip queue, `<audio src=/tts…>` progressive playback, AudioContext unlock, analyser for the mouth, female-only browser-voice fallback. `Mic`: adaptive noise gate with pre-roll, stricter while she speaks, `setInterval` loop, `/stt` upload, Web Speech fallback. | Mic sensitivity, playback. |
| `src/avatar.js` | 459 | Puppet renderer: dt-based smoothing, gesture takes with cooldown/variation, idle noise + drift, blink scheduling, emphasis from the voice envelope; Simli live mode (PCM push) and D-ID mode. | Motion feel. |
| `src/motion-data.js` | 251 | GENERATED by `tran_moment/motion/fit_gestures.py`: 22 gesture tracks (incl. 9 variants) + idle statistics. Do not edit by hand. | Re-run the fit. |
| `src/events.js` | 22 | Tiny emitter. | |
| `test/smoke.mjs`, `test/motion.mjs`, `test/motion.html`, `test/unit/*.test.mjs` | | End-to-end call, headless render checks, 55 unit tests (session, speaker, mic gate, SSE/WS transports, motion). | |
| `serve.py`, `demo.html` | | Dev static server and demo page. Not for production. | |

## 5. tran_moment/ and voice_clone/

| Path | What it does |
|---|---|
| `tran_moment/prepared/face.jpg` | Neha's CGI portrait (served as `/avatar.jpg`). `path_b/` has the photo mouth shapes for the card's puppet. |
| `tran_moment/motion/extract_motion.py` | MediaPipe face landmarks → pose curves from reference clips (CPU). |
| `tran_moment/motion/fit_gestures.py` | Pose curves → keyframe tracks + variants + idle stats → `avatar_widget/src/motion-data.js` and `report.md`. |
| `voice_clone/.env` | **The shared secrets file** (Groq, Gemini, OpenAI, Simli, D-ID keys and settings). Read by human_bot and kb. Never commit, never print. |
| `voice_clone/scripts/neha_read_script.md` | Recording script for the voice artist (needs consent + dry recording before any clone). |
| `voice_clone/*.ipynb` | Colab notebooks for cloning (GPU work happens there, never on the laptop). |

## 6. Where the time goes now, and what to change next

Measured 2026-11-02/03 on the laptop (details and the sequence diagram in `ARCHITECTURE.md`):

| Stage | Now | File | Next step | Gain |
|---|---|---|---|---|
| Silence gate | 650 ms (card) / 700 ms (widget), **early STT at 350 ms** (done 2026-11-03: the clip so far is transcribed during the pause; if the customer continues the result is dropped) | `embed.html listenVad()`, `src/audio.js createGate()/Mic` | tune `earlyMs` from field recordings | ~0.3 s, done |
| Speech to text | 450 ms (Groq Whisper, batch) | `llm/client.py transcribe()` | streaming ASR with partial transcripts (needs a streaming provider or a GPU worker) | overlaps 0.4 s with the customer still talking |
| Round trips | `/stt` then `/chat` | `app.py` | one `/turn` request: audio in, SSE out, knowledge starts in-process | ~50-150 ms on mobile |
| Knowledge | **30 ms** (was 550-1200) | `kb/src/server.js` hybrid-local | done; keep the Hinglish term list growing | |
| LLM first token | ~200 ms (Groq) | `llm/client.py` | paid tier for concurrency; prompt kept short | |
| First audio | **1.0-1.5 s** (Edge connection setup) | `llm/client.py _edge_stream()` | Azure Speech, same voices, persistent connection | ~0.8-1.0 s, the biggest remaining item |
| Next clips | 10 ms (prefetched) | `app.py TtsJob` | done | |
| Reaction | "acha" at 20 ms | `app.py`, cards | done | |

Customer-perceived today: about 2.8 s from the end of their sentence to Neha's first word (was 4-5.5 s), with
an audible reaction at about 1.1 s. With early STT, `/turn` and Azure Speech the same path is about 1.4 s.
A cloud voice pipeline cannot go much below ~1 s (STT 0.3-0.45 + LLM 0.2 + TTS 0.3); what makes it feel
instant is the reaction sound and the first word arriving before the customer wonders.

## 7. 500 to 1000 customers a day: what it means and what breaks first

Assume 1000 calls/day, 70% in the 8 busiest hours (≈ 90/hour), average call 3 minutes:
average concurrency ≈ 4.5, busy-hour peak ≈ 12-15 simultaneous calls. The 10-user load test showed the
server itself holds that (session open 20 ms, knowledge p50 0.28 s under 10 parallel queries). The ceilings
are outside the code, in this order:

1. **LLM quota.** All three keys are free tier today; at 10 parallel turns every provider returned 429 within
   a second. A paid Groq key (or OpenAI) is the first purchase. The server already degrades gracefully
   (next provider, then the offline script) and bounds a stalled request to 8 s.
2. **Voice.** Edge TTS is unofficial and unmetered; at 15 parallel calls it may throttle or block without
   notice. Azure Speech (same Neerja/Swara voices) is the supported path and also the latency fix.
3. **Face.** Simli bills per minute and has a concurrent-session limit per plan: 1000 calls × 3 min = 3000
   minutes/day. Confirm the plan, or let the 2D puppet carry the load and reserve Simli for the demo page.
4. **Audio storage.** ≈ 1.7 MB per 3-minute call (customer utterances + Neha's clips + merged file) →
   ≈ 1.7 GB/day. Keep `call.m4a` only, 30-day retention, on object storage (S3/R2) rather than the VM disk.
   SQLite is fine at 1000 rows/day for years; back it up nightly (`VACUUM INTO`).
5. **Machine.** One VM with 2 vCPU / 4 GB runs the bot; the kb needs 1 GB for the local model. Behind Caddy
   (TLS, streaming on `/chat` and `/tts`). Above ~100 simultaneous calls: a second bot process behind the
   same proxy, with sessions moved to Redis (section 7 of `ARCHITECTURE.md`).

Before raising traffic, run `python human_bot/tools/loadtest.py --users 15 --turns 2` against the production
host and look at `first_sentence p90` and the `offline-script turns` count.

## 8. Bitrix24: what is built, what is needed from you

Built: every ended call is queued and pushed as one Lead with name, phone, car, problem, area, intent, lead
status, next step, summary, recording link, and the full transcript as a timeline comment; retries with
backoff; no duplicates (conversation id in `UF_CRM_CONVERSATION_ID`, phone fallback); the customer is never
kept waiting on the CRM; `/health` shows the queue. Unit-tested without network; the live push needs:

1. An **inbound webhook** URL from Bitrix24 (Developer resources → Other → Inbound webhook, scope `crm`) in
   `BITRIX24_WEBHOOK_URL`.
2. One custom Lead field `UF_CRM_CONVERSATION_ID` (string) created in the CRM settings.
3. The funnel stage id for new web leads (`BITRIX24_LEAD_STATUS`, default `NEW`) and, if you want
   auto-assignment, the advisor's user id (`BITRIX24_ASSIGNED_BY`).

Then restart the bot: the queued conversations flush, and each new call appears in Bitrix24 within seconds
of the customer hanging up.
