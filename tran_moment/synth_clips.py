"""Render the prepared/clips_neha reaction set from prepared/face.jpg using Neha's learned gesture tracks.

No GPU, no API: each clip is the portrait moved by the keyframe tracks in avatar_widget/src/motion-data.js
(pitch / roll / dx / dy / zoom fitted from footage by tran_moment/motion/fit_gestures.py) on top of idle
breathing and micro-drift, rendered with OpenCV affine warps, then normalised + ping-ponged by make_clips.py.
The lips never move (the voice is TTS); a blink is not rendered (no eye layer here).

    python tran_moment/synth_clips.py            # -> prepared/clips_neha/*.mp4 + manifest.json
    python tran_moment/synth_clips.py --only idle nod

Output names match CLIP_PROMPTS.md: idle, small_nod, nod, slow_nod, tilt_head, head_wobble, look_up,
open_palm, both_hands, lean_in, greeting, listening; learned variants become extra takes (nod_2, nod_3 ...).
"""
from __future__ import annotations

import importlib.util
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent
PORTRAIT = ROOT / "prepared" / "face.jpg"
MOTION = ROOT.parent / "avatar_widget" / "src" / "motion-data.js"
W, H, FPS = 720, 960, 24
REST_S = 0.55            # still frames before / after a gesture so the loop breathes
IDLE_S = 6.0             # idle loop (doubled by the ping-pong)
PITCH_PX = 2.4           # px of vertical head travel per degree of pitch
PIVOT = (0.5, 0.74)      # rotation / scale pivot (base of the neck) as a fraction of the canvas

# which learned track feeds which clip; variants (name__vN) become extra takes of the same clip
CLIPS = {
    "idle": None, "small_nod": "small_nod", "nod": "nod", "slow_nod": "slow_nod", "tilt_head": "tilt_head",
    "head_wobble": "head_wobble", "look_up": "look_up", "open_palm": "open_palm", "both_hands": "both_hands",
    "lean_in": "lean_in", "greeting": "greeting", "listening": "small_nod__v2",
}


def load_tracks() -> tuple[dict, dict]:
    s = MOTION.read_text(encoding="utf-8")
    g = re.search(r"LEARNED_GESTURES = Object\.freeze\(\{(.*?)\n\}\);", s, re.S).group(1)
    tracks = {m.group(1): json.loads(m.group(2)) for m in re.finditer(r"^\s*(\w+): (\{.*\}),?$", g, re.M)}
    i = re.search(r"IDLE_STATS = Object\.freeze\((\{.*?\})\);", s, re.S).group(1)
    return tracks, json.loads(i)


def sample(keys: list | None, p: float) -> float:
    """Keyframes [[progress, value], ...] with smoothstep easing, like avatar.js sampleGesture."""
    if not keys:
        return 0.0
    if p <= keys[0][0]:
        return keys[0][1]
    for (p0, v0), (p1, v1) in zip(keys, keys[1:]):
        if p <= p1:
            u = 0.0 if p1 == p0 else (p - p0) / (p1 - p0)
            u = u * u * (3 - 2 * u)
            return v0 + (v1 - v0) * u
    return keys[-1][1]


def base_canvas() -> tuple[np.ndarray, tuple[int, int, int]]:
    im = Image.open(PORTRAIT).convert("RGB")
    diff = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).convert("L").point(lambda v: 255 if v > 18 else 0)
    photo = im.crop(diff.getbbox())                       # drop the white polaroid border
    bg = photo.getpixel((6, 6))
    sc = (W * 1.06) / photo.width                         # 6 % overscan so drifts never expose the edge
    p2 = photo.resize((round(photo.width * sc), round(photo.height * sc)), Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), bg)
    canvas.paste(p2, ((W - p2.width) // 2, -int(p2.height * 0.02)))
    return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR), (bg[2], bg[1], bg[0])


def warp(base: np.ndarray, bgr: tuple, pitch: float, roll: float, dx: float, dy: float, zoom: float) -> np.ndarray:
    cx, cy = W * PIVOT[0], H * PIVOT[1]
    s = 1.0 + zoom
    m = cv2.getRotationMatrix2D((cx, cy), -roll, s)       # roll: positive = tilt to her right on screen
    m[0, 2] += dx / 100.0 * W
    m[1, 2] += dy / 100.0 * H + pitch * PITCH_PX           # pitch: chin down = head travels down the frame
    sy = 1.0 - min(abs(pitch), 8.0) * 0.004                # a touch of foreshortening when the head dips
    m[1, 1] *= sy
    m[1, 2] += cy * (1 - sy)
    return cv2.warpAffine(base, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=bgr)


def idle_pose(t: float, idle: dict) -> tuple[float, float, float, float, float]:
    ms = idle.get("micro_std", {})
    ap = min(max(ms.get("pitch", 0.7) * 0.5, 0.3), 0.9)
    ar = min(max(ms.get("roll", 1.0) * 0.5, 0.3), 0.8)
    ad = min(max(ms.get("dx", 0.5) * 0.5, 0.15), 0.4)
    breath = idle.get("breath_period_s", 4.6)
    zoom = 0.006 * math.sin(2 * math.pi * t / breath)
    pitch = ap * math.sin(2 * math.pi * t / 7.3) + 0.3 * ap * math.sin(2 * math.pi * t / 2.9 + 1.1)
    roll = ar * math.sin(2 * math.pi * t / 9.1 + 0.6)
    dx = ad * math.sin(2 * math.pi * t / 11.0 + 2.0)
    dy = 0.15 * math.sin(2 * math.pi * t / breath + 0.5)
    return pitch, roll, dx, dy, zoom


def frames_for(track: dict | None, idle: dict):
    dur = IDLE_S if track is None else REST_S * 2 + track["dur"] / 1000.0
    n = int(round(dur * FPS))
    for i in range(n):
        t = i / FPS
        pitch, roll, dx, dy, zoom = idle_pose(t, idle)
        if track is not None:
            p = (t - REST_S) / (track["dur"] / 1000.0)
            if 0.0 <= p <= 1.0:
                pitch += sample(track.get("pitch"), p)
                roll += sample(track.get("roll"), p)
                dx += sample(track.get("dx"), p)
                dy += sample(track.get("dy"), p)
                zoom += sample(track.get("zoom"), p)
        yield pitch, roll, dx, dy, zoom


def encode(frames, out: Path, base: np.ndarray, bgr: tuple) -> int:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = 0
    for pose in frames:
        proc.stdin.write(warp(base, bgr, *pose).tobytes())
        n += 1
    proc.stdin.close()
    if proc.wait() != 0:
        sys.exit(f"ffmpeg failed for {out.name}")
    return n


def main(argv: list[str]) -> int:
    only = set(argv[argv.index("--only") + 1:]) if "--only" in argv else None
    tracks, idle = load_tracks()
    base, bgr = base_canvas()
    spec = importlib.util.spec_from_file_location("make_clips", ROOT / "make_clips.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    mk.OUT.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[str, dict | None]] = []
    for name, src in CLIPS.items():
        if only and name not in only:
            continue
        if src is None:
            jobs.append((name, None))
            continue
        if src not in tracks:
            print(f"skip {name}: track {src} missing")
            continue
        jobs.append((name, tracks[src]))
        if "__v" not in src:                               # extra takes from the mined variants
            for k, tr in tracks.items():
                if k.startswith(src + "__v"):
                    jobs.append((f"{name}_{k.rsplit('__v', 1)[1]}", tr))
    with tempfile.TemporaryDirectory() as td:
        for name, track in jobs:
            raw = Path(td) / f"{name}.mp4"
            n = encode(frames_for(track, idle), raw, base, bgr)
            dst = mk.OUT / f"{name}.mp4"
            mk.normalise(raw, dst, pingpong=True)
            print(f"{name:14s} {n:4d} frames -> {dst.relative_to(ROOT)} ({dst.stat().st_size // 1024} KB)")
    m = mk.write_manifest()
    print("manifest:", json.dumps({"states": m["states"], "gestures": {k: len(v) for k, v in m["gestures"].items()}}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
