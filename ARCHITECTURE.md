# Auto Car Repair AI Video Service Advisor: architecture and latency report

Written 2026-11-02 against the brief "Production-Ready AI Video Service Advisor". Section 1 is the audit
of what actually exists; the brief assumes several things this repository does not have, and those are
named rather than pretended. Everything measured below was measured on the development laptop (Windows 11,
no GPU, 7.6 GB RAM) over a home connection; production numbers will be better on a server near Delhi.

## 1. Audit: what exists versus what the brief assumes

| Brief assumes | In this repository | Decision |
|---|---|---|
| React/Vite/TypeScript frontend | Two vanilla front-ends: `avatar_widget/` (voice-only loader + iframe panel, ES modules, no build) and `human_bot/web/embed.html` (card with text + voice) | Keep. A build step adds nothing for a 7-module widget; versioning is done with `?v=`. |
| FastAPI backend, asyncio, Pydantic | `human_bot/server/app.py`: stdlib `ThreadingHTTPServer`, HTTP/1.1, SSE for `/chat`, chunked MP3 for `/tts` | Keep for now. Thread-per-connection is fine to ~100 concurrent; the port to FastAPI is a later phase (section 7) and the API contract (section 5) is already stable so clients will not change. |
| Existing RAG, SQL tools, Salesforce | RAG: `kb/` (Crawlee crawl of autocarrepair.in, Gemini embeddings in SQLite, FTS5). No SQL tools. **No Salesforce**: the group's CRM is Bitrix24 (ACR + Galaxy Toyota); HubSpot is reachable through a connector but unused. | CRM push is a phase-3 item against Bitrix24, with an adapter interface so Salesforce can be dropped in if the business chooses it. Nothing to preserve today. |
| Redis, PostgreSQL | Neither. Memory rule: no Postgres/Ollama on this machine. Sessions in process memory (reaped), conversations in SQLite, TTS cache in memory. | Single-process is enough until ~100 concurrent; Redis becomes necessary only when the bot runs as more than one process (section 7). |
| vLLM / local LLM on GPU | Groq (qwen 27B) first, OpenAI and Gemini as fallbacks, offline rule script as last resort | Keep. Groq TTFT is ~0.2 s; a self-hosted 27B model needs an A100-class GPU to match that. |
| Faster-Whisper streaming ASR | Groq Whisper large-v3-turbo, batch per utterance (~0.45 s), browser recogniser fallback | Keep for now; partial transcripts are phase 2 (section 7). |
| Kokoro/Piper TTS | Edge neural voices (hi-IN Swara with Devanagari transliteration; unofficial endpoint), streamed per sentence, per-emotion prosody | Keep for the demo; **decision needed**: Azure Speech (same voices, supported, persistent connection) for production. Kokoro has no Indian female voice; Piper's `hi_IN-priyamvada` is noticeably robotic. |
| MuseTalk/LivePortrait avatar on GPU workers | Simli (hosted, WebRTC, lips from our audio) when configured; a 2D portrait puppet (learned gestures, CPU) otherwise | Keep. Self-hosted talking heads need a GPU per ~4 streams; Simli bills per minute and scales without us. |
| Prometheus/Grafana/OpenTelemetry | `/health` on both services with `degraded[]`, timings in the SSE `final` event and in kb responses (`latency_ms`), `human_bot/tools/loadtest.py` | Phase 3: export the same timings as Prometheus metrics. |

What was found broken in the audit and fixed the same day (details in `DEPLOYMENT.md` and `human_bot/README.md`):
path traversal to the secrets file, wildcard CORS with no limits, unbounded bodies, reindex wiping the only
crawl, a widget bug that dropped every answer after a barge-in, the portrait rendering at zero height.

## 2. Architecture (as built)

```
 dealer site                     widget origin / CDN                    bot host (behind Caddy, HTTPS)
 ┌──────────────┐  <script>      ┌──────────────────┐                   ┌────────────────────────────────┐
 │ page         │ ─────────────► │ loader.js        │  iframe           │ human_bot :4001                │
 │              │                │  shadow launcher │ ────────────────► │  /session /chat(SSE) /tts(MP3) │
 │              │                │ panel.html       │  postMessage      │  /stt /end /conversations      │
 │              │                │  src/*.js        │  (origin-pinned)  │  /avatar/* (Simli token relay) │
 └──────────────┘                └──────────────────┘                   │  memory/store.py (SQLite+audio)│
                                                                        └──┬──────────┬──────────┬───────┘
                                     Groq LLM + Whisper ◄──────────────────┘          │          │
                                     Edge TTS (→ Azure Speech in prod) ◄──────────────┘          │
                                     Simli (WebRTC face) ◄───── browser SDK ◄────────────────────┘
                                                                                                 │ loopback
                                                                        ┌────────────────────────▼───────┐
                                                                        │ kb :4100                       │
                                                                        │  hybrid-local: e5-small (CPU)  │
                                                                        │   + FTS5 bm25, rank fusion     │
                                                                        │  gemini vectors (quality path) │
                                                                        │  SQLite WAL, backups, crawler  │
                                                                        └────────────────────────────────┘
```

Trust boundaries: API keys only on the bot host; the browser receives a short-lived Simli token at most.
`/tts` speaks only text the server produced; origins, rates, body sizes and voices are enforced server-side.

## 3. One turn, with the measured times (laptop, 2026-11-02)

```
customer stops talking
  │ 0.65 s  silence gate (card / widget)
  ├─► /stt  Groq Whisper                               0.45 s        [phase 2: partial transcripts]
  ├─► /chat ──► backchannel "acha" (cached clip)        +0.02 s  ◄── customer hears a reaction
  │        ├─► kb hybrid-local (e5 + bm25, fused)       +0.03 s   (was 0.55-1.2 s with Gemini)
  │        ├─► Groq first token                         +0.2  s
  │        └─► first sentence event                     0.44-0.62 s after /chat  (was 0.9-1.5 s)
  │             └─► Edge TTS first chunk streamed        +1.0-1.5 s  ◄── first words of the answer
  └ remaining sentences: synthesized while she speaks; each next clip ~0.01 s from cache
```

End-to-end, customer-perceived: silence gate 0.65 + STT 0.45 + first sentence 0.5 + first audio 1.2 ≈
**2.8 s** to the first word of the answer (was 4 to 5.5 s at the start of the day), with "acha" heard at
≈1.1 s. The single largest remaining item is the TTS first chunk (Edge connection setup), which Azure Speech
with a persistent connection brings to ~0.3 s.

Per-stage measurements that fed the design: Gemini query embedding 0.52-1.2 s; local e5-small query 9-11 ms,
full ACR corpus (919 chunks) embedded once in 186 s, kb process RSS 669 MB with the model loaded; FTS5
answer ~1-10 ms; Edge first chunk 0.8-2.2 s, total clip 1.2-4.6 s; Groq TTFT ~0.2 s; STT 0.45 s.

Retrieval quality check (12 customer questions, Hinglish and English): hybrid-local matched the Gemini
top page on 9, and the three misses ("free pickup and drop", "battery dead", "tyre puncture") are questions
on which Gemini's own top result was also a generic page. The Hinglish keyword expansion
(`kb/src/search/hinglish.js`) is what closed the gap for "brake se awaaz", "AC thanda", "dhuan".

## 4. Knowledge retrieval design (implemented)

Three paths, chosen per request, reported as `mode` in the response:

1. **hybrid-local** (default, `KB_QUERY_PROVIDER=local`): expand Hinglish terms → embed the query with
   `Xenova/multilingual-e5-small` (int8 ONNX, CPU, 10 ms) → top-20 by cosine over the local vectors
   (`chunk_vectors` table) + top-20 FTS5 bm25 → reciprocal-rank fusion → at most 2 chunks per page → k.
   Used only when the model is loaded and every chunk of the site has a local vector (never a half-built index).
2. **hybrid-gemini**: Gemini query embedding inside a 2 s deadline, fused with FTS5. Circuit breaker opens
   after 3 failures for 30 s. Selected with `?provider=gemini` for A/B checks, or when the local index is not ready.
3. **lexical**: FTS5 only, when both embeddings are unavailable.

Local vectors are built in the background at startup and after every crawl job (resumable, persisted),
so new pages get vectors without an API call. Gemini vectors stay as the indexing/quality path; the two
never mix inside one search.

Still open from the brief: PDF/OCR ingestion, reranking (the fused list is good enough at this corpus size;
a cross-encoder would cost 100-300 ms on CPU), Redis FAQ cache (the in-process query cache does this).

## 5. API contract (unchanged for clients; additions are optional fields)

| Endpoint | Request | Response |
|---|---|---|
| `POST /session` | `{persona?, sessionContext:{page:{url,title,model}, visitor?}}` | `{session_id, greeting, chunks[{text,emotion,gesture}], provider, persona, returning, recording}` |
| `POST /chat` | `{session_id, text}` → `text/event-stream` | events `meta{generation,provider}`, `backchannel{text}`, `sources{results}`, `sentence{index,text,emotion,gesture}`, `final{text,chunks,latency_ms,words}`, `interrupted`, `error` |
| `GET /tts?text=&emo=&voice=&prefetch=1` | server-produced text only | chunked `audio/mpeg` (first bytes ~1 s), or 202 for prefetch, or 403/429/503 |
| `POST /stt?session_id=` | raw audio body (webm/mp4/wav, ≤ 8 MB) | `{text, ms}`; 503 → use the browser recogniser |
| `GET /tts_cues?text=&emo=` | same text/emo as `/tts` (server-produced only) | `{cues:[{s,e,v}], map}` lip timeline from Edge word boundaries; `[]` when the voice gave no timing (card falls back to the loudness analyser) |
| `POST /end` | `{session_id}` | `{ok, conversation_id, turns, lead}` |
| `GET /conversations[?visitor=&phone=&limit=]`, `/conversations/<id>`, `/conversations/<id>/audio/<file>` | loopback or `X-Admin-Key` | structured records (CRM feed) |
| `GET /health` | | `{ok, degraded[], ...}` (detail on loopback) |
| kb `GET/POST /api/knowledge/search` | `website, q, k, min_score, page_type, provider?` | `{mode, latency_ms, results[{score,url,title,text,...}]}` |

## 6. Booking and CRM workflow (designed; needs the systems)

The conversation already collects, validates (from the customer's own words only) and stores: name, phone,
car, area, problem, intent, agreed next step. What is missing is the two external systems:

1. **Availability**: a `BookingProvider` interface (`slots(location, date)`, `book(...)→reference`,
   `status(reference)`). Until a workshop system exposes slots, Neha offers a call-back, never a confirmed
   slot ("Do not claim an appointment is booked until the booking system confirms it" is already how the
   prompt and the offline script behave).
2. **CRM**: a `CrmSink` interface (`upsert_lead(record)` idempotent on `conversation_id`, retried with
   backoff, errors logged, never reported as success to the customer) with a Bitrix24 implementation first
   (inbound webhook) and Salesforce as an alternative implementation if the business moves. Feed: the
   `/conversations` record. Insurance-claim intent additionally triggers the WhatsApp template (Wati) and a
   telephony call (Exotel/Knowlarity/Qcall: decision pending).

## 7. Phased plan

| Phase | Item | Expected gain | Status |
|---|---|---|---|
| 1 (done today) | hybrid-local retrieval | knowledge 0.55-1.2 s → 0.03 s; first sentence 0.9-1.5 → 0.5 s | done, measured |
| 1 (done today) | streamed TTS, prefetch, backchannel first, silence gate 650 ms, LLM order | first audio 1.5-4.6 s → ~1.2 s; reaction at 0.02 s | done, measured |
| 1 (done today) | security/robustness fixes, recording + memory, expressive voice, motion variants | | done |
| 2 | early STT: send the clip at 350 ms of silence, keep listening; use the result if silence reaches 650/700 ms | ~0.3 s per turn | done 2026-11-03 (card `listenVad`, widget `createGate` "early"/"resume", unit-tested) |
| 2 | merge `/stt` + `/chat` into `/turn` (audio in, SSE out) | one round trip, knowledge starts in-process | planned |
| 2 | Azure Speech with persistent connection (or stream Edge chunks earlier) | first audio 1.2 s → ~0.3-0.5 s | needs account decision |
| 2 | partial transcripts (streaming ASR) so retrieval and the LLM start before the customer finishes | overlaps 0.4-0.6 s | needs a streaming ASR provider or a GPU worker |
| 3 | Bitrix24 sink, Wati template, telephony call on claim intent | business flow | needs credentials |
| 3 | Prometheus metrics from the timings already emitted; Grafana board | observability | planned |
| 3 | FastAPI port behind the same contract; Redis sessions; 2+ bot processes behind Caddy | >100 concurrent | when load requires |

Concurrency today, measured with `python human_bot/tools/loadtest.py` (section 8):

| Run | session open | knowledge | first sentence | note |
|---|---|---|---|---|
| 5 users × 1 turn | p50 0.02 s | p50 0.08 s | p50 0.68 s, one turn 61 s | the 61 s turn was a stalled provider socket: the per-request deadline was 60 s, now 8 s (`HUMAN_BOT_LLM_TIMEOUT_S`) |
| 10 users × 1 turn | p50 0.02 s | p50 0.28 s | p50 12.3 s | **all three LLM keys are free tier**: Groq, OpenAI and Gemini each returned 429 within the first second; turns then waited out the (then 12 s) deadline and fell back to the offline script |

So the server itself holds 10 concurrent turns (knowledge and sessions stay fast); the ceiling is the
**free-tier provider quotas**, which allow roughly 3 to 5 simultaneous customers. Before 10/25/50/100
concurrent customers: a paid Groq (or OpenAI) key, and re-run the load test at each step. The design does
not assume users per GPU because no GPU is in the path.

## 8. How to verify

```
node --test avatar_widget/test/unit/*.test.mjs       # 55 tests
node avatar_widget/test/motion.mjs                   # puppet render checks
node avatar_widget/test/smoke.mjs                    # end-to-end call (needs :4001 and :4300)
node human_bot/web/shot_compact.mjs                  # embed card typed turn + screenshots
cd kb && npm test                                    # 17 tests incl. hybrid fusion + local vectors
python human_bot/tools/loadtest.py --users 10 --turns 2
```

## 8. Latency review, 2026-11-03 (measured on the live laptop bot, :4001)

| Stage | Measured | Verdict |
|---|---|---|
| reaction ("hmm" / "acha" backchannel) | 3-13 ms after /chat | already milliseconds (cached clip) |
| knowledge (kb hybrid-local) | 14-84 ms direct; 35-315 ms inside a turn under CPU load | fine; now prefetched at /stt so it leaves the critical path |
| STT (Groq Whisper, 9 s clip) | 418-470 ms | fine; early STT at 350 ms of silence already overlaps it |
| LLM first token | Groq 0.2-0.5 s when it answers; **429 on the free tier, then OpenAI-compatible 429, then Gemini 8 s timeout -> 9.3 s** before the offline script took over | the real problem: provider quota, not code |
| TTS first byte | cached 23 ms; fresh Edge clip 1.0-2.2 s | the fixed floor until Azure Speech (persistent connection, ~0.3 s) |
| offline script answer (all providers down) | 36-141 ms whole turn | the floor for scripted answers |

What "milliseconds" means here: a reaction sound, a cached line and a scripted answer are already in the
tens of milliseconds. A model-generated spoken answer cannot be: STT 0.3-0.45 s + first token 0.2 s + first
TTS chunk 0.3-1.0 s is a physical floor of about 0.8-1.6 s, and today's 2.8 s is mostly Edge's connection
setup plus provider stalls.

Done in this pass:
- `/stt` starts the kb lookup for its transcript in a background thread; `/chat` with the same text reuses
  it (Session.prefetch_knowledge / knowledge). Saves the whole kb time whenever the card's early STT fires.
- Opening interjections ("Acha, ...", "Haan ji, ...", "Bilkul! ...") are emitted as their own first `sentence`
  the moment the comma arrives, using the warm backchannel clip when one exists, so the customer hears her
  react before the first fresh Edge clip (~1 s) is ready.
- `HUMAN_BOT_LLM_TIMEOUT_S=4` (was 8) in the env: a stalled provider is abandoned after 4 s instead of 8.

Still the biggest levers, in order (decisions, not code):
1. Paid Groq tier (or another fast provider with real quota): removes the 429 stalls that turned 0.5 s turns
   into 9 s turns during this review.
2. Azure Speech for TTS with a persistent connection: first audio 1.0-2.2 s -> ~0.3 s.
3. Streaming ASR (partial transcripts) so retrieval and the model start before the customer stops talking.
4. Merge `/stt` + `/chat` into one `/turn` request (one round trip less on mobile).

## Capacity and latency (measured 2026-11-03, hosted pair on Render free, probe from Delhi)

`python human_bot/tools/latency_probe.py <bot-url> <kb-url> 3,6,10` runs one full conversation with
per-step timings, then N conversations in parallel (session + two chat turns each) and prints p50/p95.

| Step (one warm conversation) | Hosted (free) | Laptop |
|---|---|---|
| card HTML (gzip, 31 KB on the wire) | 340-370 ms | 10 ms |
| kb search, server side (gemini vector + FTS; cached query) | 80-950 ms | 40-190 ms (hybrid-local) |
| /session | 390-610 ms | 28 ms |
| /chat first token | 325-620 ms | 3-50 ms |
| /tts first byte (Edge, Swara) | 300-400 ms | 14 ms |

Roughly 300 ms of every hosted number is the Delhi -> Singapore round trip through Render's proxy;
the server itself spends 30-80 ms per turn on the laptop.

| Parallel conversations | Hosted free, before | Hosted free, after this pass | Laptop |
|---|---|---|---|
| 3 | reply p50 1.1 s | reply p50 0.4 s, p95 0.6 s | 0.17 s |
| 6 | reply p50 3.9 s | reply p50 2.1 s, p95 2.5 s | 0.2 s |
| 10 | 5 of 10 got 502, p50 12 s | 6 of 10 got 502, p50 8.8 s | 0.17 s, p95 0.55 s |
| 20 | - | - | 0.5 s, p95 1.6 s |

What the pass changed: bot -> kb over Render's private network instead of the public HTTPS proxy
(`KB_URL=http://neha-kb:10000`); the card paints the face before the 2 MB of reaction frames arrive
(frames stream in, greeting smile and blink first); gzip + ETag/304 for the card's text assets;
`/health` no longer 503s when the LLM keys are rate-limited (that restarted the instance mid-call);
per-provider LLM timeout 4 s; the local embedding model stays off on the free kb (it needs ~550 MB).

The ceiling is the free instance: 0.1 vCPU, 512 MB, sleeps after 15 min, one instance. Six
simultaneous conversations are fine; ten saturate the CPU and Render's proxy answers 502. 500-1000
visitors a day is 1-2 conversations at a time on average and 5-10 at the peak hour, so free plan
survives a soft launch but not a campaign.

Scaling path, in order:
1. **Render starter for the bot (0.5 vCPU, 512 MB) and standard for the kb (1 vCPU, 2 GB)**: no
   cold starts, ~3-5x the concurrent capacity, and the kb can run the local model (search 40 ms
   instead of 300-900 ms). Disks come with it: recordings survive deploys. About $7 + $25 a month.
2. **Paid Groq key**: the free key is shared by every environment and hits 429 within minutes of
   real traffic; then every answer is the offline script. This is the first real limit, before CPU.
3. **Vertical before horizontal**: one bigger instance handles ~30-40 conversations at once. Only
   beyond that add instances, and then sessions (in memory today: `SESSIONS`, TTS cache, outbox)
   need either sticky routing by `session_id` or a shared store (Redis / Postgres). Sticky routing
   is the cheap first step: the load balancer hashes the `session_id` header/cookie to an instance.
4. **CDN for the puppet assets** (`/avatar_puppet/*`, versioned, immutable): 7 MB per first visit
   served from the edge, not from the Python process. Vercel or Cloudflare in front of the bot URL.
5. **Region**: Singapore is the nearest Render region; a Mumbai VPS (Caddy + the same two processes,
   DEPLOYMENT.md section 4) would take ~250 ms off every request for Delhi NCR customers.
