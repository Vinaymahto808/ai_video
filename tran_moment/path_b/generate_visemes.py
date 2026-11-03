"""Viseme timings for a WAV: Rhubarb if installed, else a CPU letter map.

    uv run python tran_moment/path_b/generate_visemes.py --wav path_b/sample.wav --text "Namaste"
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RHUBARB = HERE / "tools" / "rhubarb.exe"
CACHE = HERE / "cache"

# Rough letter → Rhubarb mouth (A closed … H L, X rest)
LETTER = {
    "a": "C", "e": "B", "i": "D", "o": "F", "u": "G",
    "m": "A", "b": "A", "p": "A",
    "f": "G", "v": "G",
    "l": "H",
}


def letter_cues(text: str, duration: float) -> list[dict]:
    chars = [c for c in text if c.strip() or c in ".!?,;"]
    if not chars:
        return [{"start": 0.0, "end": duration, "value": "X"}]
    step = duration / len(chars)
    cues = []
    t = 0.0
    for ch in chars:
        cl = ch.lower()
        if ch in ".!?":
            val = "X"
        else:
            val = LETTER.get(cl, "B")
        cues.append({"start": round(t, 3), "end": round(t + step, 3), "value": val})
        t += step
    if cues:
        cues[-1]["end"] = round(duration, 3)
    return cues


def wav_duration(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wav", type=Path, required=True)
    ap.add_argument("--text", default="")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)

    wav = a.wav if a.wav.is_absolute() else (HERE / a.wav)
    if not wav.exists():
        print("missing wav:", wav, file=sys.stderr)
        return 1
    CACHE.mkdir(parents=True, exist_ok=True)
    out = a.out or (CACHE / (wav.stem + ".visemes.json"))
    if not out.is_absolute():
        out = HERE / out

    exe = RHUBARB if RHUBARB.exists() else shutil.which("rhubarb")
    if exe:
        tmp = out.with_suffix(".rhubarb.json")
        cmd = [str(exe), "-f", "json", "-o", str(tmp), str(wav)]
        if a.text:
            dialog = wav.with_suffix(".txt")
            dialog.write_text(a.text, encoding="utf-8")
            cmd[1:1] = ["-d", str(dialog)]
        print("running", " ".join(cmd))
        rc = subprocess.call(cmd)
        if rc != 0:
            return rc
        data = json.loads(tmp.read_text(encoding="utf-8"))
        data["engine"] = "rhubarb"
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print("wrote", out)
        return 0

    dur = wav_duration(wav)
    payload = {
        "metadata": {"soundFile": str(wav), "duration": dur},
        "mouthCues": letter_cues(a.text or wav.stem, dur),
        "engine": "letter-fallback",
        "note": "Install Rhubarb (path_b/install_rhubarb.ps1) for better timings.",
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("wrote", out, "(letter fallback — Rhubarb not installed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
