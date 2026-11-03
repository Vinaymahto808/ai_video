"""Render clips_neha with FACE motion: blinks, glances, brows, expressions and talking lips on top of the
learned head motion. CPU only; no model at render time.

Layers come from the puppet kit (tran_moment/path_b): expression morph sequences (expressions.json:
smile_soft / smile_warm / smile_big / surprised, eyes: eyes_closed / glance / wink), brow warps
(reactions.json: brows_up / brows_in) and the real-lip mouth shapes (mouths.json, Rhubarb map). They are
composited on prepared/face.jpg in the card's order (base -> face -> brows -> mouth -> eyes), then the frame
gets the head motion from synth_clips.py (learned gesture tracks + idle drift) and make_clips.py installs it.

    python tran_moment/synth_face_clips.py              # all clips -> prepared/clips_neha/ + manifest.json
    python tran_moment/synth_face_clips.py --only idle talking

The `talking` clip moves the lips to the Rhubarb cues of path_b/sample.wav (a 9 s Neha line); it is not
ping-ponged. Every other clip is a loop like before, now with blinks and an expression that fits the gesture.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent
PB = ROOT / "path_b"
PORTRAIT = ROOT / "prepared" / "face.jpg"
FPS = 24

_spec = importlib.util.spec_from_file_location("synth_clips", ROOT / "synth_clips.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)
W, H = sc.W, sc.H

# timing (seconds), matching the card's puppet; synth_talk_clips.py overrides these for its calmer delivery
BLINK_DOWN, BLINK_UP = 0.07, 0.09
EXPR_IN, EXPR_OUT = 0.26, 0.18
GLANCE_IN, GLANCE_OUT = 0.14, 0.16
BROW_RAMP = 0.15
MOUTH_FADE = 0.06
IDLE_SCALE = 1.0      # multiplier on the idle breathing / drift amplitude


# ---------------------------------------------------------------- assets
class Kit:
    def __init__(self) -> None:
        self.base = Image.open(PORTRAIT).convert("RGBA")
        ex = json.loads((PB / "expressions.json").read_text(encoding="utf-8"))
        self.expr = {}
        for name, layer in ex["layers"].items():
            frames = [Image.open(PB / "expressions" / f).convert("RGBA") for f in layer["frames"]]
            self.expr[name] = {"box": layer["box"], "frames": frames, "region": layer["region"]}
        re_ = json.loads((PB / "reactions.json").read_text(encoding="utf-8"))
        self.react = {n: Image.open(PB / "reactions" / l["file"]).convert("RGBA")
                      for n, l in re_["layers"].items() if l.get("kind") in ("brows",) and (PB / "reactions" / l["file"]).exists()}
        # gaze / lid warps (eyes-only RGBA) and whole-frame head poses (RGB) from the same kit
        self.gaze = {n: Image.open(PB / "reactions" / l["file"]).convert("RGBA")
                     for n, l in re_["layers"].items() if l.get("kind") == "eyes" and (PB / "reactions" / l["file"]).exists()}
        self.pose = {n: Image.open(PB / "reactions" / l["file"]).convert("RGBA")
                     for n, l in re_["layers"].items() if l.get("kind") == "pose" and (PB / "reactions" / l["file"]).exists()}
        mo = json.loads((PB / "mouths.json").read_text(encoding="utf-8"))
        self.mouth = {k: Image.open(PB / "mouths" / f).convert("RGBA") for k, f in mo["shapes"].items()}
        self.mouth["smile"] = Image.open(PB / "mouths" / "smile.png").convert("RGBA")
        self.rhubarb = {k: Path(f).stem for k, f in mo["rhubarb"].items()}   # Rhubarb letter -> shape file stem
        self.shape_by_stem = {Path(f).stem: k for k, f in mo["shapes"].items()}
        # the portrait's photo area (white polaroid border dropped), same as synth_clips.base_canvas
        diff = ImageChops.difference(self.base.convert("RGB"), Image.new("RGB", self.base.size, (255, 255, 255))).convert("L").point(lambda v: 255 if v > 18 else 0)
        self.bbox = diff.getbbox()
        photo = self.base.crop(self.bbox)
        bg = photo.getpixel((6, 6))[:3]
        self.bg = bg
        self.bgr = (bg[2], bg[1], bg[0])
        self.scale = (W * 1.06) / photo.width
        self.psize = (round(photo.width * self.scale), round(photo.height * self.scale))

    def compose(self, face: tuple[str, int] | None, brows: tuple[str, float] | None,
                mouth: tuple[str, str | None, float], eyes: tuple[str, int] | None,
                pose: tuple[str, float] | None = None, gaze: tuple[str, float] | None = None) -> Image.Image:
        """face/eyes = (layer, frame index 1..n) ; brows = (layer, alpha) ; mouth = (shape, prev shape, fade 0..1) ;
        pose = (whole-frame head pose, alpha) ; gaze = (eyes warp such as gaze_left, alpha)."""
        im = self.base.copy()
        if pose and pose[1] > 0.01:                       # head turned / lowered / raised: blend the pose warp in
            im = Image.blend(im, self.pose[pose[0]], min(1.0, pose[1]))
        if face:
            x = self.expr[face[0]]
            im.alpha_composite(x["frames"][face[1] - 1], (x["box"][0], x["box"][1]))
        if brows and brows[1] > 0.01:
            layer = self.react[brows[0]]
            if brows[1] < 0.99:
                a = layer.split()[3].point(lambda v: int(v * brows[1]))
                layer = layer.copy()
                layer.putalpha(a)
            im.alpha_composite(layer)
        shape, prev, fade = mouth
        if shape == "rest" and prev and prev != "rest" and fade < 0.99:   # closing: the last shape fades out
            pl = self.mouth[prev].copy()
            pl.putalpha(pl.split()[3].point(lambda v: int(v * (1 - fade))))
            im.alpha_composite(pl)
        if shape != "rest":
            cur = self.mouth[shape]
            if prev and prev != "rest" and fade < 0.99:
                pl = self.mouth[prev].copy()
                pl.putalpha(pl.split()[3].point(lambda v: int(v * (1 - fade))))
                im.alpha_composite(pl)
                cur = cur.copy()
                cur.putalpha(cur.split()[3].point(lambda v: int(v * fade)))
            im.alpha_composite(cur)
        if eyes:
            x = self.expr[eyes[0]]
            im.alpha_composite(x["frames"][eyes[1] - 1], (x["box"][0], x["box"][1]))
        elif gaze and gaze[1] > 0.01:
            layer = self.gaze[gaze[0]]
            if gaze[1] < 0.99:
                layer = layer.copy()
                layer.putalpha(layer.split()[3].point(lambda v: int(v * gaze[1])))
            im.alpha_composite(layer)
        return im

    def to_canvas(self, im: Image.Image) -> np.ndarray:
        photo = im.crop(self.bbox).convert("RGB").resize(self.psize, Image.BILINEAR)
        canvas = Image.new("RGB", (W, H), self.bg)
        canvas.paste(photo, ((W - photo.width) // 2, -int(photo.height * 0.02)))
        return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)


# ---------------------------------------------------------------- timelines
def seq_index(n: int, t: float, start: float, t_in: float, hold: float, t_out: float) -> int | None:
    """Frame index 1..n of a morph sequence shown at `start`: in over t_in, hold, out over t_out. None = not shown."""
    if t < start:
        return None
    u = t - start
    if u < t_in:
        return max(1, min(n, int(round(u / t_in * n))))
    if u < t_in + hold:
        return n
    if u < t_in + hold + t_out:
        return max(1, min(n, int(round((1 - (u - t_in - hold) / t_out) * n))))
    return None


def ramp(t: float, start: float, hold: float, r: float = BROW_RAMP) -> float:
    if t < start:
        return 0.0
    u = t - start
    if u < r:
        return u / r
    if u < r + hold:
        return 1.0
    if u < 2 * r + hold:
        return 1 - (u - r - hold) / r
    return 0.0


class Script:
    """A clip: head track + blink times + expression/glance/brow/mouth events."""

    def __init__(self, name: str, track: str | None, dur: float | None = None, blinks=(), expr=(), glances=(),
                 brows=(), mouth=None, rest_mouth: str = "rest", pingpong: bool = True, winks=(), nods=()):
        self.name, self.track, self.dur = name, track, dur
        self.blinks, self.expr, self.glances, self.brows, self.winks = blinks, expr, glances, brows, winks
        self.mouth, self.rest_mouth, self.pingpong = mouth, rest_mouth, pingpong
        self.nods = nods            # [(start_s, track_name[, amp[, slow]]), ...] head gestures inside the clip
        self.poses = ()             # [(pose_layer, start, hold)]: whole-frame head pose blended in/out (ramp = BROW_RAMP*2)
        self.gazes = ()             # [(gaze_layer, start, hold)]: eyes look left/right/up, ramped like brows

    def pose_at(self, t: float):
        best = None
        for name, start, hold in self.poses:
            a = ramp(t, start, hold, BROW_RAMP * 2)
            if a > 0 and (best is None or a > best[1]):
                best = (name, round(a, 2))
        return best

    def gaze_at(self, t: float):
        best = None
        for name, start, hold in self.gazes:
            a = ramp(t, start, hold, GLANCE_IN)
            if a > 0 and (best is None or a > best[1]):
                best = (name, round(a, 2))
        return best

    def face_at(self, kit: Kit, t: float):
        for name, start, hold in self.expr:            # later events win so an expression can follow another
            i = seq_index(len(kit.expr[name]["frames"]), t, start, EXPR_IN, hold, EXPR_OUT)
            if i:
                face = (name, i)
                return face
        return None

    def eyes_at(self, kit: Kit, t: float):
        for start in self.blinks:
            i = seq_index(len(kit.expr["eyes_closed"]["frames"]), t, start, BLINK_DOWN, 0.0, BLINK_UP)
            if i:
                return ("eyes_closed", i)
        for start in self.winks:
            i = seq_index(len(kit.expr["wink"]["frames"]), t, start, 0.12, 0.25, 0.14)
            if i:
                return ("wink", i)
        for start, hold in self.glances:
            i = seq_index(len(kit.expr["glance"]["frames"]), t, start, GLANCE_IN, hold, GLANCE_OUT)
            if i:
                return ("glance", i)
        return None

    def brows_at(self, t: float):
        best = None
        for name, start, hold in self.brows:
            a = ramp(t, start, hold)
            if a > 0 and (best is None or a > best[1]):
                best = (name, a)
        return best

    def mouth_at(self, kit: Kit, t: float):
        if not self.mouth:
            return (self.rest_mouth, None, 1.0)
        cues = self.mouth
        cur, prev, fade = self.rest_mouth, None, 1.0
        for k, c in enumerate(cues):
            if c["start"] <= t < c["end"]:
                cur = kit.shape_by_stem.get(kit.rhubarb.get(c["value"], "X"), "rest")
                if k > 0:
                    prev = kit.shape_by_stem.get(kit.rhubarb.get(cues[k - 1]["value"], "X"), "rest")
                    fade = min(1.0, (t - c["start"]) / MOUTH_FADE)
                break
        if prev == cur:
            prev, fade = None, 1.0
        return (cur, prev, round(fade, 2))


def scripts(tracks: dict, cues: list) -> list[Script]:
    g = lambda n: tracks[n]["dur"] / 1000.0  # noqa: E731
    R = sc.REST_S
    out = [
        Script("idle", None, dur=sc.IDLE_S, blinks=(1.4, 4.3), glances=((2.6, 0.45),), expr=(("smile_soft", 0.0, 5.3),)),
        Script("listening", "small_nod__v2", blinks=(0.9, 2.9), glances=((1.9, 0.5),), expr=(("smile_soft", 0.1, 3.0),)),
        Script("greeting", "greeting", blinks=(1.1, 2.9), expr=(("smile_warm", 0.3, 1.0), ("smile_big", 1.9, 1.2)), brows=(("brows_up", 0.3, 0.6),)),
        Script("small_nod", "small_nod", blinks=(R + 0.8,), expr=(("smile_soft", R, g("small_nod")),)),
        Script("nod", "nod", blinks=(R + 0.5,), expr=(("smile_warm", R - 0.1, g("nod") + 0.2),)),
        Script("slow_nod", "slow_nod", blinks=(R + 0.9,), brows=(("brows_in", R, g("slow_nod")),)),
        Script("tilt_head", "tilt_head", blinks=(R + 0.3,), brows=(("brows_in", R, g("tilt_head") + 0.3),)),
        Script("head_wobble", "head_wobble", blinks=(R + 1.0,), expr=(("smile_soft", R, g("head_wobble")),)),
        Script("look_up", "look_up", blinks=(0.2,), glances=((R, g("look_up") + 0.4),), brows=(("brows_up", R, g("look_up") + 0.4),)),
        Script("open_palm", "open_palm", blinks=(R + 1.6,), expr=(("smile_warm", R, g("open_palm")),)),
        Script("both_hands", "both_hands", blinks=(R + 0.9,), expr=(("smile_big", R, g("both_hands")),)),
        Script("lean_in", "lean_in", blinks=(R + 0.7,), expr=(("smile_soft", R, g("lean_in")),), brows=(("brows_up", R + 0.2, 0.6),)),
        Script("surprised", "tilt_head__v2", blinks=(R + g("tilt_head__v2") + 0.3,), expr=(("surprised", R, 1.1),), brows=(("brows_up", R, 1.3),)),
        Script("talking", "nod__v2", dur=cues[-1]["end"] + 0.4, blinks=(1.2, 4.0, 7.1), glances=((5.2, 0.4),),
               expr=(("smile_soft", 0.2, 8.6),), brows=(("brows_up", 0.4, 0.5), ("brows_up", 6.1, 0.4)), mouth=cues, pingpong=False),
    ]
    return out


# ---------------------------------------------------------------- render
def render(script: Script, kit: Kit, tracks: dict, idle: dict, out: Path) -> int:
    track = tracks[script.track] if script.track else None
    if script.dur:
        dur = script.dur
    else:
        dur = sc.REST_S * 2 + track["dur"] / 1000.0
    n = int(round(dur * FPS))
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    cache: dict = {}
    for i in range(n):
        t = i / FPS
        pitch, roll, dx, dy, zoom = (v * IDLE_SCALE for v in sc.idle_pose(t, idle))
        gestures = list(script.nods) if script.nods else (
            [(2.0, script.track), (6.0, script.track)] if script.name == "talking" else
            ([(sc.REST_S, script.track)] if track is not None else []))
        for g in gestures:
            start, tname = g[0], g[1]
            amp = g[2] if len(g) > 2 else 1.0           # (start, track, amp): amp < 1 = a calmer, smaller gesture
            slow = g[3] if len(g) > 3 else 1.0          # (start, track, amp, slow): slow > 1 stretches it in time
            tr = tracks[tname]
            p = (t - start) / (tr["dur"] / 1000.0 * slow)
            if 0.0 <= p <= 1.0:
                pitch += amp * sc.sample(tr.get("pitch"), p); roll += amp * sc.sample(tr.get("roll"), p)
                dx += amp * sc.sample(tr.get("dx"), p); dy += amp * sc.sample(tr.get("dy"), p); zoom += amp * sc.sample(tr.get("zoom"), p)
        key = (script.face_at(kit, t), script.brows_at(t), script.mouth_at(kit, t), script.eyes_at(kit, t),
               script.pose_at(t), script.gaze_at(t))
        if key not in cache:                                    # identical layer states share one composite
            if len(cache) > 64:
                cache.clear()
            cache[key] = kit.to_canvas(kit.compose(*key))
        proc.stdin.write(sc.warp(cache[key], kit.bgr, pitch, roll, dx, dy, zoom).tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        sys.exit(f"ffmpeg failed for {script.name}")
    return n


def main(argv: list[str]) -> int:
    only = set(argv[argv.index("--only") + 1:]) if "--only" in argv else None
    tracks, idle = sc.load_tracks()
    cues = json.loads((PB / "cache" / "sample.visemes.json").read_text(encoding="utf-8"))["mouthCues"]
    kit = Kit()
    spec = importlib.util.spec_from_file_location("make_clips", ROOT / "make_clips.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    mk.OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        for s in scripts(tracks, cues):
            if only and s.name not in only:
                continue
            raw = Path(td) / f"{s.name}.mp4"
            n = render(s, kit, tracks, idle, raw)
            dst = mk.OUT / f"{s.name}.mp4"
            mk.normalise(raw, dst, pingpong=s.pingpong)
            print(f"{s.name:12s} {n:4d} frames -> {dst.relative_to(ROOT)} ({dst.stat().st_size // 1024} KB)")
    m = mk.write_manifest()
    print("manifest:", json.dumps({"states": {k: len(v) for k, v in m["states"].items()},
                                   "gestures": {k: len(v) for k, v in m["gestures"].items()}}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
