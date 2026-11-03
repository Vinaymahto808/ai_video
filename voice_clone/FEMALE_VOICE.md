# Female Indian (Delhi NCR) voice — what is live, and why

Decision (2026-10-29): the assistant speaks as a **female Indian voice with a Delhi-NCR Hindi/Hinglish
delivery**, one voice across Galaxy Toyota, Hans Hyundai and Auto Car Repair. This file records the choice,
where it is configured, and what must not be used.

## The voice

**Update 2026-11-01:** the live voice is now **Microsoft `en-IN-NeerjaNeural`** via edge-tts for every answer (`HUMAN_BOT_TTS_PROVIDER=edge` in `voice_clone/.env`). Gemini Kore stayed as the preferred sound but its free tier (~10 clips/day) made the chain fall to Edge mid-conversation, so the voice changed from answer to answer. One quota-free voice fixes that; `hi-IN-SwaraNeural` is used only when a reply is mostly Devanagari. Audition clips of the same Hinglish line: `work/voice_samples/edge_neerja_hinglish.mp3`, `edge_neerjaexpressive_hinglish.mp3`, `edge_swara_hinglish.mp3` (switch with `HUMAN_BOT_EDGE_VOICE_EN`). The table below is the earlier Gemini decision.

| | |
|---|---|
| Provider | Google Gemini TTS (`TTS_PROVIDER=gemini`) |
| Voice | **`Kore`** (female) |
| Model | `gemini-3.8-flash-lite-tts` (~4 s per reply) |
| Accent | Hindi and Hinglish come back in a natural Indian accent; English words keep an Indian delivery |
| Fallback | Browser Web Speech: a **female `en-IN`/`hi-IN`** voice is picked (`Microsoft Heera`, `Microsoft Kalpana`, `Google हिन्दी`) |

Audition clips (24 kHz WAV) are in `voice_clone/work/voice_samples/`:

| File | Voice | Notes |
|---|---|---|
| `compare_hinglish.wav` | Kore → Aoede → Sulafat → Vindemiatrix | same Hinglish line, 0.45 s gaps |
| `compare_hindi.wav` | Kore → Aoede | Hindi price line |
| `compare_english.wav` | Kore → Aoede | English price line |
| `kore_*.wav`, `aoede_*.wav`, `sulafat_hinglish.wav`, `vindemiatrix_hinglish.wav` | single clips | `*_hinglish`, `*_hindi`, `*_english` |

Regenerate (needs `gemini_api_key` in `voice_clone/.env`):

```powershell
python voice_clone/gemini_voice_samples.py --voices Kore,Aoede --lines hinglish,hindi,english
```

Free tier allows **~10 speech clips per day per model**, so the script spaces calls 62 s apart and a 429 means
"come back tomorrow". `main` caches replies (the demo caches `/tts` by text too), but real traffic needs a paid
Gemini key.

## Why not the other options

* **Piper `hi_IN-priyamvada-medium`** (free Hindi female) — the model card says the training data is
  **CC BY-NC-SA 4.0**: non-commercial. Fine for a local test, not for the dealer sites. The other Piper Hindi
  voices (`pratham`, `rohan`) are male.
* **Piper + OpenVoice clone** (`voice_clone/`) — the only way to get an *owned* Delhi-NCR voice, but it needs a
  real female speaker's recording and a **signed** consent (`consent/CONSENT_FORM_narrator.md`); the narrator
  consent in this repo is still unsigned and `consent.json` still names Yash Narula (male). Until that exists,
  do not configure a cloned speaker.
* **Groq / Canopy Orpheus** (`hannah`) — female but English-only: a Hinglish line comes out with an American
  accent. Still available with `HUMAN_BOT_TTS_PROVIDER=groq`.

## Style notes: only for models that follow them

Measured on the live key 2026-10-29, same 9-word Hindi line:

| Model | Result |
|---|---|
| `gemini-3.8-flash-tts` | **33.8 s** — it read the voice notes aloud, then the line |
| `gemini-3.8-flash-lite-tts` | the same read-aloud, in every prompt format tried |
| `gemini-2.5-flash-preview-tts` | **5.8 s** — followed the notes without reading them |

So the code sends the **bare transcript** to the 3.x speech models (`app/voice/providers.py`,
`human_bot/llm/client.py`). The Delhi-NCR direction in `app/config.py` (`gemini_tts_style`) is used only by
`gemini-2.5-*` models; switching to 2.5 buys the style control for a slower reply and the same 10/day quota.

The 3.x models also return a complete WAV (`audio/wav`), not raw L16 — `GeminiTts` now passes it through
instead of wrapping it in a second WAV header.

## Where it is configured

* `D:\New folder (2)\VIDEO\.env` — `TTS_PROVIDER=gemini`, `GEMINI_TTS_VOICE=Kore`,
  `GEMINI_TTS_MODEL=gemini-3.8-flash-lite-tts`
* `D:\New folder (2)\VIDEO\app\config.py` — `gemini_tts_style` (Delhi NCR, warm, Hinglish)
* `D:\New folder (2)\VIDEO\app\voice\providers.py` — model-aware notes + WAV passthrough
* `human_bot/llm/client.py` — demo `/tts`: Gemini `Kore`, browser voice as fallback
* `human_bot/web/index.html`, `human_bot/web/embed.html` — female `en-IN`/`hi-IN` voice picker
* `human_bot/persona/persona_*.json` — `voice` block documents the female Delhi-NCR voice; both personas are
  female (Neha · Galaxy Toyota, Riya · Hans Hyundai) so the Hindi verb endings match the voice

## Change the voice

```powershell
# one line, then restart the server
GEMINI_TTS_VOICE=Aoede
```

`Kore` = warm and steady (default), `Aoede` = breezier, `Sulafat` = softer, `Vindemiatrix` = gentler.
Listen to the comparison files first — they are the same line in each voice.
