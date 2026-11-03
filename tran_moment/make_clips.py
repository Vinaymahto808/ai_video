"""Normalise generated Neha video clips and install them into prepared/clips_neha/ (+ manifest).

Input: raw clips from any image-to-video tool (Kling, Runway, Luma, Veo, FAL ...) generated from
prepared/ref_720x1280.png with the prompts in CLIP_PROMPTS.md. Name each raw file after its key:
idle.mp4, small_nod.mp4, nod.mp4, slow_nod.mp4, tilt_head.mp4, head_wobble.mp4, look_up.mp4, open_palm.mp4,
both_hands.mp4, lean_in.mp4, greeting.mp4, listening.mp4 (takes: idle_2.mp4, idle_3.mp4 ...).

    python tran_moment/make_clips.py <raw_dir>             # normalise + ping-pong + install + manifest
    python tran_moment/make_clips.py <raw_dir> --no-pingpong   # when the tool already returned a loop

Each clip becomes 720x960 (3:4), 24 fps, silent, h264 yuv420p, faststart, forward+reverse so it loops
without a cut. Existing files with the same name are replaced; other files stay. CPU only (ffmpeg).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "prepared" / "clips_neha"
MANIFEST = OUT / "manifest.json"

# gesture key -> which clip keys may play for it (first existing wins order; all existing are listed)
GESTURE_SOURCES = {
    "idle": ["idle", "relax"], "pause": ["idle", "relax", "look_down"],
    "small_nod": ["small_nod", "listening", "attentive"], "nod": ["nod", "small_nod", "agree"], "slow_nod": ["slow_nod", "thank", "apologetic"],
    "tilt_head": ["tilt_head", "slow_nod", "concerned", "doubt", "turn_left", "turn_right"],
    "head_wobble": ["head_wobble", "small_nod", "agree"],
    "look_up": ["look_up", "look_up_long", "look_left", "look_right"], "open_palm": ["open_palm", "lean_close"],
    "both_hands": ["both_hands", "laugh"], "lean_in": ["lean_in", "both_hands", "lean_close", "attentive"], "greeting": ["greeting"],
    "surprised": ["surprised", "surprised_up"], "question": ["surprised", "surprised_up", "doubt"], "talking": ["talking"],
    # angle / mood clips under their own names too (synth_angle_clips.py), for a persona that emits them directly
    "look_left": ["look_left"], "look_right": ["look_right"], "look_down": ["look_down"], "look_up_long": ["look_up_long"],
    "turn_left": ["turn_left"], "turn_right": ["turn_right"], "lean_close": ["lean_close"], "agree": ["agree"],
    "head_shake": ["head_shake"], "concerned": ["concerned"], "doubt": ["doubt"], "apologetic": ["apologetic"],
    "thank": ["thank"], "laugh": ["laugh"], "attentive": ["attentive"], "surprised_up": ["surprised_up"], "relax": ["relax"],
}
STATE_SOURCES = {"idle": ["idle", "relax"], "greeting": ["greeting", "idle"], "listening": ["listening", "small_nod", "attentive", "look_down"],
                 "thinking": ["look_up", "look_up_long", "look_left", "look_right"], "backchannel": ["head_wobble", "small_nod", "agree"],
                 "speaking": ["talking", "talk_greeting", "talk_ask", "talk_explain", "talk_reassure", "talk_price",
                              "talk_booking", "talk_close"]}


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ffmpeg failed: {' '.join(cmd[:3])} ...\n{r.stderr[-800:]}")


def normalise(src: Path, dst: Path, pingpong: bool = True) -> None:
    vf = "scale=720:960:force_original_aspect_ratio=increase,crop=720:960,fps=24"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "n.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-an", "-vf", vf,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", str(tmp)])
        if pingpong:
            run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-filter_complex",
                 "[0:v]reverse[r];[0:v][r]concat=n=2:v=1:a=0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-crf", "20", "-movflags", "+faststart", str(dst)])
        else:
            shutil.copyfile(tmp, dst)


def key_of(name: str) -> str:
    return re.sub(r"(_\d+)?$", "", Path(name).stem)


def write_manifest() -> dict:
    files = sorted(p.name for p in OUT.glob("*.mp4"))
    by_key: dict[str, list[str]] = {}
    for f in files:
        by_key.setdefault(key_of(f), []).append(f)

    def pick(sources: list[str]) -> list[str]:
        out: list[str] = []
        for s in sources:
            out += by_key.get(s, [])
        return out

    m = {
        "_note": "Neha reaction set generated from prepared/ref_720x1280.png (AI portrait, see CONSENT.md) and "
                 "installed by tran_moment/make_clips.py. 720x960, 24 fps, muted, ping-pong loops.",
        "version": 2, "set": "neha", "crossfade_ms": 350,
        "states": {k: v for k, v in ((k, pick(s)) for k, s in STATE_SOURCES.items()) if v},
        "gestures": {k: v for k, v in ((k, pick(s)) for k, s in GESTURE_SOURCES.items()) if v},
    }
    MANIFEST.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")
    return m


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    raw = Path(argv[1])
    pingpong = "--no-pingpong" not in argv
    OUT.mkdir(parents=True, exist_ok=True)
    done = 0
    for src in sorted(raw.glob("*.mp4")) + sorted(raw.glob("*.mov")) + sorted(raw.glob("*.webm")):
        dst = OUT / (src.stem + ".mp4")
        normalise(src, dst, pingpong)
        print(f"{src.name} -> {dst.relative_to(ROOT)}  ({dst.stat().st_size // 1024} KB)")
        done += 1
    m = write_manifest()
    print(f"{done} clip(s) installed; manifest keys: states={list(m['states'])} gestures={list(m['gestures'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
