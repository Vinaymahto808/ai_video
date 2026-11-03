"""Conversation clips: Neha speaks real service-advisor lines with lip sync, blinks, glances, brows, smiles
and small nods at the phrase breaks, like a person on a video call. CPU only.

    python tran_moment/synth_talk_clips.py                 # all lines -> prepared/clips_neha/talk_*.mp4 (+ manifest)
    python tran_moment/synth_talk_clips.py --only talk_greeting talk_booking
    python tran_moment/synth_talk_clips.py --no-audio      # muted files only

Voice: the project's Edge TTS setup (hi-IN Swara reading Devanagari, female, per-emotion prosody). Lip timing:
Rhubarb if tran_moment/path_b/tools/rhubarb.exe exists, otherwise Edge's word boundaries + a letter map
(each word's letters spread over the word's own duration; rests between words). Audio is muxed into the
clip (AAC) so a file stands alone as a demo; the card plays clips muted and speaks live TTS itself.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PB = ROOT / "path_b"
RHUBARB = PB / "tools" / "rhubarb.exe"
sys.path.insert(0, str(ROOT.parent / "human_bot"))

_spec = importlib.util.spec_from_file_location("synth_face_clips", ROOT / "synth_face_clips.py")
sfc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sfc)
sc = sfc.sc

VOICE = "hi-IN-SwaraNeural"
# Calm, polite delivery (2026-11-03 feedback "aaram se, politely"): a little slower than the live bot, soft pitch.
PROSODY = {"neutral": ("-8%", "+0Hz"), "warm": ("-8%", "+2Hz"), "enthusiastic": ("-5%", "+5Hz"),
           "concerned": ("-12%", "-4Hz"), "thinking": ("-12%", "+0Hz")}

# calm face profile: slower expression changes, longer lip crossfades, smaller gestures, fewer blinks
CALM = {"EXPR_IN": 0.45, "EXPR_OUT": 0.40, "GLANCE_IN": 0.22, "GLANCE_OUT": 0.26, "BROW_RAMP": 0.30,
        "MOUTH_FADE": 0.09, "IDLE_SCALE": 0.55, "MOUTH_STRENGTH": 0.55}   # 0.55: lips form the word, cheeks stay put
NOD_AMP, NOD_SLOW = 0.85, 1.15        # the learned tracks are already calm (fit_gestures CALM profile); a touch softer and slower here
BLINK_EVERY = (3.6, 5.2)              # seconds between blinks (min, max)

# (name, emotion, line). Polite advisor register: aap/ji, one thought per sentence, short pauses between them.
# Facts stay generic: no prices, no promises the KB does not make.
LINES = [
    ("talk_greeting", "warm", "Namaste ji. Main Neha, Auto Car Repair se. Bataiye, aapki gaadi mein kya dikkat hai? Main poori help karungi."),
    ("talk_ask", "neutral", "Ji, Creta hai aapki. Yeh brake ki awaaz kab se aa rahi hai? Aur zyada kab hoti hai, slow speed par ya highway par?"),
    ("talk_explain", "neutral", "Dekhiye ji, pehle hum gaadi ko scan karte hain. Phir brake pads aur disc check hote hain. Genuine parts hi lagte hain, warranty ke saath."),
    ("talk_reassure", "warm", "Aap bilkul chinta mat kijiye. Pickup free hai. Hamari team Gurgaon mein aapke ghar se gaadi le jaayegi, aur shaam tak wapas pahuncha degi."),
    ("talk_price", "thinking", "Exact quote gaadi dekh kar hi banega ji. Main confirm karke aapko call par bata dungi. Koi hidden charge nahi hoga."),
    ("talk_booking", "warm", "Toh main note kar leti hoon. Creta, brake ka kaam, Gurgaon se kal subah pickup. Main yeh request bhej doon?"),
    ("talk_close", "warm", "Bahut shukriya ji. Aapki request note ho gayi hai. Hamari team aapko jaldi call karegi. Aapka din accha rahe."),
]

# letter -> Rhubarb mouth letter (A closed, B teeth, C open, D wide, F pucker, G f/v, H l, X rest)
LETTER = {"a": "C", "e": "B", "i": "D", "o": "F", "u": "G", "m": "A", "b": "A", "p": "A", "f": "G", "v": "G", "l": "H",
          "w": "F", "y": "D", "s": "B", "z": "B", "t": "B", "d": "B", "n": "B", "r": "B", "k": "C", "g": "C", "h": "C", "j": "B", "c": "B"}


def tts(text: str, emotion: str, mp3: Path) -> list[tuple[float, float, str]]:
    """Edge TTS -> mp3 + [(start, duration, word)] from the WordBoundary events."""
    from llm.hindi_script import to_devanagari
    import edge_tts
    spoken = to_devanagari(text)
    rate, pitch = PROSODY.get(emotion, PROSODY["neutral"])

    async def run():
        try:
            com = edge_tts.Communicate(spoken, VOICE, rate=rate, pitch=pitch, boundary="WordBoundary")
        except TypeError:                     # older edge-tts: boundaries are on by default
            com = edge_tts.Communicate(spoken, VOICE, rate=rate, pitch=pitch)
        audio, words = b"", []
        async for ch in com.stream():
            if ch["type"] == "audio":
                audio += ch["data"]
            elif ch["type"] == "WordBoundary":
                words.append((ch["offset"] / 1e7, ch["duration"] / 1e7, ch["text"]))
        mp3.write_bytes(audio)
        return words

    return asyncio.run(run())


def roman_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z]+", text)]


def cues_from_words(words: list[tuple[float, float, str]], text: str, duration: float) -> list[dict]:
    """Letters of each spoken word spread over that word's time; a rest (X) wherever the voice pauses."""
    roman = roman_words(text)
    if not words:                              # no boundary events: spread the words evenly over the audio
        n = max(1, len(roman))
        step = max(0.1, (duration - 0.3)) / n
        words = [(0.15 + i * step, step * 0.85, w) for i, w in enumerate(roman)]
    cues: list[dict] = []
    t_prev = 0.0
    for i, (start, dur, _spoken) in enumerate(words):
        if start - t_prev > 0.12:
            cues.append({"start": round(t_prev, 3), "end": round(start, 3), "value": "X"})
        letters = [c for c in (roman[i] if i < len(roman) else "a").lower() if c.isalpha()] or ["a"]
        # consonant clusters collapse: keep vowels and the first consonant of a run
        keep = []
        for c in letters:
            if c in "aeiou" or not keep or keep[-1] in "aeiou":
                keep.append(c)
        step = max(dur, 0.08) / len(keep)
        for k, c in enumerate(keep):
            cues.append({"start": round(start + k * step, 3), "end": round(start + (k + 1) * step, 3), "value": LETTER.get(c, "B")})
        t_prev = start + max(dur, 0.08)
    cues.append({"start": round(t_prev, 3), "end": round(max(duration, t_prev + 0.05), 3), "value": "X"})
    # merge micro-cues (< 90 ms) and repeats into their neighbour so the mouth does not flicker
    merged: list[dict] = []
    for c in cues:
        if merged and ((c["end"] - c["start"]) < 0.09 or merged[-1]["value"] == c["value"]):
            merged[-1]["end"] = c["end"]
        else:
            merged.append(dict(c))
    return merged


def cues_rhubarb(wav: Path) -> list[dict] | None:
    if not RHUBARB.exists():
        return None
    out = wav.with_suffix(".rhubarb.json")
    rc = subprocess.call([str(RHUBARB), "-r", "phonetic", "-f", "json", "-o", str(out), str(wav)])
    if rc != 0:
        return None
    return json.loads(out.read_text(encoding="utf-8"))["mouthCues"]


def mp3_to_wav(mp3: Path, wav: Path) -> float:
    subprocess.check_call(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-ac", "1", "-ar", "24000", str(wav)])
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(wav)])
    return float(out.strip())


def speech_onset(wav: Path) -> float:
    """Seconds of leading silence in the audio (ffmpeg silencedetect); 0 if none is reported."""
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(wav), "-af", "silencedetect=n=-35dB:d=0.08", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"silence_start: (-?[\d.]+)\s+.*?silence_end: ([\d.]+)", r.stderr, re.S)
    return float(m.group(2)) if m and float(m.group(1)) <= 0.02 else 0.0


def align(cues: list[dict], onset: float, lead: float = 0.05) -> list[dict]:
    """Shift the timeline so the first mouth shape opens `lead` s before the voice is heard."""
    first = next((c["start"] for c in cues if c["value"] != "X"), None)
    if first is None:
        return cues
    shift = (onset - lead) - first
    if abs(shift) < 0.03:
        return cues
    out = []
    for c in cues:
        s, e = c["start"] + shift, c["end"] + shift
        if e <= 0:
            continue
        out.append({"start": round(max(0.0, s), 3), "end": round(e, 3), "value": c["value"]})
    if out and out[0]["start"] > 0:
        out.insert(0, {"start": 0.0, "end": out[0]["start"], "value": "X"})
    return out


def plan(name: str, emotion: str, cues: list[dict], duration: float, tracks: dict) -> sfc.Script:
    """Calm delivery: a blink every 3.6-5.2 s, at most one glance in the longest pause, brows up on a question,
    a small slow nod (half amplitude, stretched) at every second phrase end."""
    rests = [c for c in cues if c["value"] == "X" and 0.3 < c["start"] < duration - 0.4 and c["end"] - c["start"] >= 0.18]
    nods, glances = [], []
    for k, r in enumerate(rests):
        if k % 2 == 0:
            tname = ("slow_nod", "small_nod")[(k // 2) % 2]
            nods.append((max(0.0, r["start"] - 0.2), tname, NOD_AMP, NOD_SLOW))
    if not nods:
        nods = [(duration * 0.45, "small_nod", NOD_AMP, NOD_SLOW)]
    long_rests = [r for r in rests if (r["end"] - r["start"]) > 0.45]
    if long_rests:
        r = max(long_rests, key=lambda c: c["end"] - c["start"])
        glances.append((r["start"], 0.4))
    blinks, t = [], 1.4
    while t < duration - 0.3:
        blinks.append(round(t, 2))
        lo, hi = BLINK_EVERY
        t += lo + ((hash(name) >> (len(blinks) * 3)) % 16) / 10.0 * (hi - lo) / 1.6
    # while speaking only the soft smile: the warm / big smiles move the cheeks on top of the lips
    expr = {"warm": "smile_soft", "enthusiastic": "smile_warm", "neutral": "smile_soft", "thinking": "smile_soft", "concerned": None}[emotion]
    exprs = [(expr, 0.15, duration - 0.5)] if expr else []
    brows = []
    if "?" in LINES[[n for n, _, _ in LINES].index(name)][2]:
        q = rests[-1]["start"] - 0.9 if rests else duration - 1.4
        brows.append(("brows_up", max(0.2, q), 0.8))
    if emotion == "enthusiastic":
        brows.append(("brows_up", 0.3, 0.4))
    if emotion == "concerned":
        brows.append(("brows_in", 0.2, duration - 0.6))
    return sfc.Script(name, None, dur=duration + 0.35, blinks=tuple(blinks), expr=tuple(exprs), glances=tuple(glances),
                      brows=tuple(brows), mouth=cues, pingpong=False, nods=tuple(nods))


def main(argv: list[str]) -> int:
    only = set(argv[argv.index("--only") + 1:]) if "--only" in argv else None
    with_audio = "--no-audio" not in argv
    tracks, idle = sc.load_tracks()
    for k, v in CALM.items():                 # calm face profile for the spoken clips
        setattr(sfc, k, v)
    kit = sfc.Kit()
    spec = importlib.util.spec_from_file_location("make_clips", ROOT / "make_clips.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    mk.OUT.mkdir(parents=True, exist_ok=True)
    audio_dir = mk.OUT / "audio"
    audio_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        for name, emotion, line in LINES:
            if only and name not in only:
                continue
            mp3 = audio_dir / f"{name}.mp3"
            words = tts(line, emotion, mp3)
            wav = Path(td) / f"{name}.wav"
            duration = mp3_to_wav(mp3, wav)
            cues = cues_rhubarb(wav) or align(cues_from_words(words, line, duration), speech_onset(wav))
            (audio_dir / f"{name}.visemes.json").write_text(json.dumps({"text": line, "duration": duration, "mouthCues": cues}, ensure_ascii=False, indent=1), encoding="utf-8")
            script = plan(name, emotion, cues, duration, tracks)
            raw = Path(td) / f"{name}.mp4"
            n = sfc.render(script, kit, tracks, idle, raw)
            silent = Path(td) / f"{name}.norm.mp4"
            mk.normalise(raw, silent, pingpong=False)
            dst = mk.OUT / f"{name}.mp4"
            if with_audio:
                subprocess.check_call(["ffmpeg", "-v", "error", "-y", "-i", str(silent), "-i", str(mp3), "-c:v", "copy",
                                       "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", str(dst)])
            else:
                shutil.copyfile(silent, dst)
            print(f"{name:14s} {duration:5.2f}s {n:4d} frames, {len(words):2d} words, {len(cues):3d} cues, "
                  f"{len(script.nods)} nods, {len(script.blinks)} blinks -> {dst.relative_to(ROOT)} ({dst.stat().st_size // 1024} KB)")
    m = mk.write_manifest()
    print("manifest speaking:", m["states"].get("speaking"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
