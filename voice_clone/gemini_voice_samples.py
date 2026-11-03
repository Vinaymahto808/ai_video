"""Make female-voice audition samples with Gemini TTS (no local model needed).

    python voice_clone/gemini_voice_samples.py
    python voice_clone/gemini_voice_samples.py --voices Kore,Aoede,Sulafat --lines hinglish
    python voice_clone/gemini_voice_samples.py --voices Kore --lines hindi,english

Reads the Gemini key from voice_clone/.env (or the repo-root .env), asks each voice to read the same
Hinglish / Hindi / English sales lines in a Delhi-NCR female accent, and writes 24 kHz WAVs to
voice_clone/work/voice_samples/. Listen, pick one, then set GEMINI_TTS_VOICE to it.

Notes (measured 2026-10-29):
* `gemini-2.5-flash-preview-tts` applies the style instruction (it is not read aloud) and returns raw
  audio/L16 at 24 kHz.
* `gemini-3.8-flash-tts` returns a complete 24 kHz WAV but *reads the style instruction aloud*, so the
  style prefix is only used on the 2.5 model here.
* Free tier is ~3 TTS requests/minute — the script spaces calls 62 s apart.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "work" / "voice_samples"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

LINES = {
    "hinglish": "Hi! Main Riya hoon, Hans Hyundai se. Aap Creta dekh rahe hain, ya koi aur car?",
    "hindi": "नमस्ते! मैं रिया हूँ, हैन्स हुंडई से। क्रेटा की कीमत ग्यारह लाख दस हज़ार से शुरू होती है।",
    "english": "Hi, I'm Riya from Hans Hyundai. The Creta starts at eleven lakh ten thousand, ex-showroom Delhi.",
}

STYLE = (
    "You are Riya, a 28-year-old sales consultant from Delhi NCR, India, on a video call with a customer. "
    "Speak in natural conversational Indian Hinglish with a Delhi Hindi accent — warm, friendly, confident, "
    "medium pace, a slight smile in the voice. Keep English words in an Indian accent. "
    "Do not sound like a newsreader, an advertisement, or an IVR. Say exactly: "
)


def load_key() -> str:
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    for env in (HERE / ".env", HERE.parent / ".env"):
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.lower().startswith("gemini_api_key="):
                    return line.split("=", 1)[1].strip()
    sys.exit("GEMINI_API_KEY not found (env, voice_clone/.env or .env)")


def synth(key: str, model: str, voice: str, text: str, styled: bool = True) -> bytes:
    prompt = (STYLE + text) if styled else text
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    req = urllib.request.Request(
        API.format(model=model) + "?key=" + key,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read().decode())
    for p in data["candidates"][0]["content"]["parts"]:
        inline = p.get("inlineData") or p.get("inline_data")
        if inline and inline.get("data"):
            raw = base64.b64decode(inline["data"])
            mime = inline.get("mimeType") or inline.get("mime_type") or ""
            if mime.startswith("audio/wav") or raw[:4] == b"RIFF":
                return raw  # already a complete WAV
            rate = 24000
            if "rate=" in mime:
                rate = int(mime.split("rate=")[1].split(";")[0])
            return _wav(raw, rate)
    raise RuntimeError(f"no audio in response: {json.dumps(data)[:400]}")


def _wav(pcm: bytes, rate: int = 24000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def duration(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav)) as w:
        return round(w.getnframes() / w.getframerate(), 2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="gemini-2.5-flash-preview-tts")
    ap.add_argument("--voices", default="Kore,Aoede,Sulafat,Vindemiatrix")
    ap.add_argument("--lines", default="hinglish")
    ap.add_argument("--no-style", action="store_true", help="send the bare line (no Delhi-NCR instruction)")
    ap.add_argument("--gap", type=float, default=62.0, help="seconds between calls (free tier ~3/min)")
    a = ap.parse_args(argv)

    key = load_key()
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(v.strip(), n.strip()) for v in a.voices.split(",") if v.strip()
            for n in a.lines.split(",") if n.strip()]
    first = True
    for voice, name in jobs:
        if not first:
            time.sleep(a.gap)
        first = False
        try:
            wav = synth(key, a.model, voice, LINES[name], styled=not a.no_style)
        except urllib.error.HTTPError as ex:
            print(f"{voice}/{name}: HTTP {ex.code} {ex.read()[:160]!r}")
            continue
        except Exception as ex:  # noqa: BLE001
            print(f"{voice}/{name}: {type(ex).__name__}: {ex}")
            continue
        p = OUT / f"{voice.lower()}_{name}.wav"
        p.write_bytes(wav)
        print(f"ok {p.name}  {duration(wav)}s")
    print(f"\nlisten: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
