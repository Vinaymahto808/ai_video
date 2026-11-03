"""Assemble the AIVA-style sample call from the clips_neha set: she speaks, listens while the customer talks
(silent reaction loop), answers, reads the booking back, closes. Also exports the greeting as welcome.mp4.

    python tran_moment/make_demo.py            # -> prepared/clips_neha/demo/aiva_style_call.mp4 + welcome.mp4

Re-run after synth_talk_clips.py / synth_angle_clips.py. Pure ffmpeg; spoken clips keep their audio, loops get
a silent track so the concat has one continuous AAC stream.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
CLIPS = ROOT / "prepared" / "clips_neha"
DEMO = CLIPS / "demo"
# (file, seconds to keep; None = whole clip)
SEQUENCE = [("talk_greeting.mp4", None), ("attentive.mp4", 3.0), ("talk_ask.mp4", None), ("listening.mp4", 2.5),
            ("talk_explain.mp4", None), ("agree.mp4", 2.0), ("talk_reassure.mp4", None), ("attentive.mp4", 2.5),
            ("talk_price.mp4", None), ("small_nod.mp4", 2.0), ("talk_booking.mp4", None), ("attentive.mp4", 2.0),
            ("talk_close.mp4", None), ("relax.mp4", 2.5)]


def has_audio(path: pathlib.Path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout
    return out.strip() != ""


def main() -> int:
    DEMO.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        parts = []
        for i, (name, cut) in enumerate(SEQUENCE):
            src = CLIPS / name
            if not src.exists():
                print(f"skip {name}: missing")
                continue
            out = pathlib.Path(td) / f"p{i:02d}.mp4"
            cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src)]
            if not has_audio(src):
                cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest"]
            if cut:
                cmd += ["-t", str(cut)]
            cmd += ["-vf", "scale=720:960,fps=24,format=yuv420p", "-c:v", "libx264", "-crf", "20", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "96k", str(out)]
            subprocess.check_call(cmd)
            parts.append(out)
        lst = pathlib.Path(td) / "list.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
        subprocess.check_call(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c:v", "libx264", "-crf", "20",
                               "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(DEMO / "aiva_style_call.mp4")])
    subprocess.check_call(["ffmpeg", "-v", "error", "-y", "-i", str(CLIPS / "talk_greeting.mp4"), "-c", "copy", str(DEMO / "welcome.mp4")])
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(DEMO / "aiva_style_call.mp4")],
                         capture_output=True, text=True).stdout.strip()
    print(f"demo/aiva_style_call.mp4 {float(dur):.1f} s, demo/welcome.mp4 ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
