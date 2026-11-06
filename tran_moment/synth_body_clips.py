"""Body-motion clips for clips_neha: shoulders, chest and posture move on their own, the head rides on top.

synth_face_clips.py moves the whole frame as one rigid piece (pivot in the chest), so the body sways with every
nod. Here each frame gets a smooth warp FIELD instead of one affine:

  * head region (above the neck band): the learned head motion (tracks + idle drift) about the neck base,
    carried by the body (lean, sway and the neck's own lift) so the head sits on the shoulders;
  * body region (below the collar): breathing (chest rises from the planted hips), shoulder lifts left/right
    (Gaussian blobs on each shoulder), shrug, lean toward camera (scale about a point below the frame), weight
    shift (sway about the seat), sitting up;
  * a smoothstep across the neck blends the two maps, so nothing tears.

The portrait (prepared/face.jpg) is a chest-up studio headshot: shoulders and blazer are in frame, HANDS ARE NOT.
Hand gestures need a generative video model (see CLIP_PROMPTS.md / make_clips.py for installing real renders);
`explain` is the body cue of someone talking with their hands just below the frame (alternating shoulders, a
small lean), not drawn hands.

Face layers (blinks, smiles, brows, glances) and head tracks come from the same kit as synth_face_clips.py.
Calm register throughout (user feedback 2026-11-03: slow, polite, small amplitudes).

    python tran_moment/synth_body_clips.py              # -> prepared/clips_neha/<name>.mp4 + manifest.json
    python tran_moment/synth_body_clips.py --only shrug lean_forward
    python tran_moment/synth_body_clips.py --preview    # also writes prepared/clips_neha/demo/body_preview.png
"""
from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("synth_face_clips", ROOT / "synth_face_clips.py")
sfc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sfc)
sc = sfc.sc
W, H, FPS = sc.W, sc.H, sc.FPS

CALM = {"EXPR_IN": 0.45, "EXPR_OUT": 0.40, "GLANCE_IN": 0.24, "GLANCE_OUT": 0.28, "BROW_RAMP": 0.30,
        "MOUTH_FADE": 0.09, "IDLE_SCALE": 0.6}
BREATH_PX = 2.0          # chest rise at the shoulders per normal breath (px on the 720x960 canvas)
BREATH_SHOULDER_PX = 1.0 # extra shoulder lift riding each breath
SHOULDER_SIGMA = (105.0, 85.0)   # Gaussian footprint of a shoulder lift (x, y) in px
R = sc.REST_S

# Landmarks on prepared/face.jpg (640x852); converted to canvas coordinates in Geometry.
FACE_PTS = {"chin": (325.8, 450.8), "neck": (325.0, 486.0), "shoulder_l": (120.0, 522.0), "shoulder_r": (530.0, 522.0)}


def smooth(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def env(t: float, start: float, t_in: float, hold: float, t_out: float) -> float:
    """0 -> 1 (ease in) -> hold -> 0 (ease out)."""
    if t < start:
        return 0.0
    if t < start + t_in:
        return smooth((t - start) / t_in)
    if t < start + t_in + hold:
        return 1.0
    return 1.0 - smooth((t - start - t_in - hold) / t_out)


class Geometry:
    """Canvas-space landmarks, the pixel grid and the static weight maps (computed once)."""

    def __init__(self, kit) -> None:
        x0, y0 = kit.bbox[0], kit.bbox[1]
        ox, oy = (W - kit.psize[0]) // 2, -int(kit.psize[1] * 0.02)      # same paste offset as Kit.to_canvas
        to_c = lambda p: ((p[0] - x0) * kit.scale + ox, (p[1] - y0) * kit.scale + oy)  # noqa: E731
        self.pt = {k: to_c(v) for k, v in FACE_PTS.items()}
        self.cx = self.pt["neck"][0]
        gx, gy = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
        self.gx, self.gy = gx, gy
        chin_y, neck_y = self.pt["chin"][1], self.pt["neck"][1]
        sh_y = (self.pt["shoulder_l"][1] + self.pt["shoulder_r"][1]) / 2
        # head weight: 1 above the chin, 0 below the collar
        u = np.clip((gy - (chin_y - 12)) / ((neck_y + 48) - (chin_y - 12)), 0, 1)
        self.w_head = (1 - u * u * (3 - 2 * u)).astype(np.float32)
        # torso lift profile: 0 at the seat (below the frame), 1 from the shoulders up
        seat = H + 40.0
        self.up = np.clip((seat - gy) / (seat - sh_y), 0, 1).astype(np.float32)
        sx, sy = SHOULDER_SIGMA
        self.g_l = np.exp(-(((gx - self.pt["shoulder_l"][0]) / sx) ** 2 + ((gy - self.pt["shoulder_l"][1]) / sy) ** 2) / 2).astype(np.float32)
        self.g_r = np.exp(-(((gx - self.pt["shoulder_r"][0]) / sx) ** 2 + ((gy - self.pt["shoulder_r"][1]) / sy) ** 2) / 2).astype(np.float32)
        self.seat = (self.cx, H + 300.0)       # sway pivot (she is seated; hips are below the frame)
        self.lean_c = (self.cx, H + 60.0)      # lean: grow from just below the frame = comes toward the camera
        n = self.pt["neck"]
        self.neck_ix = (int(round(n[1])), int(round(n[0])))


class Body:
    """Per-frame body state."""
    __slots__ = ("breath", "lift_l", "lift_r", "sway", "lean", "posture", "shift")

    def __init__(self) -> None:
        self.breath = self.lift_l = self.lift_r = self.sway = self.lean = self.posture = self.shift = 0.0


def body_at(events, t: float, breath_period: float) -> Body:
    b = Body()
    b.breath = math.sin(2 * math.pi * t / breath_period)
    for e in events:
        kind, start, t_in, hold, t_out, amp = e[:6]
        a = env(t, start, t_in, hold, t_out)
        if a <= 0:
            continue
        if kind == "shrug":
            b.lift_l += amp * a; b.lift_r += amp * a
        elif kind == "lift_l":
            b.lift_l += amp * a
        elif kind == "lift_r":
            b.lift_r += amp * a
        elif kind == "lean":
            b.lean += amp * a
        elif kind == "posture":
            b.posture += amp * a
        elif kind == "breath":                       # one deeper breath on top of the rhythm
            b.posture += amp * a; b.lift_l += 0.45 * amp * a; b.lift_r += 0.45 * amp * a
        elif kind == "sway":                         # (…, amp_deg, period): weight shift, one smooth oscillation
            period = e[6] if len(e) > 6 else 4.0
            b.sway += amp * a * math.sin(2 * math.pi * (t - start) / period)
        elif kind == "shift":                        # (…, amp_px, period): torso slides sideways, hips planted
            period = e[6] if len(e) > 6 else 4.0
            b.shift += amp * a * math.sin(2 * math.pi * (t - start) / period)
        elif kind == "tilt":                         # held lean to one side (deg)
            b.sway += amp * a
    return b


def body_field(geo: Geometry, b: Body) -> tuple[np.ndarray, np.ndarray]:
    """Forward displacement (dx, dy) of every body pixel."""
    lift = BREATH_PX * b.breath + b.posture
    dy = -lift * geo.up
    dx = b.shift * geo.up
    sh = BREATH_SHOULDER_PX * max(0.0, b.breath)
    dy -= (b.lift_l + sh) * geo.g_l + (b.lift_r + sh) * geo.g_r
    if b.lean:
        s = b.lean
        dx += s * (geo.gx - geo.lean_c[0])
        dy += s * (geo.gy - geo.lean_c[1])
    if b.sway:
        th = math.radians(b.sway)
        px, py = geo.gx - geo.seat[0], geo.gy - geo.seat[1]
        dx += (math.cos(th) - 1) * px - math.sin(th) * py
        dy += math.sin(th) * px + (math.cos(th) - 1) * py
    return dx, dy


def head_matrix(geo: Geometry, b: Body, local_neck: tuple[float, float], pitch, roll, dx, dy, zoom) -> np.ndarray:
    """3x3 forward map of the head: learned head pose about the neck, then carried by the body."""
    nx, ny = geo.pt["neck"]
    a = cv2.getRotationMatrix2D((nx, ny), -roll, 1.0 + zoom)
    a[0, 2] += dx / 100.0 * W
    a[1, 2] += dy / 100.0 * H + pitch * sc.PITCH_PX
    sy = 1.0 - min(abs(pitch), 8.0) * 0.004
    a[1, 1] *= sy
    a[1, 2] += ny * (1 - sy)
    m = np.vstack([a, [0, 0, 1]])
    if b.lean:                                       # lean scales about the same point as the torso
        cxl, cyl = geo.lean_c
        s = 1 + b.lean
        m = np.array([[s, 0, cxl * (1 - s)], [0, s, cyl * (1 - s)], [0, 0, 1]]) @ m
    if b.sway:
        rs = cv2.getRotationMatrix2D(geo.seat, -b.sway, 1.0)   # same sense as body_field's rotation
        m = np.vstack([rs, [0, 0, 1]]) @ m
    m = np.array([[1, 0, local_neck[0]], [0, 1, local_neck[1]], [0, 0, 1]]) @ m
    return m


def remap_frame(img: np.ndarray, geo: Geometry, b: Body, head_pose) -> np.ndarray:
    bdx, bdy = body_field(geo, b)
    # the neck point's displacement from breathing / posture / shoulders / shift only (lean and sway go in the matrix)
    iy, ix = geo.neck_ix
    lift = BREATH_PX * b.breath + b.posture
    sh = BREATH_SHOULDER_PX * max(0.0, b.breath)
    nl = (b.shift * float(geo.up[iy, ix]),
          -lift * float(geo.up[iy, ix]) - (b.lift_l + sh) * float(geo.g_l[iy, ix]) - (b.lift_r + sh) * float(geo.g_r[iy, ix]))
    m = head_matrix(geo, b, nl, *head_pose)
    inv = np.linalg.inv(m)
    hx = inv[0, 0] * geo.gx + inv[0, 1] * geo.gy + inv[0, 2]
    hy = inv[1, 0] * geo.gx + inv[1, 1] * geo.gy + inv[1, 2]
    bx, by = geo.gx - bdx, geo.gy - bdy             # small smooth field: p - D(p) inverts it to well under a pixel
    w = geo.w_head
    mx = (w * hx + (1 - w) * bx).astype(np.float32)
    my = (w * hy + (1 - w) * by).astype(np.float32)
    return cv2.remap(img, mx, my, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


# ---------------------------------------------------------------- clips
def S(name, dur, *, body=(), nods=(), blinks=(), expr=(), glances=(), brows=(), poses=(), gazes=()):
    s = sfc.Script(name, None, dur=dur, blinks=blinks, expr=expr, glances=glances, brows=brows, pingpong=True, nods=nods)
    s.poses, s.gazes, s.body = poses, gazes, body
    return s


def scripts() -> list:
    # body events: (kind, start, t_in, hold, t_out, amp[, period]); px on the 720x960 canvas, deg for sway/tilt,
    # fraction for lean. Every clip starts and ends at rest so the ping-pong loop and crossfades are seamless.
    return [
        S("body_idle", 6.0, body=(("sway", 0.0, 0.8, 4.4, 0.8, 0.35, 6.0),), blinks=(1.3, 4.2), glances=((2.8, 0.4),),
          expr=(("smile_soft", 0.0, 5.6),)),
        S("breathe", 5.0, body=(("breath", 0.6, 1.5, 0.4, 1.8, 3.2),), gazes=(("eyes_half", 1.9, 0.9),), blinks=(0.5, 4.2),
          expr=(("smile_soft", 0.2, 4.4),)),
        S("shrug", 3.8, body=(("shrug", 0.6, 0.45, 0.35, 0.65, 9.0),), nods=((0.55, "tilt_head", 0.35, 1.3),),
          brows=(("brows_up", 0.6, 0.8),), expr=(("smile_soft", 0.3, 2.9),), blinks=(3.2,)),
        S("lean_forward", 4.4, body=(("lean", 0.5, 0.9, 1.5, 0.9, 0.028),), nods=((1.5, "small_nod__v2", 0.5, 1.3),),
          expr=(("smile_warm", 0.5, 3.0),), brows=(("brows_up", 0.8, 0.5),), blinks=(3.7,)),
        S("sit_up", 3.8, body=(("posture", 0.5, 0.7, 1.4, 0.8, 6.0), ("shrug", 0.5, 0.7, 1.4, 0.8, 2.0)),
          brows=(("brows_up", 0.6, 0.6),), expr=(("smile_soft", 0.3, 3.0),), blinks=(0.4, 3.3)),
        S("sway", 5.2, body=(("sway", 0.3, 0.9, 3.0, 0.9, 0.7, 4.8), ("shift", 0.3, 0.9, 3.0, 0.9, 3.5, 4.8)),
          expr=(("smile_soft", 0.2, 4.6),), glances=((2.4, 0.4),), blinks=(1.1, 4.4)),
        S("shoulder_tilt", 3.8, body=(("lift_r", 0.5, 0.6, 1.2, 0.7, 6.0), ("lift_l", 0.5, 0.6, 1.2, 0.7, -2.0),
                                      ("tilt", 0.5, 0.6, 1.2, 0.7, -0.5)),
          nods=((0.5, "tilt_head", 0.6, 1.6),), expr=(("smile_warm", 0.3, 2.9),), blinks=(3.3,)),
        S("relax_shoulders", 4.6, body=(("shrug", 0.4, 0.8, 0.2, 0.6, 3.0), ("shrug", 1.9, 0.9, 1.0, 0.9, -4.5)),
          gazes=(("eyes_half", 1.9, 1.2),), expr=(("smile_soft", 0.3, 3.8),), blinks=(0.3, 4.0)),
        S("explain", 4.4, body=(("lean", 0.4, 0.6, 2.6, 0.7, 0.012), ("lift_l", 0.7, 0.35, 0.3, 0.45, 4.0),
                                ("lift_r", 1.8, 0.35, 0.3, 0.45, 4.0), ("lift_l", 2.8, 0.3, 0.2, 0.4, 3.0)),
          nods=((1.1, "nod", 0.45, 1.2), (2.7, "small_nod", 0.4, 1.2)), brows=(("brows_up", 1.0, 0.4),),
          expr=(("smile_warm", 0.3, 3.6),), blinks=(3.9,)),
    ]


def render(script, kit, tracks: dict, idle: dict, geo: Geometry, out: Path) -> int:
    n = int(round(script.dur * FPS))
    breath_period = idle.get("breath_period_s", 4.6)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    cache: dict = {}
    for i in range(n):
        t = i / FPS
        pitch, roll, dx, dy, zoom = (v * sfc.IDLE_SCALE for v in sc.idle_pose(t, idle))
        zoom *= 0.3                                   # the body breathes now; keep only a trace of head-zoom breathing
        for g in script.nods:
            start, tname = g[0], g[1]
            amp = g[2] if len(g) > 2 else 1.0
            slow = g[3] if len(g) > 3 else 1.0
            tr = tracks[tname]
            p = (t - start) / (tr["dur"] / 1000.0 * slow)
            if 0.0 <= p <= 1.0:
                pitch += amp * sc.sample(tr.get("pitch"), p); roll += amp * sc.sample(tr.get("roll"), p)
                dx += amp * sc.sample(tr.get("dx"), p); dy += amp * sc.sample(tr.get("dy"), p); zoom += amp * sc.sample(tr.get("zoom"), p)
        key = (script.face_at(kit, t), script.brows_at(t), script.mouth_at(kit, t), script.eyes_at(kit, t),
               script.pose_at(t), script.gaze_at(t))
        if key not in cache:
            if len(cache) > 64:
                cache.clear()
            cache[key] = kit.to_canvas(kit.compose(*key))
        b = body_at(script.body, t, breath_period)
        proc.stdin.write(remap_frame(cache[key], geo, b, (pitch, roll, dx, dy, zoom)).tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        sys.exit(f"ffmpeg failed for {script.name}")
    return n


def preview(kit, geo: Geometry, out: Path) -> None:
    """Rest vs. peak of each body move side by side, for a quick visual check."""
    base = kit.to_canvas(kit.compose(None, None, ("rest", None, 1.0), None))
    rows = []
    for s in scripts():
        peak = max((e[1] + e[2] for e in s.body), default=0.5)
        b = body_at(s.body, peak, 1e9)
        b.breath = 0.0
        img = remap_frame(base, geo, b, (0, 0, 0, 0, 0))
        pair = np.hstack([base, img])
        cv2.putText(pair, s.name, (16, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (40, 40, 40), 2, cv2.LINE_AA)
        rows.append(cv2.resize(pair, (W, H // 2), interpolation=cv2.INTER_AREA))
    grid = np.vstack([np.hstack(rows[i:i + 3]) if len(rows[i:i + 3]) == 3 else
                      np.hstack(rows[i:i + 3] + [np.zeros_like(rows[0])] * (3 - len(rows[i:i + 3]))) for i in range(0, len(rows), 3)])
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), grid)


def main(argv: list[str]) -> int:
    only = set(a for a in argv[argv.index("--only") + 1:] if not a.startswith("--")) if "--only" in argv else None
    for k, v in CALM.items():
        setattr(sfc, k, v)
    tracks, idle = sc.load_tracks()
    kit = sfc.Kit()
    geo = Geometry(kit)
    spec = importlib.util.spec_from_file_location("make_clips", ROOT / "make_clips.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    mk.OUT.mkdir(parents=True, exist_ok=True)
    if "--preview" in argv:
        p = mk.OUT / "demo" / "body_preview.png"
        preview(kit, geo, p)
        print("preview:", p.relative_to(ROOT))
    with tempfile.TemporaryDirectory() as td:
        for s in scripts():
            if only and s.name not in only:
                continue
            missing = [g[1] for g in s.nods if g[1] not in tracks]
            if missing:
                print(f"skip {s.name}: tracks missing {missing}")
                continue
            raw = Path(td) / f"{s.name}.mp4"
            n = render(s, kit, tracks, idle, geo, raw)
            dst = mk.OUT / f"{s.name}.mp4"
            mk.normalise(raw, dst, pingpong=True)
            print(f"{s.name:16s} {n:4d} frames -> {dst.relative_to(ROOT)} ({dst.stat().st_size // 1024} KB)")
    m = mk.write_manifest()
    print("manifest:", json.dumps({"states": {k: len(v) for k, v in m["states"].items()},
                                   "gestures": {k: len(v) for k, v in m["gestures"].items()}}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
