"""Step 1 — collect the consented speaker's audio as clean 22,050 Hz mono WAV files.

    uv run --with yt-dlp python voice_clone/get_audio.py --url "https://www.youtube.com/watch?v=..."
    uv run python voice_clone/get_audio.py --file "C:/videos/talk.mp4"
    ... --ranges "00:30-04:10,05:00-12:45"      keep only the parts where the consented speaker talks

Output: voice_clone/work/audio/<name>.wav. Use videos where this speaker talks alone (no interviewer, no
background music) — every other voice and the music end up in the cloned voice. Download only videos you are
allowed to use (your own channel: YouTube Studio → Download is the simplest route).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from consent import require_consent  # noqa: E402

WORK = HERE / "work"
RAW, AUDIO = WORK / "raw", WORK / "audio"


def ffmpeg() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        exe = shutil.which("ffmpeg")
        if not exe:
            raise SystemExit("ffmpeg not found: install imageio-ffmpeg or add ffmpeg to PATH")
        return exe


def parse_ranges(spec: str) -> list[tuple[float, float]]:
    """"00:30-04:10,1:02:00-1:05:30" -> [(30, 250), (3720, 3930)] seconds."""
    def sec(t: str) -> float:
        parts = [float(p) for p in t.strip().split(":")]
        v = 0.0
        for p in parts:
            v = v * 60 + p
        return v

    out = []
    for item in filter(None, (x.strip() for x in spec.split(","))):
        a, b = item.split("-")
        lo, hi = sec(a), sec(b)
        if hi <= lo:
            raise ValueError(f"range {item}: end before start")
        out.append((lo, hi))
    return out


def download(url: str) -> Path:
    try:
        import yt_dlp
    except ImportError:
        sys.exit("needs yt-dlp: uv run --with yt-dlp python voice_clone/get_audio.py --url ...")
    RAW.mkdir(parents=True, exist_ok=True)
    opts = {"format": "bestaudio/best", "outtmpl": str(RAW / "%(id)s.%(ext)s"), "noplaylist": True, "quiet": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info))


def to_wav(src: Path, dst: Path, ranges: list[tuple[float, float]] | None) -> float:
    """Mono 22,050 Hz 16-bit, loudness-normalised; only `ranges` when given. Returns seconds written."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    flt = "loudnorm=I=-20:TP=-2:LRA=11"
    if ranges:
        sel = "+".join(f"between(t,{a},{b})" for a, b in ranges)
        flt = f"aselect='{sel}',asetpts=N/SR/TB," + flt
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-vn", "-af", flt,
           "-ac", "1", "-ar", "22050", "-sample_fmt", "s16", str(dst)]
    subprocess.run(cmd, check=True)
    import wave

    with wave.open(str(dst)) as w:
        return w.getnframes() / w.getframerate()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="YouTube (or other yt-dlp supported) URL of the consented speaker")
    src.add_argument("--file", type=Path, help="local audio/video file")
    ap.add_argument("--ranges", default="", help="keep only these parts, e.g. 00:30-04:10,05:00-12:45")
    ap.add_argument("--name", default="", help="output name (default: video id / file name)")
    a = ap.parse_args(argv)
    consent = require_consent()
    source = download(a.url) if a.url else a.file
    if not source.exists():
        sys.exit(f"not found: {source}")
    name = re.sub(r"[^\w.-]+", "_", a.name or source.stem)
    secs = to_wav(source, AUDIO / f"{name}.wav", parse_ranges(a.ranges) if a.ranges else None)
    print(f"{AUDIO / (name + '.wav')}: {secs / 60:.1f} min of {consent['speaker_name']}")
    total = sum(__import__("wave").open(str(p)).getnframes() / 22050 for p in AUDIO.glob("*.wav"))
    print(f"all audio so far: {total / 60:.1f} min (aim for 30+ min of clean speech before training)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
