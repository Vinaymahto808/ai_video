# AIVA (aiva.saleassist.ai) — reverse-engineered feature map

Analysis date: 2026-10-29. Scope: publicly delivered front end + public documentation only.
No authenticated dashboard was accessed, no private API was called.

Evidence sources
- `https://aiva.saleassist.ai/` (Next.js marketing app, 326 KB HTML)
- `https://static.saleassist.ai/widgets/widget.js` (1.0 MB webpack bundle, saved as `widget.js`)
- `https://docs.saleassist.ai/llms.txt` + 100 page sitemap (GitBook; pages available as `.md`)
- `https://docs.saleassist.ai/saleassist-aiva-widget-integration-guide.md`
- `https://docs.saleassist.ai/policies/aiva-meeting-intelligence-lead-qualification-and-audit-rules.md`

Legend: **[O]** observed in delivered code/copy · **[D]** documented · **[I]** inferred from evidence · **[?]** unknown

---

## 1. What the product is

**[O]** AIVA = "AI Video Agent that sells, qualifies, consults and nurtures". A talking-head AI
salesperson that joins a live video call on a website (or WhatsApp / social / kiosk) and holds a
sales conversation with a visitor, then hands off to a human when needed.

**[O]** Marketing claims: 500 ms–1.2 s response latency, 50+ languages, 8+ personas, 20 %+ conversion
uplift, <8 s first response, 24+ integrations, 1M+ conversations.

**[D]** Positioning is a "Video AI Sales OS" with 3 modules: **Reels** (top funnel), **VidSell**
(mid funnel consultations), **Strike** (bottom funnel nudges; Strike X de-anonymises traffic).

---

## 2. Runtime architecture (what actually ships to the browser)

| Piece | Evidence | Meaning |
|---|---|---|
| Next.js 14 app router | `/_next/static/chunks/app/page-*.js`, RSC payload | marketing site only |
| `widget.js` (webpack, 1 MB) | bundle header + license file | the entire customer-facing call widget |
| **Agora RTC 4.24.2** | `https://download.agora.io/sdk/release/AgoraRTC_N-4.24.2.js`; `window.AgoraRTC.isCompatible()`, `AgoraRTC.VERSION` in telemetry | real-time audio/video transport (WebRTC SFU) |
| **socket.io** | `https://cdn.socket.io/4.7.2/socket.io.min.js`, client at `https://sockets.saleassist.ai` | signalling: incoming call, accept/reject, meeting URL, agent presence |
| `/meeting_tokens`, `/meetings`, `/missed_calls` | string table in bundle | call-token minting + meeting records |
| `/set_AI_sessionContext`, `/video-workflows`, `/lead-interactions/*` | string table in bundle | AI context injection, per-widget AI config, lead timeline |
| `client-static.saleassist.ai/<org-uuid>/…` | preloads | per-tenant asset CDN (persona stills, logos, welcome video) |
| `static.saleassist.ai/widgets/widget.js` | preload | versioned widget distribution |

**[O]** AI capability flags shipped to the widget (exact identifiers):
`is_enable_ai_audio_call_agent_all`, `is_enable_ai_audio_call_agent_offline`,
`is_enable_ai_video_call_agent_all`, `is_enable_ai_video_call_agent_offline`,
`is_enable_ai_video_call_agent_with_real_agent`.

→ There are three AI modes: **AI audio agent**, **AI video agent**, and **AI video agent operating
alongside a real agent** (the "human in the loop" product).

**[O]** Telemetry emitted per call (bundle): `callDurationMs`, `endReason`, `hadAudio`, `hadVideo`,
`agora: {sdkVersion, isCompatible, error}`, `collectedAt`.

### Call flow (reconstructed)

1. Page loads `widget.js`; site calls `saleassist.mountWidget({id, form_factor, sessionContext})`.
2. Widget opens a socket.io connection to `sockets.saleassist.ai`, subscribes to widget config.
3. Config returns channel mix (AI chat / AI audio / AI video / human), persona, and the five
   `is_enable_*` flags → widget shows launcher, welcome video, or an auto-popup trigger.
4. Visitor accepts → `user_accept` emitted → server mints meeting (`/meeting_tokens`) → `meeting_url`
   received → Agora channel join → media flows.
5. Agent rejection path: `agent_reject` → widget tears down the popup. Human handover uses the same
   channel, so a human can join the same room as AIVA **[I, from `is_enable_ai_video_call_agent_with_real_agent`]**.
6. On end: telemetry POST, `missed_calls` / `lead-interactions` updated, transcript queued.
7. Post-call: transcription → summarisation → sentiment → lead grade → self-audit (section 5).

**Not observable:** which ASR/TTS/LLM vendor powers the voice (no vendor strings in the bundle); the
avatar rendering method (pre-rendered clip library vs. neural talking head); lip-sync engine. Treat
these as design choices, do not copy.

---

## 3. Integration contract (documented, and worth matching exactly)

**[D]** Two script tags + one call:

```html
<script src="https://static.saleassist.ai/widgets/widget.js"></script>
<script>
  saleassist.mountWidget({
    id: "YOUR_WIDGET_ID",
    form_factor: "button",           // omit for floating launcher
    sessionContext: [{
      user:  { name: "Priya Sharma", logged_in: true, loyalty_tier: "gold" },
      cart:  { items: 2, total: 4499, currency: "INR" },
      page:  { product: "Bosch Serie 6 Washing Machine", price: 42990, in_stock: true }
    }]
  });
</script>
```

Rules the docs state explicitly: array of plain JSON objects (merged server-side), no secrets, read
once at mount (re-mount to refresh), descriptive keys because **the keys are part of the prompt**.

This is the single most important design idea in the product: *the website's own state is the AI's
opening knowledge*. It removes the "what are you looking at?" discovery tax.

---

## 4. AI layer

**[O]** Base prompt is configured per widget in the dashboard; `sessionContext` is layered on top.
**[D]** Multilingual, "trained natively on your own content" (marketing; the mechanism is a config +
prompt, not per-customer model training).
**[O]** Personas: Advisor, Relationship Manager, Sales Lead, Consultant, Concierge, Support (+custom).
**[O]** Industry tone packs: Luxury Retail, Electronics, Insurance, Real Estate, Automotive, Banking,
Healthcare, Travel.

---

## 5. Post-call intelligence — the actual prompt contract

This is published verbatim in their policy docs, so it is the clearest view of the "AIVA Intelligence"
loop. It is a **JSON contract** consumed by the dashboard:

```json
{
  "summary": "most important points first, max 2 sentences",
  "sentiment": "positive|neutral|negative",
  "sentiment_score": 0.0,
  "keywords": ["max 10"],
  "sentiment_segments": [
    {"text": "max 10 words", "start_word": 0, "end_word": 5,
     "sentiment": "positive|neutral|negative", "sentiment_score": 0.0}
  ],
  "questions": ["max 10, most important first"],
  "action_items": ["max 10"],
  "lead_status": "HOT|WARM|COLD",
  "miscellaneous": [
    {"question": "purchase_timeline", "answer": "9-12 months", "weight": 20, "score": 0}
  ],
  "unanswered_questions": ["ordered by importance"],
  "audit_details": "audit report per client_audit_ai_agent_rule"
}
```

Lead grading rules: **HOT** = any real buying intent (price talk, hold request, repeated product
questions) — payment/documents NOT required; **WARM** = interested but exploratory; **COLD** =
no intent / fragmented. Tie-break rule: *when in doubt, choose the hotter category*.

Audit dimensions (scored, feeds back into the next call): response quality, customer engagement,
lead handling, resolution effectiveness, compliance. Output `OVERALL_SCORE /5`,
`CUSTOMER_EXPERIENCE_SCORE /10`, issues, highlights, improvements, action steps.

**[I]** "Audits & self-improves" = the audit text is appended to the tenant's prompt/knowledge for
subsequent calls. That is a retrieval/prompt loop, not model retraining — cheap and correct.

---

## 6. Full feature inventory

| # | Feature | Status | Notes |
|---|---|---|---|
| F1 | Web widget, floating launcher or button form factor | O/D | `mountWidget` |
| F2 | AI video agent (talking head) | O | `is_enable_ai_video_call_agent_*` |
| F3 | AI audio-only agent | O | `is_enable_ai_audio_call_agent_*` |
| F4 | Hybrid AI + real agent in one call | O | `..._with_real_agent` |
| F5 | Session context injection from host site | D | keys become prompt |
| F6 | Auto-popup / intent trigger, popup accept-reject | O | `auto_popup_shown`, `auto_trigger_accept/reject` |
| F7 | Welcome video on launcher | O | `source_welcome_video_url` |
| F8 | Persona library (8+), per-industry tone | O/D | |
| F9 | 50+ languages incl. Indic + Hinglish | O | marketing claim |
| F10 | Omnichannel: web, social DM, WhatsApp, kiosk, QR, email/SMS, expo | O/D | |
| F11 | Lead capture + CRM routing + webhooks + API | D | `/lead-interactions` |
| F12 | Human handover with live agent presence | O/D | "AIVA first" / "human first" |
| F13 | Transcription + summary per call | D | addon |
| F14 | Sentiment scoring + HOT/WARM/COLD grading | D | weighted qualification rubric |
| F15 | Conversation audit / self-improvement loop | D | 5 dimensions, /5 and /10 |
| F16 | Analytics: widget insights, video tiles insights, reporting | D | |
| F17 | Strike / Strike X: intent nudges, anonymous-traffic ID | O/D | "5 % hit ratio" claim |
| F18 | Shoppable video tiles, video FAQ, video QR, video email | D | |
| F19 | Live stream selling (RTMP to social) | D | |
| F20 | Co-browsing SDKs (Flutter, RN, iOS, Android) + 6-digit pairing | D | separate product line |
| F21 | Scheduled + instant video meetings, Calendly-style booking | O/D | `/schedule_meeting_v3` |
| F22 | Mobile apps for agents (call management) | D | |
| F23 | Shopify app | O | app store listing |
| F24 | Roles/permissions, SMTP, custom domain, notifications, leave mgmt | D | |

**Claims worth beating, not copying:** latency 500 ms–1.2 s, <8 s first response, 10× conversion
vs chat/voice, 180 ms CRM sync, 24–48 h go-live.

---

## 7. Replication blueprint

Minimum viable equivalent, in build order:

1. **Transport** — WebRTC SFU (Agora/LiveKit/Daily) + WebSocket signalling. You cannot do this well
   over plain HTTP.
2. **Brain** — LLM with a strict style contract + `sessionContext` injection + tool calls
   (price, stock, EMI, slot booking, save lead). Streaming tokens, not full replies.
3. **Voice** — streaming ASR (Deepgram/Whisper.cpp), sentence-level TTS, incremental audio chunks
   so first audio starts < 700 ms.
4. **Face** — talking head driven by the TTS audio (MuseTalk / LiveTalking; both already scaffolded
   in this workspace under `tran_moment/`), idle micro-motion + blink when silent, gesture clips
   keyed to emotion tags.
5. **Turn-taking** — VAD + barge-in (stop TTS mid-word), backchannels while the user speaks.
6. **Post-call** — the JSON contract in section 5, plus the audit loop.
7. **Ops** — persona config UI, per-widget flags, lead inbox, analytics.

Differentiator to exploit: AIVA's faces are pre-built personas. Your edge is a *licensed, consented*
real person (the workspace already has `voice_clone/consent/` and a consented narrator) — a real
advisor's face + cloned-consent voice reads as human in a way a synthetic persona does not.

**Do not** copy their avatar art, voice samples, prompt text verbatim, or scrape their tenants' data.
Rebuild behaviour; ship your own assets.

---

## 8. Unknowns (state them, don't fake them)

- LLM/ASR/TTS vendors behind the call — not in the bundle.
- Whether the face is real-time neural rendering or a pre-rendered viseme/clip library.
- Actual measured latency (marketing claim only).
- Their `sessionContext` serialisation and prompt template.
- Pricing, seat limits, and enterprise contract terms.
