# human_bot — a video sales agent that sounds like a person

Two things live here:

1. **`../research/aiva/FEATURES.md`** — what AIVA (aiva.saleassist.ai) actually is, rebuilt
   from its public bundle, docs and policy text. Feature map, call flow, and the exact
   post-call JSON contract it uses.
2. **this folder** — a working implementation of the same loop, with the human-likeness
   work made explicit and measurable instead of "add a friendly tone".

```
human_bot/
  persona/   persona JSON, prompt builder, speech humanizer
  llm/       provider-agnostic streaming client + offline fallback brain
  server/    stdlib HTTP + SSE call loop (barge-in, sentence TTS, emotion tags)
  postcall/  AIVA-compatible call analysis (lead grade, sentiment, audit)
  train/     dataset builder + QLoRA SFT script (Colab)
  eval/      human-likeness scorer (heuristic + optional LLM judge)
  web/       browser demo: mic -> SSE -> TTS -> avatar panel
```

## Put it on a dealer site

`web/widget.js` is a drop-in launcher: one script tag adds a floating "Chat with Riya"
button, page context, teaser and a responsive panel. See **`web/EMBED.md`** for the snippet,
the JS API, the HTTPS/hosting requirement and the CSP notes.

```html
<script src="https://BOT-HOST/widget.js" data-persona="hans-hyundai"
        data-accent="#0b2f6b" defer></script>
```

Screenshots of it running on the real `hanshyundai.com/hyundai-creta` page are in
`research/previews/`.

## Run it

One process serves one brand by default, but every `persona/persona_*.json` is available at
runtime (`POST /session {"persona": "hans-hyundai"}`), and the demo page has a brand picker.

```powershell
python human_bot\server\app.py --port 4001 --persona galaxy-toyota   # Neha · Galaxy Toyota
python human_bot\server\app.py --port 4002 --persona hans-hyundai    # Riya  · Hans Hyundai
# or: human_bot\run.ps1 -Port 4002 -Persona hans-hyundai
```

Open http://127.0.0.1:4001/ or http://127.0.0.1:4002/ — the header dropdown switches brand
without restarting.

No dependencies. With no API key it runs `OfflineBrain` (a deterministic, brand-aware sales
state machine) so the full loop — greeting, streaming, backchannels, barge-in, post-call
report — works end to end. Add a key and it uses the real model:

```powershell
$env:OPENAI_API_KEY="sk-..."        # or GROQ_API_KEY / GEMINI_API_KEY
$env:HUMAN_BOT_MODEL="gpt-4o-mini"
python human_bot\server\app.py --port 8010
```

Keys are auto-read from `voice_clone/.env` if present. Live instances run on 4001
(Galaxy Toyota) and 4002 (Hans Hyundai).

Persona files carry the verified price list (`offline.models`). It is injected into the
system prompt so the model quotes those numbers verbatim instead of estimating — replace the
values with the dealer's live price list before any customer-facing use.

```powershell
# headless smoke test
curl -s -XPOST localhost:8010/session -d '{}' -H "Content-Type: application/json"
curl -s -N -XPOST localhost:8010/chat -H "Content-Type: application/json" `
  -d '{"session_id":"<id>","text":"Budget 12 lakh, family of 5"}'
curl -s -XPOST localhost:8010/analyze -H "Content-Type: application/json" -d '{"session_id":"<id>"}'
```

## Website knowledge (RAG)

`../kb/` crawls a brand's website and serves `GET /api/knowledge/search`. A persona opts in with a
`knowledge` block (`persona_auto_car_repair.json` -> `website_id: auto_car_repair`). On every `/chat`
turn the server retrieves the top passages, emits an SSE `sources` event, and appends a "Verified
website information" block to the system prompt with the strict rule: facts about services, prices,
locations, offers only from those passages, otherwise "cannot verify" + call-back. Service down =>
the bot still answers, without claiming facts. Env: `KB_URL` (default `http://127.0.0.1:4100`),
`KB_TIMEOUT_S` (2.5).

```powershell
cd kb; npm start                                                   # knowledge API on :4100
python human_bot\server\app.py --port 4003 --persona auto-car-repair   # Neha · Auto Car Repair
```

## Why it sounds human (and where each piece is enforced)

| # | Behaviour | Enforced in | Checkable signal |
|---|---|---|---|
| 1 | First turn ≤ 14 words, later ≤ 40 | prompt + `enforce_budget` | `pct_over_budget` |
| 2 | Exactly one question per turn | prompt + humanizer demotes extras | `pct_stacked_questions` |
| 3 | Acknowledge before answering | prompt + `ACK` rubric | `ack_rate` |
| 4 | Contractions, no markdown/lists | humanizer strips markup | `pct_markdown` |
| 5 | Numbers spoken ("16.46 lakh rupees") | `spoken_numbers` | `pct_written_money` |
| 6 | Agent talks less than the customer | prompt + talk-ratio score | `talk_ratio_agent` |
| 7 | Pauses masked, never dead air | prompt rule 6 | transcript |
| 8 | Tailors examples to the customer type (new / existing / claim / fleet / breakdown; first-car / exchange / family / corporate / ready) | persona `conversation.visitor_types` -> "Who is on the call" block | transcript |
| 9 | Follows a 6-step call flow, captures name / area / phone (phone only for a call-back, pickup or test drive) / time, reads them back once before booking | persona `sales.flow`, `sales.capture_fields`, `sales.confirm_line` | `lead` fields in `/conversations` |
| 10 | Never invents prices, offers, dates, capabilities; out-of-scope and competitor lines; fallback line when the team is unavailable | persona `guardrails.never_invent`, `out_of_scope_line`, `competitors`, `handoff_unavailable_line` | transcript |
| 11 | Longer answer (up to `max_words_detail`, 70) only when the customer asks for detail | `humanizer.wants_detail()` + prompt rule 1 | `pct_over_budget` |
| 8 | Backchannels while user speaks | `pick_backchannel` → SSE | `backchannels` counter |
| 9 | Stops instantly on interruption | generation counter in `_chat` | `interrupted` event |
| 10 | Face reacts (emotion → gesture) | `emotion_of` → SSE `gesture` | `emotion` tag |

The humanizer is the cheap 80 %. It runs in ~20 ms and fixes the tells that make LLMs sound
like brochures: written money, bullet lists, stacked questions, 90-word monologues, "Certainly!".

## What still makes it feel fake (fix in this order)

1. **Voice.** Browser TTS is the biggest tell. Point it at the cloned consented voice
   (`voice_clone/start_voice_service.ps1`) and stream audio chunks, not whole sentences.
2. **Latency.** Target < 700 ms to first audio. Budget: VAD 100 ms, ASR 150 ms (streaming),
   LLM first token 250 ms, TTS first chunk 200 ms. If LLM is slower, play "Ek second…".
3. **Face.** `tran_moment/` already has MuseTalk scaffolding. Idle motion + blink while silent
   matters more than perfect lip-sync; a frozen face reads as a photo.
4. **Turn-taking.** Real people overlap. Allowing 150-250 ms of overlap before you cut the
   customer is what makes it feel like a conversation instead of a walkie-talkie.
5. **Memory.** Repeating a question they already answered destroys the illusion faster than
   any accent problem. Keep the slot table, inject it every turn.

## Training it to talk like a real human — honestly

There is no "train on 14 examples" path to human speech. Here is the ladder, cheapest first:

**Step 0 — prompt + humanizer (works today, no GPU).**
Run `eval/humanness.py` before and after any change. If you are under 80, the model is not the
problem.

**Step 1 — collect real calls (the actual bottleneck).**
300-1000 recorded sales calls with consent, transcribed with speaker labels. This is the only
data that teaches rhythm, Hinglish code-mixing, objection phrasing, and how your best rep
actually closes. Rules: written consent per speaker (`voice_clone/consent/` already has the
template), 12-month retention, PII redaction before it ever reaches a trainer.

**Step 2 — build the set.**
```powershell
python human_bot\train\build_dataset.py --in training\conversations.jsonl --out training\sft
```
It dedupes, splits, and flags every assistant turn that breaks the style contract, so you fix
the data instead of training on slop.

**Step 3 — QLoRA SFT (Colab T4, ~35 min per 1k examples).**
`train/lora_sft.py` — Qwen2.5-7B-Instruct, 4-bit, rank 16. Then better: DPO on pairs from real
calls (human rep reply = chosen, model reply = rejected) using the same prompts.

**Step 4 — measure like a product, not a vibe.**
`eval/humanness.py --judge` gives the 0-100 score plus an LLM rater. Track it per release. Also
track business numbers: talk ratio, discovery completion, next-step rate, handover rate.

**Step 5 — close the loop like AIVA does.**
`postcall/analyze.py` emits their contract (summary, sentiment segments, HOT/WARM/COLD,
weighted qualification, audit). Feed the `audit_details.action_steps` back into the persona
prompt weekly. That is a prompt/RAG loop, not retraining — cheap, and it is what their
"self-improving" claim actually amounts to.

## Honest limits

- `training/conversations.jsonl` has 14 examples. Enough to tune a prompt and validate an eval;
  nowhere near enough to fine-tune. Fine-tuning on it will make the model worse.
- No GPU on this machine (`nvidia-smi` absent) — training runs on Colab/Kaggle or a rented A100.
- The browser demo uses the Web Speech API for STT and TTS. Chrome only, and the voice is
  generic. The server contract is already chunk-based, so swapping in Whisper + OpenVoice +
  MuseTalk is a transport change, not a rewrite.
- Hinglish ASR quality is the weakest link in the chain; budget real time for evaluating
  Whisper vs Deepgram vs Sarvam on your own call audio before choosing.

## Live talking-head avatar (D-ID stream)

The card can replace the 2D puppet with a neural, lip-synced video stream of the same Neha portrait
(AIVA-class motion). Server-side relay only — the key never reaches the browser.

1. Create an API key at D-ID (studio → API keys). Add to `voice_clone/.env`:
   ```
   DID_API_KEY=<key>
   HUMAN_BOT_AVATAR_PROVIDER=did
   ```
2. Restart `python human_bot/server/app.py …`. `GET /health → avatar.ready: true`.
3. Open the card: with no `?clips=` it switches to the stream automatically; force with `?clips=did`
   (or `data-clips="did"` on the widget tag). Sentences go to `POST /avatar/say` and D-ID speaks them
   with `hi-IN-SwaraNeural` / `en-IN-NeerjaNeural`; the stream video plays in the stage.
4. If the stream fails or the key is missing the card falls back to the puppet (`avatar.fallback`).

Flow: `/avatar/start` (upload portrait once, open stream, return SDP offer + ICE) → browser answer via
`/avatar/sdp` → `/avatar/ice` → `/avatar/say` per sentence → `/avatar/stop`. Billing is per streamed
minute on the D-ID account; the KB, persona and widget are unchanged.

## Live avatar, free tier: Simli (audio-driven)

Alternative to D-ID with a real free quota (50 min/month, template faces; a custom face from the Neha
portrait needs the $10 Hobby plan → set `SIMLI_FACE_ID`). Our TTS still speaks (Indian female voice);
Simli lip-syncs and renders the face in real time.

```
SIMLI_API_KEY=<key from app.simli.com>
HUMAN_BOT_AVATAR_PROVIDER=simli
SIMLI_FACE_ID=<template or custom face id>     # optional; default is Simli's demo face
```

Restart the bot. `/health → avatar.provider: simli, ready: true`. The card then: `POST /avatar/start`
(server mints a session token via `/compose/token`) → `simli-client` SDK (ESM from esm.sh) opens WebRTC into
the stage → each sentence: `/tts` → decode → PCM16 16 kHz → `sendAudioData` → Simli speaks + animates.
`POST /avatar/faces` lists faces visible to the key. Any failure falls back to the 2D puppet.

## Hearing the customer

`POST /stt` takes a raw audio body (webm/opus, mp4 or wav) and returns Roman-script Hinglish text from Groq Whisper (`whisper-large-v3-turbo`, 400-700 ms a sentence). The card records each utterance itself: getUserMedia, a loudness gate with adaptive floor, up to 4 s of pre-roll, cut after 0.8 s of silence, then the clip goes to `/stt` and the text to `/chat`. The browser recogniser is only the fallback (mic denied, no MediaRecorder, or `/stt` answering 503). `HUMAN_BOT_STT_MODEL` and `HUMAN_BOT_STT_PROMPT` override the model and the Hinglish bias prompt.

Opened from the widget the card starts at once (`?autostart=1`, the launcher click is the user gesture); the Start button only remains when /embed is opened directly.

Simli's legacy face streams a 512x512 head crop. Shown raw it zooms into a close-up that reads as a different face, so with the puppet loaded the card composites the stream into the portrait instead: the frame is drawn onto the puppet canvas with a feathered ellipse around head and neck (`LIVE_FIT` in web/embed.html, measured eyes-to-eyes against tran_moment/prepared/face.json). Only the face moves; framing, shirt and lapel mic stay the still's. Regenerate the face from the same portrait if you change it, then re-measure `LIVE_FIT`.


## Response latency (measured 2026-11-02, this laptop + home connection)

Where a turn's time went before the fix: STT 0.45 s, model (OpenAI gpt-4o-mini) 2.2 s with the whole
reply arriving at once, then Edge TTS 1.5–3 s **per sentence**, fetched one after another, with a
7.6 s cold start on the first clip. Customers heard nothing for 5–8 s and then gaps between sentences.

What changed:

| Stage | Before | Now | How |
|---|---|---|---|
| Model | ~2.2 s, OpenAI, 429-prone | ~0.3 s stream (0.9–1.2 s incl. KB retrieval) | Groq first (`HUMAN_BOT_LLM_ORDER`, default `groq,openai-compatible,gemini`) |
| Reaction | silent until first clip | "acha"/"haan ji" spoken at ~0.02 s | backchannels + stock lines warmed at start-up (`warm_tts`); both cards play the backchannel immediately |
| Sentence clips | serial: fetch → play → fetch | server synthesizes each sentence the moment it is emitted (`prefetch_tts`); cards download the next clip while the current plays | 2nd+ clips typically 0.01 s; only the first clip still waits on Edge (~1.5 s) |
| Cold start | 7.6 s first clip | 0 (warm-up at boot, ~16 s in the background) | `/tts` joins an in-flight synthesis instead of duplicating it (`tts_get_or_make`) |

Second pass (same day, "still takes time to respond"):

| Stage | Before | Now | How |
|---|---|---|---|
| First clip | waits for the whole Edge clip (1.5–4.6 s, one 12 s outlier) | audio starts at Edge's first chunk (~1–2 s), rest streams behind it | `/tts` streams chunks as a `TtsJob` receives them (no Content-Length, connection close); the cards play `<audio src=/tts…>` progressively instead of fetch→blob. `/tts?prefetch=1` only starts the job (202). |
| End-of-speech wait | 800 ms silence (card), 900 ms (widget) | 650 ms / 700 ms | mid-sentence Hinglish pauses measure 300–500 ms |
| Cached clip | — | 5 ms | unchanged cache, now served as one chunk |

Still fixed: knowledge retrieval 0.65–1.2 s (one Gemini embedding call per question; the kb service caches
repeat queries), Groq first token ~0.2 s, STT 0.45 s. A typical turn now: silence 0.65 s → STT 0.45 s →
"hmm/acha" → first sentence ~1.5 s → her first words ~1–2 s later. The next lever is a local query
embedding (the ACR corpus would need re-embedding with the same model) or Azure Speech with a kept-open
connection. Groq/PlayAI TTS is faster but has no Indian female voice, so it stays out of the chain.

## Conversation memory and recording (2026-11-02)

Decision reversed on 2026-11-02: conversations **are** recorded now (owner's goal: every call stored, leads
to the CRM, returning customers recognised). `memory/store.py`, SQLite at `data/conversations.sqlite`,
audio under `data/audio/<conversation id>/`.

- **Disclosure**: the greeting ends with "Yeh baat-cheet quality ke liye record hoti hai." and the card's
  footer says the same; `consent=1` is stored once that line was delivered (DPDP Act 2023).
- **What is stored per conversation**: visitor id, persona, page, timestamps, turns, phone / name / car /
  area / problem extracted from the customer's own words, intent, lead status (post-call analysis), agreed
  next step, one-line summary, the full transcript, the analysis, and the audio: the customer's utterances
  (`u000.webm` from `/stt?session_id=`), Neha's sentences (`a<turn>_<n>.mp3`), and `call.m4a`, the whole
  call merged in order (ffmpeg).
- **When**: after every turn (upsert, nothing lost on a closed tab), on `POST /end` (the red handset,
  `pagehide`, the widget's `end()`), and when the 30-minute idle reaper closes a session.
- **Returning customer**: the cards keep a random visitor id in `localStorage` (`hhbot_vid` / `aw_visitor`)
  and send it as `sessionContext.visitor`. `/session` loads that visitor's last three conversations into the
  system prompt ("Returning customer" block: facts + agreed next step + the last two things they said) and
  opens with "Namaste, wapas swagat hai! Pichli baar aapki Creta ki AC repair ki baat hui thi. Kya woh
  theek ho gayi?". Phone number recall (same person, new browser) is supported by the store (`recall(phone=)`)
  and is wired in as soon as the number is known in the current call.
- **Read it back**: `GET /conversations?visitor=&phone=&limit=` (list, newest first),
  `GET /conversations/<id>` (full record), `GET /conversations/<id>/audio/<file>`. Loopback only, or send
  `X-Admin-Key: $HUMAN_BOT_ADMIN_KEY`. This is the feed for the CRM push (Bitrix24 / HubSpot: next step).
- **Off switch**: `HUMAN_BOT_RECORD=0` disables storage, audio and the disclosure line.

## Voice (2026-11-02)

`HUMAN_BOT_VOICE_STYLE` picks how Edge reads a reply; sample clips of the same sentence are in `web/samples/`:

- `hindi` (default): Hindi words are transliterated to Devanagari (`llm/hindi_script.py`, dictionary of ~450
  everyday Hinglish words; English words, acronyms and names of places stay Latin) and **hi-IN Swara** reads it.
  Fixes the "robotic" sound: an English voice applies English phonetics to "mein / hai / jayegi".
  Sample `C_swara_hindi_script.mp3`.
- `neerja`: en-IN Neerja reads Roman-script Hinglish as Indian English (the 2026-11-01 setup). `A_neerja.mp3`.
- `expressive`: en-IN NeerjaExpressive, livelier, ~0.8 s slower to first audio. `B_neerja_expressive.mp3`.

Rate is `+2%` (`HUMAN_BOT_EDGE_RATE`), pitch `+0Hz` (`HUMAN_BOT_EDGE_PITCH`). Words missing from the
dictionary are read as-is, so add to `WORDS` when a new Hindi word shows up in replies.

**Expressive delivery**: every sentence carries the humanizer's emotion tag (`warm`, `enthusiastic`,
`concerned`, `thinking`, `neutral`) and `/tts?emo=` shifts rate and pitch per tag (`EMOTION_PROSODY` in
`llm/client.py`, small offsets so it stays one person). The cards pass the tag on prefetch and playback;
clips are cached per text+emotion.

**Body motion**: `tran_moment/motion/fit_gestures.py` now also mines up to two extra takes per gesture
(`nod__v2`, `head_wobble__v3`, ...; 9 variants from the existing footage) and `avatar.js` plays a different
take each time, never the same one twice in a row, on top of the per-gesture amplitude/timing variation,
cooldown and dt-based smoothing. The artist read-script for a cloned voice is in
`voice_clone/scripts/neha_read_script.md` (needs consent and a dry recording; see that file).

## Service request on the call (2026-11-03)

`crm/service_request.py` collects the facts from the customer's words (name, phone, car, problem, area,
registration, time, pickup), tells the model what is still missing (one question at a time, never invent),
and detects the read-back question "Main yeh request bhej doon?" and the customer's yes. On yes the server
calls `crm/acr_leads.py` (the website's own `/api/v1/leads` endpoint; `ACR_LEADS_MODE=dry` by default) and
speaks the backend's reference, or an honest "could not send, the team will call". The offline script asks
the same questions and reads the request back, so the flow works even with no live model. Record:
`analysis.service_request` + `next_step = service_request:<lead_id|queued>` in the conversation store, and
the CRM outbox. Tests: `python human_bot/tests/test_service_request.py`.
