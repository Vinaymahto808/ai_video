# avatar_widget — DaveAI avatar trace and a clean implementation for this project

Date of trace: 2026-11-01. Target: <https://www.iamdave.ai/> (DaveAI / Sociograph Solutions, Bengaluru).
Method: static capture only (curl with a Chrome UA, bundle and inline-script extraction, Wayback and
certificate/npm/GitHub lookups). No account, no authenticated surface, no automated interaction with
DaveAI's servers beyond fetching public pages. REA's MCP server was not registered in this session,
so the same steps were run by hand; raw captures are in [evidence/](evidence/).

Every statement below is labelled **observed** (seen in a capture), **inferred** (follows from
observations) or **unknown** (not recoverable from public artifacts).

---

## 1. Architecture & findings

**Observed: the public DaveAI website does not ship the avatar widget.**
`www.iamdave.ai` is WordPress 6 + Elementor Pro 3.30 + WP Rocket. Its 24 script tags are all
WordPress/Elementor/jQuery/GTranslate assets ([evidence/homepage_scripts.txt](evidence/homepage_scripts.txt)).
The one tempting file, `wp-content/cache/min/1/en_US/sdk.js`, is WP Rocket's cached copy of the
**Facebook JS SDK**, not a DaveAI SDK ([evidence/sdk_js_head.txt](evidence/sdk_js_head.txt)). There is no
iframe other than Google Tag Manager, no WebSocket, no `wss://`, no XHR to a DaveAI API, and no
inline bootstrap for an avatar on the homepage, `/platform/`, `/solutions/virtual-sales-avatar/`,
`/solutions/gryd/`, `/gryd/`, or the Maruti Suzuki / BYD case-study pages.

**Observed: no public SDK, docs or demo host exists today.**
`docs.`, `api.`, `apps.`, `cdn.`, `gryd.iamdave.ai` do not resolve. npm has zero packages matching
"iamdave"; GitHub has no vendor repo. The Internet Archive has no copy of a docs host. The archived
`api.iamdave.ai` (2019) was a login portal titled "Dave.Ai – Intelligent Sales Companion" with static
assets on `d2cnp23b72g6na.cloudfront.net`; only `/`, `/login` and `/page/*` were captured
([evidence/api_iamdave_ai_2019_text.txt](evidence/api_iamdave_ai_2019_text.txt)). `dev.iamdave.ai`
(2024) was a staging copy of the WordPress site.

**Observed: customer sites named in the case studies do not carry it now.**
`marutisuzuki.com`, `nexaexperience.com` (live and the 2022 archive) and `heromotocorp.com` contain no
`iamdave` / `dave.ai` / `i2ce` reference. The BYD story links to a WhatsApp number
(`wa.me/917303950845`), i.e. the BYD deployment is a WhatsApp bot, not a web avatar.

**Inferred: how the product is delivered.** DaveAI's own pages describe the avatar as available on
"Website, Mobile App, AR/VR, Kiosk" and on "WhatsApp, Google, Meta, Telegram", integrated through
"a user-friendly API", and sold per enterprise with onboarding ("Integrate & Launch … we ensure
everything runs smoothly"). Together with the absent public SDK, the 2019 login-only API host and the
per-brand case studies, the consistent reading is a **tenant-scoped, vendor-managed deployment**:
DaveAI (or the OEM's agency) embeds a per-tenant bundle on the customer's pages; nothing is
self-serve or publicly downloadable. That is why the handshake, payload schema and avatar
rendering pipeline could not be captured from public artifacts.

**Unknown (cannot be established from public evidence):** transport (WebSocket vs HTTP streaming),
authentication headers, message schema, whether the face is 3D/WebGL, pre-rendered video or a
server-streamed talking head, and any rate limits. Anything more specific would be speculation.

### What this means for our design

The useful output of the trace is therefore not a copy of DaveAI's wire protocol but the set of
**architectural properties a product like this must have**, which our existing `human_bot` already
implements most of and which this folder packages cleanly:

| Property | Why it matters | Where it lives here |
|---|---|---|
| One-tag embed, isolated from host CSS | OEM/dealer CMSs (WordPress, AEM, React SPAs) must not break it | `loader.js` (closed shadow root + iframe) |
| Tenant/persona selection at embed time | one bundle, many brands | `data-persona`, `data-api` |
| Explicit turn state machine with barge-in | voice UX falls apart without it | `src/session.js` |
| Sentence-level streaming so speech starts in ~1 s | perceived latency | `src/transport.js` (SSE) + `src/audio.js` queue |
| Server-side secrets for the avatar vendor | API keys never in the browser | `src/avatar.js` via human_bot `/avatar/*` relay |
| Graceful degradation | WebRTC blocked, no mic, no TTS, no STT | live → puppet; server TTS → browser; server STT → Web Speech |
| Page context in, lead signal out | sales value of the widget | `aw:ctx` / `aw:lead` bridge messages |

---

## 2. Evidence & endpoints

### 2a. Captured from DaveAI (public)

| Item | Value | Status |
|---|---|---|
| Marketing origin | `https://www.iamdave.ai` (WordPress 6, Elementor Pro 3.30.1, WP Rocket, GTM `GTM-PPGSPBRH`) | observed |
| "sdk.js" on homepage | WP Rocket-minified Facebook JS SDK (`window.FB`) | observed |
| Legacy platform host | `api.iamdave.ai` → 2019 login portal, static on `d2cnp23b72g6na.cloudfront.net` | observed (archive) |
| Legacy company | SocioGraph Solutions Pvt Ltd (`sociographsolutions.in`, `@socio_graph`) | observed |
| Delivery surfaces | Website, Mobile App, AR/VR, Kiosk; WhatsApp, Google, Meta, Telegram | observed (copy) |
| BYD deployment | WhatsApp bot at `wa.me/917303950845` | observed |
| Widget bundle / API / schema / auth | none public | unknown |

Full probe log: [evidence/probes.txt](evidence/probes.txt).

### 2b. Contract this implementation uses (our own `human_bot` server)

| Endpoint | Purpose | Schema |
|---|---|---|
| `POST /session` | open call, returns greeting + delivery chunks | [schemas/session.schema.json](schemas/session.schema.json) |
| `POST /chat` (SSE) | one user utterance → `meta, backchannel?, sources?, sentence*, final \| interrupted \| error` | [schemas/chat-events.schema.json](schemas/chat-events.schema.json) |
| `GET /tts?text=` | WAV/MP3 for one sentence (female Indian voice only) | — |
| `POST /stt` (audio body) | transcript; `503` ⇒ use browser recogniser | — |
| `POST /avatar/start\|sdp\|ice\|say\|stop` | relay to Simli / D-ID; browser receives only a session token or SDP | — |
| `GET /health` | feature flags: `stt`, `tts`, `avatar.ready`, `avatar.provider`, `kb` | — |
| host ⇄ iframe | `aw:ctx, aw:open, aw:close` / `aw:ready, aw:state, aw:close, aw:lead, aw:error` | [schemas/bridge.schema.json](schemas/bridge.schema.json) |

---

## 3. Clean implementation (this folder)

Stack matches the rest of `VIDEO/`: **vanilla ES modules, no bundler, stdlib Python dev server**.
Nothing is copied from DaveAI; the code is original.

```
avatar_widget/
  loader.js          one-tag embed: closed shadow-root launcher, iframe panel, postMessage bridge, window.AvatarWidget API
  panel.html         voice-only call UI: avatar face, mic + end-call icons, no text box, no chat log
  src/events.js      tiny emitter
  src/session.js     ConversationSession: idle→connecting→greeting→listening→thinking→speaking→ended, barge-in generations
  src/transport.js   SseTransport (human_bot REST+SSE) and WebSocketTransport (same contract) + an SSE parser
  src/audio.js       Speaker: sentence TTS queue with cut(); Mic: loudness-gated utterance recorder → /stt, Web Speech fallback
  src/avatar.js      Avatar: live (Simli/D-ID via server relay) or puppet (persona gesture tracks + voice-band mouth), same pose API
  src/widget.js      orchestrator wiring the four above + bridge
  schemas/           JSON schemas of every payload
  serve.py           static dev server with CORS + frame-ancestors *
  demo.html          fake dealer page with the one-line embed and a bridge-event log
  test/smoke.mjs     Playwright end-to-end check (SSE or WS via SMOKE_URL)
  test/motion.html   motion harness: fake transport/speaker, exposes every transform channel + exact cover test
  test/motion.mjs    Playwright motion assertions (roll, nod, sway, breathing, frame-edge cover, cut)
  test/unit/         node --test suites: SSE parser + SSE deadline, session state machine + recovery, WS transport,
                     Speaker generations + voice choice, mic gate, gesture tracks
  evidence/          raw captures from the trace

tools/ws-shim.mjs    dependency-free WebSocket server that bridges the WS transport to human_bot
```

### Run it

```powershell
# 1. the brain (already how human_bot runs; 4001 = Galaxy Toyota, 4002 = Hans Hyundai, or pick a persona)
python human_bot\server\app.py --port 4001 --persona auto-car-repair

# 2. the widget files
python avatar_widget\serve.py --port 4300

# 3. open the fake dealer page
start http://127.0.0.1:4300/demo.html?api=http://127.0.0.1:4001
```

### Embed on a real site

```html
<script src="https://WIDGET-HOST/loader.js"
        data-api="https://BOT-HOST" data-persona="auto-car-repair"
        data-label="Talk to Neha" data-accent="#0b2f6b" defer></script>
```

`data-api` is **required whenever `loader.js` is served from a CDN or any host other than
human_bot**: without it the loader assumes the bot lives at the same origin as `loader.js` and every
`/session`, `/chat`, `/tts` call goes to the CDN. The panel and its modules are loaded with
`?v=<VERSION>` (`VERSION` at the top of `loader.js`, also exposed as `AvatarWidget.version`); bump it
on every release so cached copies of `panel.html` and `src/*.js` can never mix. The page context
sent to the bot is `location.origin + location.pathname` only (no query string or fragment).

```js
AvatarWidget.open(); AvatarWidget.close();
AvatarWidget.on("lead",  d => crm.push({ session: d.session_id, turns: d.turns }));
AvatarWidget.on("state", s => console.log(s));   // listening | thinking | speaking | ended
```

### Swapping the transport

`createWidget({ ws: "ws://127.0.0.1:4101" })` selects `WebSocketTransport`; `?ws=` on `demo.html`
(or `data-ws` on the embed tag) does the same. The session, audio and avatar layers do not change.
TTS stays on HTTP (`?api=` is also the `ttsBase`), so no audio is tunneled over the socket.

A working backend ships in this repo — no dependencies, ~300 lines of RFC 6455:

```powershell
node tools\ws-shim.mjs --port 4101 --bot http://127.0.0.1:4001
start "http://127.0.0.1:4300/demo.html?api=http://127.0.0.1:4001&ws=ws://127.0.0.1:4101"
```

| Client frame | Server reply |
|---|---|
| `{id, type:"health"}` | `{reply_to:id, data:{ok, stt, tts, avatar, kb, …}}` (relayed `/health`) |
| `{id, type:"session", persona, context}` | `{reply_to:id, data:{session_id, greeting, chunks, persona}}` or `{reply_to:id, error}` |
| `{id, type:"avatar", path, body}` | `{reply_to:id, data:{ok, status, data}}` (relayed `/avatar/*`) |
| `{type:"chat", session_id, text}` | `{type:"turn", event, data}` … until `final` / `error` / `interrupted` |
| `{type:"abort"}` | no reply; the in-flight turn gets `interrupted` |

Both sides are bounded: connect timeout, RPC timeout, per-turn timeout, and a socket close rejects
pending RPCs and resolves the live turn with an `error` event. STT is the one asymmetry — binary
uploads are not part of the WS contract, so `Mic` uses the browser recogniser in WS mode.

---

## 4. Constraints & considerations

- **Nothing to reuse from DaveAI.** There is no public endpoint, token or schema to call; any attempt
  to drive a tenant deployment on an OEM's site would be unauthorised and is out of scope.
- **Browser policies that shape the design.** Mic and autoplay need a user gesture: the launcher
  click on the host page opens the iframe, and `panel.html` starts audio inside that gesture chain.
  The iframe is created with `allow="microphone; autoplay"`. Where that delegation is not honoured
  (iOS Safari, strict autoplay settings) the panel detects a suspended `AudioContext` on `aw:open`
  and shows **Tap to start** so the first tap lands inside the iframe; every tap in the panel also
  resumes the context, and the Speaker resumes it again before each clip.
- **Turn robustness.** `SseTransport` aborts a `/chat` stream after 20 s without an event and ends
  the turn with an `error`; the session then speaks `"Ek second, line thodi kharab hai. Dobara
  boliye."` through the normal sentence path and returns to listening. The `Speaker` never bumps
  the generation counter itself: `cut(generation)` adopts the session's value, so an interruption
  cannot leave later sentences silently dropped.
- **Mic gate.** Adaptive noise floor (3× the floor, ×2.5 while the avatar speaks), 60 ms of sustained
  loudness before speech is reported (220 ms while speaking), recorder started on the first loud
  frame so the first word survives, discarded if the burst was noise; the loop runs on a 40 ms
  interval so a background tab keeps listening, and the widget drops the mic while hidden and
  re-arms it when visible (with a screen wake lock during the call). A public HTTPS page cannot load a widget
  from `http://127.0.0.1` (mixed content + Local Network Access), so production needs one HTTPS origin
  (Caddy / Cloudflare Tunnel, see `human_bot/web/EMBED.md`).
- **Cross-origin.** `serve.py` and `human_bot` send `Access-Control-Allow-Origin: *`,
  `Content-Security-Policy: frame-ancestors *`, `Cross-Origin-Resource-Policy: cross-origin`. Host
  CSPs must allow-list the widget origin in `script-src` and `frame-src`. The postMessage bridge pins
  origins on both sides (no wildcard once the first `aw:ctx` arrives).
- **Rate limits and cost.** Live avatar minutes (Simli free tier 50 min/month, D-ID per-minute) are
  the expensive resource: the panel starts the stream only when the call starts and tears it down on
  end/pagehide, falling back to the puppet renderer. TTS clips are cached server-side per sentence.
- **Project rules honoured.** Voice-only UI (no text input, no transcript shown or stored); answers
  come from the strict knowledge block the server injects (`sources` event is surfaced, not bypassed);
  every voice path chooses a female Indian voice; the avatar face is the project's own AI-generated
  portrait served by human_bot at `/avatar.jpg`.
- **Puppet motion.** The fallback is a still portrait, so "body motion" is a small set of transform
  channels driven by one rAF loop: nod is written as vertical travel (`--ty`, 1°≈0.35% of frame),
  head roll is clamped to ±3.6°, sway is `--tx`, and breathing/lean change `--zoom` around a
  `1.14` base that provides the corner headroom. The gesture vocabulary is the persona's own
  (`avatar.gestures`: `small_nod`, `tilt_head`, `head_wobble`, `lean_in`, …); each name has keyframe
  tracks in `avatar.js`, softened with smoothstep and cross-faded by per-channel smoothing, and a unit
  test asserts every declared gesture has a track. `listen()/think()/speak()` add a resting pose bias,
  `backchannel()` plays the Indian agreement wobble once, and `cut()` clears in-flight tweens so a nod
  cannot continue through a barge-in. `prefers-reduced-motion` reduces it to a static scale.
- **Puppet lip-sync.** The same loop drives a mouth patch from **voice-band energy**
  (`AnalyserNode` FFT, 180 Hz–4 kHz) blended with RMS, smoothed with a fast attack and slow release
  so it opens on speech and closes cleanly instead of chattering. `--mouth`, `--jaw` and `--mouth-w`
  are its CSS knobs; `destroy()` cancels the rAF loop and resets every channel. All channels are
  written on the portrait's **container**, not the `<img>`: the patch is a `::after` of that
  container, and a sibling cannot inherit custom properties from the image, which had silently left
  the mouth shut. It is still an overlay, not landmark tracking — the live providers do real
  lip-sync.
- **Open items.** `WebSocketTransport` now has a reference server (`tools/ws-shim.mjs`) but no STT
  over the socket (browser recogniser is used). Live avatar minutes are a provider quota: when
  Simli refuses a new session the widget logs it and falls back to the puppet, which is what
  happened during the final WS check (the account had hit its session limit, not a code fault).

## 5. Verification

`test/smoke.mjs` (Playwright from `kb/node_modules`, headless Chromium with a fake mic) loads
`demo.html`, opens the launcher, and checks the iframe session. Run with both servers up:

```powershell
node avatar_widget\test\smoke.mjs
```

Result on 2026-11-01: launcher mounted; panel iframe loaded; `/health → /avatar/start → /session`
handshake completed; Simli live stream connected (LiveKit, India South) so `aw:ready` reported
`mode: "live"`; greeting played through `/tts`; an injected utterance produced
`backchannel → sources → sentence → final` over SSE with barge-in; bridge states
`connecting → greeting → speaking → thinking → speaking` reached the host page; close tore the
room down. No page errors. Screenshot: `test/smoke_open.png`. `window.__aw` in the panel exposes
the widget (`session`, `start()`, `end()`) for this kind of test.

**Re-verified later the same day** against a freshly started `human_bot` (`:4001`,
`groq:qwen/qwen3.8-27b`, edge TTS, Simli ready) and `serve.py` (`:4300`): same result — launcher →
iframe → `/health` → `/avatar/start` → `/session` → LiveKit room in India South, `aw:ready`
`mode: "live"`, greeting via `/tts`, injected turn gave
`barge-in → user → state → backchannel ("acha") → sources → sentence ×2 → final`, and teardown
released the room. No page errors.

### Hardening checks (added after the trace)

```powershell
# unit tests: no browser, no servers, no dependencies (list the files: node's glob is shell-dependent on Windows)
node --test --test-timeout=15000 avatar_widget\test\unit\parse-sse.test.mjs `
  avatar_widget\test\unit\session.test.mjs avatar_widget\test\unit\transport-ws.test.mjs `
  avatar_widget\test\unit\transport-sse.test.mjs avatar_widget\test\unit\speaker.test.mjs `
  avatar_widget\test\unit\mic-gate.test.mjs avatar_widget\test\unit\avatar-motion.test.mjs

# puppet motion, no bot needed (serve.py on :4300 is enough)
node avatar_widget\test\motion.mjs

# end-to-end over SSE
node avatar_widget\test\smoke.mjs

# end-to-end over the WebSocket shim
$env:SMOKE_URL="http://127.0.0.1:4300/demo.html?api=http://127.0.0.1:4001&ws=ws://127.0.0.1:4101&persona=auto-car-repair"
node avatar_widget\test\smoke.mjs
```

- **55 unit tests, all passing (2026-11-02).** They cover: an SSE event whose terminating blank line
  never arrives, events split across reads, CRLF/comment/multi-line data, non-JSON payloads, abort
  mid-stream, the 20 s inactivity deadline and the shared `/health`; the full session state machine
  including barge-in generation drops, illegal input before/after a call, idempotent `end()`,
  error openings and the spoken recovery line after a failed turn; the WS transport's connect/RPC/
  turn timeouts, close-during-connect, close-during-turn, superseded turns, malformed frames and
  `close()`; the Speaker's session-owned generations (`cut(g)` adopts, never advances; lower is
  dropped; `cut(0)` lets the next call's greeting play) and the female-only voice choice; the mic
  gate (noise floor, sustained-loudness hold, discard of clicks, the 2.5× speaking gate, the 15 s
  runaway cut); and the gesture tracks — every persona gesture is represented, each returns to
  rest, the wobble alternates sides, and no track rolls past the cover limit.
- **Motion verified in a real browser** (`test/motion.mjs`, 15 checks): over ~158 frames the roll
  moved ~4°, the nod 3.0% of frame height, the sway 0.33% and breathing 0.004 of zoom; an exact
  inverse-transform corner test never let the frame edge show (minimum ~3 px of margin); a fake
  voice opened the mouth patch from 6.2 px / opacity 0 to 36.4 px / opacity 0.90, proving the channel
  reaches the rendered pseudo-element; the pose order `listening/tilt_head → head_wobble →
  thinking/look_up → speaking/nod → listening/freeze` reached the renderer; `cut()` dropped the nod
  and held the listening tilt; no page errors.
- **WS path verified end to end** against `human_bot`: `health → session → chat` over the shim
  returned `meta, backchannel, sources, sentence ×N, final`, two consecutive turns, and a clean
  disconnect. The browser smoke over `?ws=` reached `aw:ready` and the same barge-in turn.
  On that run Simli answered the relayed `/avatar/start` but its LiveKit join was refused by the
  provider's session limit, so `mode` was `"puppet"`; the transport relay itself returned a valid
  token in a direct check.

---

## 6. REA capture (second pass, 2026-11-01 evening)

The first pass used curl because the REA MCP server was not registered for Claude Code. The second
pass ran the same investigation through the **REA CLI** (`rea-agents` 6.5.0): one passive
`inspect_web_page`, one `analyze_web_bundle`, and two launch-mode `capture_browser_scenario` runs in a
fresh Chrome profile (one with no input, one with a click and a key press per page). The Evidence
records and the scenario inputs are in [evidence/rea/](evidence/rea/), with a per-record summary in
[evidence/rea/README.md](evidence/rea/README.md).

What REA added to the curl findings:

- **Why the first pass saw "no widget" so cleanly:** WP Rocket "Delay JavaScript" gates all 32 script
  tags behind the first user input. With no input REA recorded 230 requests and **zero scripts**;
  with one click it recorded 326 requests, 96 scripts, 26 hosts.
- **The full post-interaction inventory** is WordPress/Elementor/theme code plus a Google Tag Manager
  container (`GTM-PPGSPBRH`) that loads GA4, Google Ads, LinkedIn Insight, Microsoft Clarity,
  Leadfeeder, a Customers.ai consent stub and the Facebook SDK. Every XHR/fetch is an analytics beacon.
- **The on-page "avatar" is a YouTube embed** (`5PZxAv7lPyE`, `PS0oXjPGyTg`) through the YouTube
  iframe API. No WebSocket, worker, canvas, WebGL, chat launcher, or DaveAI-owned cookie/storage key
  appears in any record.

Conclusion unchanged: the public site carries no DaveAI conversational runtime to trace, so sections
1–4 above stand. The only observed widget-embedding mechanism on iamdave.ai is Elementor's YouTube
widget plus GTM-injected tags, which is not relevant to our design beyond confirming that an
interaction-gated loader (what WP Rocket does) is a sensible default for a heavy widget: our
`loader.js` already defers the iframe and all audio/WebRTC work until the launcher is clicked.

**Re-run (same inputs, later the same day).** Both scenarios were executed again from the stored
`scenario_*.input.json`; the records are in [evidence/rea/rerun/](evidence/rea/rerun/) with the full
comparison in [evidence/rea/README.md](evidence/rea/README.md#re-run-2026-11-01-second-execution-of-the-same-inputs).
The result reproduced exactly: 230 requests / 0 scripts / 1 host without input (final DOM
byte-identical, `207d40e7…`), and 326 requests / 96 scripts / 26 hosts with input. The only
differences were Microsoft Clarity's rotating collection edge (`n.` → `f.` → `a.clarity.ms`) and
timestamps inside embedded Google Ads URLs; no non-`www` DaveAI host, WebSocket or EventSource
appeared. The widget smoke test was re-run too (see §5).


---

## 7. Body motion learned from footage (2026-11-02)

The puppet renderer's gesture table is no longer hand-typed. `src/motion-data.js` is generated by
`tran_moment/motion/fit_gestures.py` from MediaPipe head-pose tracks of the reference clips and the
presenter segment (`tran_moment/motion/extract_motion.py`); `avatar.js` merges `LEARNED_GESTURES`
over its authored fallback and takes idle drift, blink and breathing parameters from `IDLE_STATS`,
each clamped to the frame budget. `gestureOrigin(name)` tells you which table a gesture came from.
Method, sources, quality gates and the consent note are in [tran_moment/motion/README.md](../tran_moment/motion/README.md);
per-gesture ranges in `tran_moment/motion/report.md`.

Checks: `node --test test/unit/avatar-motion.test.mjs` (data), `node test/motion.mjs` (headless
render: frame cover, mouth patch, pose order), `node test/smoke.mjs` (end to end).
