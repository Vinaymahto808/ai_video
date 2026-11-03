"""Cut ~9 aligned, photo-accurate mouth-shape PNGs from prepared/face.jpg (Path B, CPU).

This is the realistic pass: instead of resizing the mouth crop into a blob, it
*wraps the real pixels* around the landmark mouth — the lower lip + chin drop
(so the photo's own teeth / inner-mouth band stretches open, exactly like the
live widget canvas), the lips spread or round, and the whole edit is feathered
back into the untouched skin.  No model, no GPU, numpy + PIL only.

A designer can still replace the files in mouths/ with hand-painted shapes;
keep the same filenames and canvas size (full portrait, transparent outside the
mouth region).

    uv run python tran_moment/path_b/generate_mouths.py
    # without uv:  python tran_moment/path_b/generate_mouths.py   (numpy + pillow)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
PREP = HERE.parent / "prepared"
OUT = HERE / "mouths"

# Each shape: how far the jaw drops (open, -0.1..1.0) and how the lips move
# sideways (width, -1 = round O/U .. +1 = wide E/I).  "open" is the same 0..1
# the live widget feeds its canvas jaw, so the two paths agree frame-to-frame.
SHAPES = {
    "rest":   {"open": 0.00, "width": 0.00, "file": "X.png"},
    "closed": {"open": -0.06, "width": 0.00, "file": "closed.png"},  # lips pressed (MBP)
    "A":      {"open": 0.90, "width": 0.05, "file": "A.png"},        # a / आ / schwa
    "E":      {"open": 0.50, "width": 0.85, "file": "E.png"},        # e / ai
    "I":      {"open": 0.38, "width": 0.92, "file": "I.png"},        # i / ee
    "O":      {"open": 0.66, "width": -0.85, "file": "O.png"},       # o
    "U":      {"open": 0.46, "width": -0.95, "file": "U.png"},       # u / oo
    "FV":     {"open": 0.18, "width": 0.22, "file": "FV.png"},       # f / v (teeth on lip)
    "L":      {"open": 0.55, "width": 0.28, "file": "L.png"},        # l / t / d
}

# Rhubarb A..H,X → our photo shapes (A is MBP = lips together, X is rest)
RHUBARB = {
    "A": "closed.png",
    "B": "E.png",
    "C": "A.png",
    "D": "I.png",
    "E": "E.png",
    "F": "U.png",
    "G": "U.png",
    "H": "L.png",
    "X": "X.png",
}


def _smooth(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _warp(rgb: np.ndarray, pts: dict, open_k: float, width_k: float) -> np.ndarray:
    """Return a copy of ``rgb`` with the mouth warped open/wide/round."""
    from scipy.ndimage import map_coordinates

    H, W = rgb.shape[:2]
    lx = float(pts["mouth_left"][0])
    rx = float(pts["mouth_right"][0])
    up = float(pts["lip_upper_inner"][1])
    lo = float(pts["lip_lower_inner"][1])
    lip_top = float(pts["lip_top"][1])
    lip_bottom = float(pts["lip_bottom"][1])
    nose = float(pts["nose_bottom"][1])
    chin = float(pts["chin"][1])
    cx = (lx + rx) / 2.0
    mw = max(8.0, rx - lx)
    lipY = (up + lo) / 2.0
    span = max(12.0, chin - lipY)

    # jaw drop: ~16% of the mouth width at full opening (a natural gape, not a hole)
    gap = open_k * mw * 0.16
    upper = open_k * mw * 0.05          # the upper lip lifts a little too

    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)

    # horizontal falloff: 1 at the mouth centre, 0 past the corners
    d = np.abs(xx - cx) / (mw / 2.0)
    fall = np.where(d <= 1.15, np.cos(np.clip(d / 1.15, 0, 1) * np.pi / 2.0), 0.0) ** 1.3
    fall = fall.astype(np.float32)

    # vertical weight below the lip line: full over the lower lip + chin, easing out on the neck
    ty = (yy - lipY) / span
    wl = np.where(ty < 0, 0.0, np.where(ty <= 0.85, 1.0, _smooth((1.15 - ty) / 0.30)))
    # vertical weight above the lip line: strongest at the lip, easing to the nose
    tu = (lipY - yy) / max(8.0, lipY - nose)
    wu = np.where(yy > lipY, 0.0, _smooth(tu) * 0.85)

    dy = (gap * fall * wl - upper * fall * wu).astype(np.float32)

    # sideways spread/round, damped away from the mouth
    sx = 1.0 + width_k * 0.12
    vert = np.exp(-((yy - lipY) / (0.95 * max(6.0, lip_bottom - lip_top))) ** 2)
    hw = (vert * fall).astype(np.float32)
    xs = (xx + ((xx - cx) / sx - (xx - cx)) * hw).astype(np.float32)

    src = np.array([yy - dy, xs], dtype=np.float32)
    out = np.empty_like(rgb, dtype=np.float32)
    for c in range(3):
        out[..., c] = map_coordinates(rgb[..., c].astype(np.float32), src, order=1, mode="nearest")

    # a soft shadow inside the opening reads as depth (never a flat dark hole)
    if open_k > 0.05:
        cav_top = up - upper * fall
        cav_bot = lo + gap * fall
        inside = (yy > cav_top) & (yy < cav_bot)
        cavw = np.clip(1.0 - (np.abs(xx - cx) / (mw * 0.5)) ** 2, 0.0, 1.0)
        shade = 1.0 - 0.20 * cavw * inside
        out *= shade[..., None]

    return out


def _alpha(rgb_wh: tuple[int, int], pts: dict) -> np.ndarray:
    """Feathered alpha that is opaque over the mouth/jaw edit and 0 outside it."""
    H, W = rgb_wh
    lx = float(pts["mouth_left"][0])
    rx = float(pts["mouth_right"][0])
    lip_top = float(pts["lip_top"][1])
    chin = float(pts["chin"][1])
    cx = (lx + rx) / 2.0
    mw = max(8.0, rx - lx)
    x0, x1 = cx - mw * 1.35, cx + mw * 1.35
    y0, y1 = lip_top - mw * 0.55, chin + mw * 0.30
    fx = mw * 0.35
    fy = mw * 0.25

    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    ax = _smooth((xx - x0) / fx) * _smooth((x1 - xx) / fx)
    ay = _smooth((yy - y0) / fy) * _smooth((y1 - yy) / fy)
    return np.clip(ax * ay * 255.0, 0, 255).astype(np.uint8)


def main() -> int:
    jpg = PREP / "face.jpg"
    meta = PREP / "face.json"
    if not jpg.exists() or not meta.exists():
        print("run check_and_prepare.py first", file=sys.stderr)
        return 1
    data = json.loads(meta.read_text(encoding="utf-8"))
    pts = data["points"]
    img = Image.open(jpg).convert("RGB")
    rgb = np.asarray(img, dtype=np.uint8)
    H, W = rgb.shape[:2]
    alpha = _alpha((H, W), pts)

    OUT.mkdir(parents=True, exist_ok=True)
    img.save(OUT / "base.png")

    for name, spec in SHAPES.items():
        warped = _warp(rgb, pts, spec["open"], spec["width"])
        rgba = np.dstack([np.clip(warped, 0, 255).astype(np.uint8), alpha])
        dest = OUT / spec["file"]
        Image.fromarray(rgba, "RGBA").save(dest)
        print("wrote", dest.name)

    index = {
        "canvas": {"width": W, "height": H},
        "mouth_alpha_box": [
            int((pts["mouth_left"][0] + pts["mouth_right"][0]) / 2 - (pts["mouth_right"][0] - pts["mouth_left"][0]) * 1.35),
            int(pts["lip_top"][1] - (pts["mouth_right"][0] - pts["mouth_left"][0]) * 0.55),
            int((pts["mouth_left"][0] + pts["mouth_right"][0]) / 2 + (pts["mouth_right"][0] - pts["mouth_left"][0]) * 1.35),
            int(pts["chin"][1] + (pts["mouth_right"][0] - pts["mouth_left"][0]) * 0.30),
        ],
        "shapes": {k: v["file"] for k, v in SHAPES.items()},
        "rhubarb": RHUBARB,
        "open": {k: v["open"] for k, v in SHAPES.items()},
        "width": {k: v["width"] for k, v in SHAPES.items()},
        "note": "Photo-accurate warp of prepared/face.jpg. Replace PNGs with designer shapes; keep filenames and canvas size.",
    }
    (HERE / "mouths.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print("wrote", HERE / "mouths.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
