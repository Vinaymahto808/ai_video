"""Phase 1 acceptance check — is the consented cloned voice really serving?

    uv run python voice_clone/verify_clone.py                  # VOICE_CONVERT_SPEAKER, or the only speaker on disk
    uv run python voice_clone/verify_clone.py --speaker yash
    uv run python voice_clone/verify_clone.py --url http://127.0.0.1:8020

Checks, in order:

 1. the consent record is complete and its signed document exists   (voice_clone/consent/consent.json)
 2. voice_models/clone/<speaker>.target.pt exists                   (made by clone_speaker.py)
 3. the service answers GET /health and lists <speaker>
 4. a WAV round-trips through POST /convert and a WAV comes back    (bytes + seconds reported)

Exit code 0 only when 1-4 pass. It downloads nothing and trains nothing. The last acceptance point — that a
reply is never lost when the service is down — is covered by tests/test_voice_clone.py::test_voice_conversion_and_fallback
(the app keeps plain Piper speech on any conversion failure).
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
OUT = ROOT / "voice_models" / "clone"
DEFAULT_URL = "http://127.0.0.1:8020"

from consent import require_consent  # noqa: E402


def speakers_on_disk(out: Path | None = None) -> list[str]:
    """Names with a .target.pt embedding in `out` (the speakers clone_speaker.py has made)."""
    out = out or OUT
    return sorted(p.name[: -len(".target.pt")] for p in out.glob("*.target.pt"))


def _under_root(path: Path) -> str:
    """Path relative to the repo when possible, else the path itself (used only to print the report)."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def health(url: str, timeout: float = 5.0) -> list[str]:
    """Speaker names the running service reports (raises on a connection or HTTP error)."""
    with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=timeout) as r:
        return list(json.loads(r.read().decode()).get("speakers", []))


def convert(url: str, speaker: str, wav: bytes, timeout: float = 120.0) -> bytes:
    """Send a WAV to the converter and return the converted WAV (raises when it is not a WAV)."""
    q = url.rstrip("/") + "/convert?" + urllib.parse.urlencode({"speaker": speaker})
    req = urllib.request.Request(q, data=wav, headers={"Content-Type": "audio/wav"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = r.read()
    if not out.startswith(b"RIFF"):
        raise ValueError("service returned no WAV")
    return out


def wav_seconds(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav)) as w:
        return w.getnframes() / w.getframerate()


def probe_wav() -> bytes:
    """A real Piper sample when one exists, else a short tone — enough to prove the pipe."""
    samples = sorted((HERE / "work" / "piper_samples").glob("*.wav"))
    if samples:
        return samples[0].read_bytes()
    sr, buf = 22050, io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 180 * i / sr)))
                               for i in range(int(sr * 2))))
    return buf.getvalue()


def settings_defaults() -> tuple[str, str]:
    """(url, speaker) from the app's .env via get_settings, or the environment when the app is not importable."""
    try:
        sys.path.insert(0, str(ROOT))
        from app.config import get_settings

        s = get_settings()
        return s.voice_convert_url or "", s.voice_convert_speaker or ""
    except Exception:  # noqa: BLE001
        return os.environ.get("VOICE_CONVERT_URL", ""), os.environ.get("VOICE_CONVERT_SPEAKER", "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--speaker", default="", help="speaker name (default: VOICE_CONVERT_SPEAKER, else the only one on disk)")
    ap.add_argument("--url", default="", help=f"service URL (default: VOICE_CONVERT_URL, else {DEFAULT_URL})")
    a = ap.parse_args(argv)

    try:
        consent = require_consent()
        print(f"[1/4] consent OK — {consent['speaker_name']} ({consent['signed_on']})")
    except SystemExit as ex:
        print(f"[1/4] consent FAILED — {ex}")
        return 1

    url_default, speaker_default = settings_defaults()
    url = a.url or url_default or DEFAULT_URL
    speaker = a.speaker or speaker_default or (speakers_on_disk() or [""])[0]

    target = OUT / f"{speaker}.target.pt" if speaker else None
    if not speaker or not target or not target.exists():
        print(f"[2/4] embedding MISSING — make one: clone_speaker.py --name <name> --wav <clean 20-60 s speech.wav>")
        return 1
    print(f"[2/4] embedding OK — {_under_root(target)}")

    try:
        names = health(url)
    except (urllib.error.URLError, OSError) as ex:
        print(f"[3/4] service unreachable at {url} ({ex}) — start it: voice_clone/start_voice_service.ps1")
        return 1
    if speaker not in names:
        print(f"[3/4] service is up but does not know '{speaker}' (has: {', '.join(names) or 'none'}) — restart it after making the embedding")
        return 1
    print(f"[3/4] service OK — {url} knows {', '.join(names)}")

    wav = probe_wav()
    try:
        t = time.perf_counter()
        out = convert(url, speaker, wav)
        secs = time.perf_counter() - t
    except Exception as ex:  # noqa: BLE001
        print(f"[4/4] conversion FAILED — {type(ex).__name__}: {ex}")
        return 1
    print(f"[4/4] conversion OK — {len(wav)} B ({wav_seconds(wav):.1f} s) in, {len(out)} B ({wav_seconds(out):.1f} s) "
          f"out, {secs:.1f} s on this CPU")
    print("\nVerified: the cloned voice is serving. Set VOICE_CONVERT_URL + VOICE_CONVERT_SPEAKER in .env and restart "
          "the assistant; if the service ever stops, replies fall back to plain Piper (never lost).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
