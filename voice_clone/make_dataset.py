"""Step 2 — cut the audio into short transcribed clips (Piper training format).

    uv run python voice_clone/make_dataset.py --voice hans_hi --language hi
    uv run python voice_clone/make_dataset.py --voice hans_en --language en --whisper medium

Whisper (faster-whisper, already in .venv) transcribes voice_clone/work/audio/*.wav with timestamps; neighbouring
segments are joined into 2–10 s clips; uncertain, noisy or very short ones are dropped; the text goes through the
same clean-up the live assistant uses (numbers → words), so training and speaking see the same text.
Hindi/Hinglish speech: --language hi (Devanagari transcripts, Hindi voice). English: --language en.

Output: voice_clone/work/dataset/<voice>/{wavs/, metadata.csv} and <voice>.zip for the Colab notebook.
**Listen to a few clips and read metadata.csv** — Whisper makes mistakes, and every wrong transcript teaches the
voice a wrong pronunciation. Delete bad lines (and their wavs) before uploading.
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
import zipfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from consent import require_consent  # noqa: E402

AUDIO = HERE / "work" / "audio"
OUT = HERE / "work" / "dataset"
SR = 22050


def read_wav(p: Path) -> np.ndarray:
    with wave.open(str(p)) as w:
        if w.getframerate() != SR or w.getnchannels() != 1:
            raise ValueError(f"{p}: run get_audio.py first (needs 22,050 Hz mono)")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def write_wav(p: Path, pcm: np.ndarray) -> None:
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.astype(np.int16).tobytes())


def group_segments(segs: list[dict], max_len: float = 10.0, max_gap: float = 0.6, min_len: float = 2.0) -> list[dict]:
    """Join consecutive confident segments into clips of min_len..max_len seconds."""
    clips, cur = [], None
    for s in segs:
        if cur and s["start"] - cur["end"] <= max_gap and s["end"] - cur["start"] <= max_len:
            cur["end"], cur["text"] = s["end"], cur["text"] + " " + s["text"]
            continue
        if cur and cur["end"] - cur["start"] >= min_len:
            clips.append(cur)
        cur = dict(s) if s["end"] - s["start"] <= max_len else None
    if cur and cur["end"] - cur["start"] >= min_len:
        clips.append(cur)
    return clips


def confident(seg, max_no_speech: float = 0.4, min_logprob: float = -0.9) -> bool:
    text = seg.text.strip()
    return bool(text) and seg.no_speech_prob <= max_no_speech and seg.avg_logprob >= min_logprob \
        and any(ch.isalpha() for ch in text) and "♪" not in text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--voice", required=True, help="dataset / voice name, e.g. hans_hi")
    ap.add_argument("--language", choices=["hi", "en"], required=True)
    ap.add_argument("--whisper", default="small", help="faster-whisper model: small (fast) | medium (better, slower)")
    a = ap.parse_args(argv)
    consent = require_consent()
    from faster_whisper import WhisperModel

    from app.voice.speech_text import prepare_for_speech

    files = sorted(AUDIO.glob("*.wav"))
    if not files:
        sys.exit(f"no audio in {AUDIO} — run get_audio.py first")
    model = WhisperModel(a.whisper, device="cpu", compute_type="int8")
    out = OUT / a.voice
    (out / "wavs").mkdir(parents=True, exist_ok=True)
    rows, kept_s, dropped = [], 0.0, 0
    for f in files:
        pcm = read_wav(f)
        segments, _ = model.transcribe(str(f), language=a.language, vad_filter=True, beam_size=5,
                                       condition_on_previous_text=False)
        segs = []
        for s in segments:
            if confident(s):
                segs.append({"start": s.start, "end": s.end, "text": s.text.strip()})
            else:
                dropped += 1
        for c in group_segments(segs):
            text, _ = prepare_for_speech(c["text"], a.language)
            if len(text) < 4:
                continue
            lo, hi = max(0, int((c["start"] - 0.12) * SR)), min(len(pcm), int((c["end"] + 0.18) * SR))
            cid = f"{a.voice}_{len(rows) + 1:05d}"
            write_wav(out / "wavs" / f"{cid}.wav", pcm[lo:hi])
            rows.append(f"{cid}.wav|{text}")
            kept_s += (hi - lo) / SR
        print(f"{f.name}: {len(rows)} clips so far")
    (out / "metadata.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (out / "dataset_info.json").write_text(json.dumps({
        "voice": a.voice, "language": a.language, "clips": len(rows), "minutes": round(kept_s / 60, 1),
        "speaker": consent["speaker_name"], "consent_signed_on": consent["signed_on"], "whisper": a.whisper,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    zpath = OUT / f"{a.voice}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in [out / "metadata.csv", out / "dataset_info.json", *sorted((out / "wavs").glob("*.wav"))]:
            z.write(p, p.relative_to(out))
    print(f"{len(rows)} clips, {kept_s / 60:.1f} min kept, {dropped} uncertain segments dropped → {zpath}")
    if kept_s < 20 * 60:
        print("warning: under 20 minutes — the voice will sound rough; 30–60 minutes of clean speech is better")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
