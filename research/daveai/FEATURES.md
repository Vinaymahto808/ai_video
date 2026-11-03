# DaveAI (www.iamdave.ai) — reverse-engineered feature map

Analysis date: 2026-11-03 (runtime evidence 2026-11-01 and 2026-11-03). Scope: the public marketing
site only. No account, no demo booking, no authenticated surface, no customer deployment was touched.
Companion to `../aiva/FEATURES.md`; same legend, same CSV layout (`features.csv`).

Evidence sources
- `https://www.iamdave.ai/` (home), `/platform/`, `/solutions/gryd/`, `/solutions/virtual-sales-avatar/`,
  `/solutions/lead-sales-acceleration/`, `/solutions/service-ownership-experience/`,
  `/solutions/dealer-productivity-agent-assist/` — page copy, fetched 2026-11-03.
- REA CLI 6.5.0 browser scenarios (launch mode, fresh Chromium profile, one click + one key per page):
  `avatar_widget/evidence/rea/` — `ev_95d45636…` (2026-11-01), `ev_72d83716…` (re-run the same evening),
  `ev_c5b1c995…` (`recheck_2026-11-03/with_interaction.json`, today). No-input runs `ev_ff5f25ab…`,
  `ev_7b801f4b…`; passive page inspection `ev_80f95376…`; bundle analysis `ev_43ee71a1…`.

Legend: **[O]** observed in delivered code/copy · **[D]** documented · **[I]** inferred from evidence · **[?]** unknown

DaveAI publishes no developer documentation, so nothing below is **[D]**. Everything about *how*
the product works is **[I]** or **[?]**; everything about *what* it claims to do is **[O]** from copy.

---

## 1. What the product is

**[O]** DaveAI (Sociograph Solutions, Bengaluru) sells an "agentic AI" layer for automotive retail:
a platform (**GRYD**) plus an intelligence layer (**Affinity Engine**), domain models (**Mobility SLM**),
and a dealership product (**AutoNgage**, autongage.com). Customer-facing pieces are a **virtual sales
avatar**, **GenAI chatbots** (web and WhatsApp), **kiosk / in-store journeys**, and **3D / metaverse
showrooms**. Staff-facing pieces are an **advisor copilot** and **manager visibility**.

**[O]** Marketing claims (no methodology, no time frame): 90 % increase in customer engagement, 36 %
improvement in lead qualification rate, 20 % increase in lead conversion, 56,846,000+ users, 75+
projects; GRYD: "70 % lower costs than off-the-shelf vector services and LLMs", "40 % faster response
than cognitive search", "SLMs handle up to 75 % of queries", go-live "in as little as a few hours".

**[O]** Named deployments: Maruti Suzuki (WhatsApp bot, 10K+ test-drive bookings; virtual avatar,
+33 % lead generation; "Nexaverse" metaverse built in 14 days), BYD (WhatsApp, 60K+ interactions),
Hero MotoCorp (hyper-realistic avatar), Axis Bank, Karnataka Bank (DhIRA, speech + text), Merino
Laminates (product visualizer), Wegofin, Wunderman Thompson.

**[O]** Four-stage process on the home page: **Signal → Understand → Decide → Act**.

---

## 2. Runtime architecture (what actually ships to the browser)

**[O] Nothing of the product ships on the public site.** Three captures, two days apart, agree:

| Capture | Requests | Hosts | Scripts | WebSocket / worker | `*.iamdave.ai` other than `www` |
|---|---|---|---|---|---|
| `ev_95d45636…` 2026-11-01 | 326 | 26 | 96 | 0 | none |
| `ev_72d83716…` 2026-11-01 re-run | 326 | 26 | 96 | 0 | none |
| `ev_c5b1c995…` 2026-11-03 | 331 | 25 | 98 | 0 | none |

The only differences between 11-01 and 11-03 are vendor rotations: Microsoft Clarity 0.8.72 → 0.8.75
and a 0.8.77-beta, a new YouTube embed player hash, a new Google `js/th` challenge script, and one
fewer LinkedIn pixel host. First-party scripts are identical (36 WordPress / Elementor Pro / Vamtam /
jQuery files; `sdk.js` is the Facebook SDK). The on-page "avatar" is a YouTube iframe. No chat
launcher, no canvas, no `<video>` from DaveAI, no DaveAI cookie or storage key. WP Rocket delays every
script until first input, so a no-input capture shows zero JavaScript (`ev_ff5f25ab…`, `ev_7b801f4b…`).

**[O]** `docs.`, `api.`, `apps.`, `cdn.`, `gryd.iamdave.ai` do not resolve; no npm package or GitHub
repo; archived `api.iamdave.ai` (2019) was a login-only portal titled "Dave.Ai – Intelligent Sales
Companion" (details in `avatar_widget/README.md`).

**[I]** Delivery model: tenant-scoped, vendor-managed. DaveAI (or the OEM's agency) embeds a per-tenant
bundle on the customer's own pages, apps, kiosks and WhatsApp numbers; nothing is self-serve. This is
consistent with the three-step onboarding on every solutions page ("Map … / Configure … / Integrate &
Launch, with DaveAI's team") and with "a user-friendly API" being the only integration statement.

**[?]** Transport, authentication, message schema, avatar rendering (3D, pre-rendered, or streamed
talking head), ASR / TTS / LLM vendors, SLM architecture. Not recoverable from public artifacts.
Treat all of these as design choices, not things to copy.

---

## 3. Product surface, decomposed

### 3a. Platform (GRYD) **[O, copy]**

| Module | Claim |
|---|---|
| GRYD Conversation | GenAI chatbots; "humanlike interactions tuned to enterprise and industry context" |
| GRYD Affinity | personalisation from preferences, behaviour, contextual signals ("Multi-Dimensional Affinity Engine") |
| GRYD Studio / Visualization | 3D visualisation, immersive avatars, interactive 3D product videos |
| GRYD Synthesis | avatar videos and real-time avatar conversation |
| GRYD Image AI | product images |
| Generative AI Hub | small models + large models, "infrastructure of their choice"; Model Zoo (NLP, image, analytics); RLHF loop |
| Secure Governance Layer | product and branch fencing, data policies, user controls, prompt-engineering frameworks |
| Deployment | cloud, VPC, on-premise, edge |
| Edge | "LSVASR" (undefined), facial detection, affinity engine at the edge |

### 3b. Affinity Engine **[O, copy]**
Intent → Context → Affinity → Propensity → Next Best Action. Anonymous intent detection on the web,
dynamic personalisation, lead scoring, lead recovery, orchestration across web, WhatsApp and voice.

### 3c. Virtual sales avatar **[O, copy]**
"Multilingual", "GPT-powered conversations", "self-learning AI", "expert product recommendations",
"seamless integration", "privacy and security", "patent-pending Empathetic AI". Roles: product
narrator, brand expert, celebrity twin, virtual influencer / tutor. Devices: website, mobile app,
AR/VR, kiosk. Platforms: WhatsApp, Google, Meta, Telegram. Onboarding: define → train & personalise
(product and customer knowledge, brand tone) → integrate & launch.

### 3d. Journeys by solution page **[O, copy]**

| Journey | What the copy promises |
|---|---|
| Lead & sales acceleration | guided lead capture instead of forms (budget, use case, model); variant comparison and finance options; WhatsApp flows that share brochures, answer FAQs, capture test-drive interest, schedule appointments; showroom QR / kiosk journeys; advisors receive, prioritise and act on leads with AI next-step suggestions; tracking of leads captured → qualified → handed off |
| Service & ownership | service reminders over web / app / WhatsApp / email; slot suggestion, outlet confirmation, requests logged into existing systems; service status, ETA, invoice estimate, pick-up details; warranty / insurance / claims guidance (coverage, documents, next steps); renewals, upgrades, loyalty offers with segmentation and warm hand-off to sales; analytics on engagement, bookings completed, queries resolved, repeat visits |
| Dealer productivity & agent assist | advisor copilot: nearest / preferred outlet, available slots, test-drive / visit / service booking; natural-language lookup of specs, feature differences, pricing rules, eligibility, policies, SOPs; suggested responses during chats or calls; kiosks for walk-ins; conversation and usage data for coaching; embeds into CRM, DMS, websites, kiosks, portals, contact-centre tools |
| Dealer operations (AutoNgage) | lead journey visibility, follow-up governance, performance intelligence, management alerts |

---

## 4. Full feature inventory, with our status

"Ours" = this repository (`human_bot` + `kb` + `avatar_widget`), status on 2026-11-03.
yes = built and tested · partial = exists in a narrower form · no = absent · excluded = decided against.

| # | DaveAI feature | Status | Ours | Where / what is missing |
|---|---|---|---|---|
| F1 | Website avatar widget | O | yes | `avatar_widget/loader.js`, one tag, shadow root + iframe |
| F2 | Mobile app SDK | O | no | the iframe panel runs in a WebView; no native SDK |
| F3 | Kiosk / in-store screens and tablets | O | partial | runs in any browser; no kiosk mode (fullscreen, attract loop, idle reset, no-mic fallback to tap) |
| F4 | AR/VR, 3D product visualisation, metaverse showroom | O | excluded | out of scope for a service advisor |
| F5 | WhatsApp bot (test-drive capture, brochures, FAQs, reminders) | O | no | Wati template planned only for insurance-claim intent; the `/chat` brain is channel-agnostic |
| F6 | Google / Meta / Telegram messaging channels | O | no | |
| F7 | Speech and text in one agent (DhIRA) | O | yes | voice-only widget by decision; `human_bot/web/embed.html` card has text + voice |
| F8 | Embedding into contact-centre tools, advisor consoles, dealer portals | O | no | |
| F9 | GPT-powered streaming conversation | O | yes | `llm/client.py`: Groq → OpenAI → Gemini → offline brain, sentence-level SSE |
| F10 | Multilingual | O | partial | Hinglish / Hindi / English with mirror policy; no other Indian languages; female voice only |
| F11 | Hybrid SLM + LLM ("75 % of queries on SLMs", lower cost) | O | partial | local e5-small embeddings (CPU) + FTS5 do the cheap part; answers always go to a hosted LLM; offline rule brain as last resort |
| F12 | Self-learning, knowledge base updates itself | O | partial | `postcall/analyze.py` records `unanswered_questions`; no loop that turns them into KB entries |
| F13 | Knowledge from catalogue, FAQs, SOPs, pricing rules, location data | O | partial | website crawl + persona catalogue; no PDF / SOP ingestion, no pricing-rules engine |
| F14 | Product recommendation, variant comparison, finance options | O | partial | `recommend_max_models: 2` for showroom personas; no EMI / finance tool; strict-KB rule forbids invented numbers |
| F15 | Objection handling with consistent talk tracks | O | yes | persona `objection_style`, `persona/humanizer.py` |
| F16 | "Empathetic AI" (emotion-aware delivery) | O | yes | emotion tags per sentence → TTS prosody, gestures, backchannels |
| F17 | Avatar roles (narrator, brand expert, celebrity twin, tutor) | O | partial | one persona per brand (Neha / Riya); celebrity twin excluded (consented voices only) |
| F18 | Real-time avatar conversation | O | yes | Simli live (WebRTC) or 2D puppet with learned gestures |
| F19 | Pre-rendered avatar videos (GRYD Synthesis) | O | no | `tran_moment/` can produce clips offline; not wired as a feature |
| F20 | Anonymous intent detection, dynamic personalisation, next best action | O | partial | page context (`aw:ctx`), visitor id, returning-customer recall from memory; no propensity model |
| F21 | Lead scoring / qualification | O | yes | HOT / WARM / COLD with weighted qualification, AIVA-compatible JSON |
| F22 | Lead recovery (re-engaging dropped leads) | O | no | needs an outbound channel (WhatsApp / call) and a scheduler |
| F23 | Guided lead capture replacing forms | O | yes | `discovery_order`, one question per turn, facts only from the customer's words |
| F24 | Nearest dealer / preferred outlet routing | O | no | persona has one `location` string; no outlet list, no distance logic |
| F25 | Appointment booking with live slots (test drive, visit, service) | O | no | `BookingProvider` interface designed (ARCHITECTURE §6); Neha offers a call-back, never a confirmed slot |
| F26 | Proactive service reminders and notifications | O | no | |
| F27 | Service status, ETA, invoice estimate self-service | O | no | needs DMS access |
| F28 | Warranty / insurance / claims guidance | O | partial | KB answers from autocarrepair.in claim-repair pages; claim intent → hand-off line + company call |
| F29 | Renewals, upgrades, loyalty offers with segmentation | O | no | |
| F30 | Advisor copilot (knowledge lookup, suggested replies) | O | no | `kb` search API is reusable as-is; no staff UI |
| F31 | Lead journey visibility, follow-up governance, management alerts | O | partial | `/conversations` feed, Bitrix24 lead with transcript; no alerts, no SLA tracking |
| F32 | Performance insights and coaching | O | partial | `human_bot/eval/` human-likeness scorer, audit score per call; no dashboard |
| F33 | Analytics: engagement, bookings, resolved queries, repeat visits | O | partial | timings and lead status are recorded; nothing aggregated |
| F34 | REST API for integration | O | yes | API contract in ARCHITECTURE §5 |
| F35 | CRM / DMS integration, logs into existing systems | O | partial | Bitrix24 sink via outbox (idempotent, retried); no DMS |
| F36 | Cloud / VPC / on-prem / edge deployment | O | partial | single host, Caddy + systemd, SQLite; on-prem is feasible (no managed DB); hosted LLM / TTS / Simli remain external |
| F37 | Governance: product and branch fencing, data policies, prompt frameworks | O | partial | strict-KB rule, persona guardrails, origin allow-list, rate limits; no multi-tenant admin |
| F38 | Security: SSL, MFA, vulnerability testing, PII handling | O | partial | HTTPS, admin key, body / path limits, keys server-side; no PII redaction in stored transcripts, no MFA |
| F39 | Edge facial detection | O | excluded | consent and privacy decision |
| F40 | Go-live in hours | O | yes | one script tag + one persona JSON + a crawl |

---

## 5. What is worth matching, and what is not

**Match (sales value, cheap to build on what exists):**
1. **Booking with real slots and outlet routing (F24, F25).** Every DaveAI journey ends in a booked
   slot; ours ends in "call-back". Build the `BookingProvider` against Bitrix24 calendar or a Google
   Sheet until a workshop system exposes availability, plus an outlet table per persona.
2. **WhatsApp channel (F5, F26, F22).** The `/chat` brain already takes text and returns sentences;
   a Wati or Meta Cloud API adapter turns it into the Maruti / BYD-style bot and gives reminders and
   lead recovery an outbound path.
3. **Advisor copilot (F30).** A staff page over `kb` search and the conversation feed, with suggested
   replies from the same prompt. No new AI needed.
4. **Self-learning loop (F12).** Weekly: `unanswered_questions` → gap list → human writes the answer →
   indexed as a KB chunk. That is what "self-learning" means in practice.
5. **Manager alerts and a small dashboard (F31–F33).** Aggregate `/conversations` and outbox state;
   alert on HOT leads not pushed, call-backs not made, outbox failures.
6. **Kiosk mode (F3).** Fullscreen, attract loop, tap-to-talk only, auto-reset after idle.

**Do not match:** 3D / metaverse (F4), celebrity twins (F17), facial detection at the edge (F39),
text-only chat in the widget (voice-only decision), "SLM" marketing (F11): our local-embedding +
hosted-LLM split already gives the cost profile the claim is about.

**Claims to beat with measurement, not copy:** 36 % qualification lift, 20 % conversion lift, "go-live
in hours". We can publish our own numbers from `/conversations` (lead grade distribution, hand-off rate,
first-word latency 2.8 s today, 1.4 s planned).

---

## 6. Evidence ledger

| Question | Answer | Evidence | Type |
|---|---|---|---|
| Does the public site ship any DaveAI runtime? | No. WordPress + Elementor + GTM tags only; avatar is a YouTube embed | `ev_95d45636…`, `ev_72d83716…`, `ev_c5b1c995…`, `ev_ff5f25ab…`, `ev_7b801f4b…` | observed |
| Did the site change between 2026-11-01 and 2026-11-03? | Only vendor script versions (Clarity, YouTube player, Google challenge); first-party script set identical | `ev_95d45636…` vs `ev_c5b1c995…` | observed |
| Is there a public SDK, API or docs host? | No (`docs.`/`api.`/`cdn.`/`apps.`/`gryd.` unresolved; no npm / GitHub) | `avatar_widget/README.md` §2a | observed |
| Do named customers' sites carry a DaveAI script? | Maruti, Nexa, Hero: no; BYD is a WhatsApp number | `avatar_widget/README.md` §1 | observed (2026-11-01) |
| Feature set | Sections 3–4 | page copy fetched 2026-11-03 | observed (copy) |
| How any feature is implemented | unknown | — | unknown |

Bounded negative searches: public web only; no demo request, no customer login, no app-store
binaries (an AutoNgage or dealer app APK, if one exists, was not looked for and would be the next
place a runtime could be observed).
