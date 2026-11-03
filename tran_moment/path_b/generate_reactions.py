"""Reaction frames for the puppet from prepared/face.jpg (CPU, numpy + PIL + scipy; no model, no GPU).

The mouth shapes (generate_mouths.py) cover speech. This adds the *other* moments a face has in a
conversation, each as an aligned PNG layer over the same 640x852 canvas (transparent outside its region):

  eyes   eyes_closed, eyes_half           blink frames with the real upper lid stretched over the eye
         gaze_left, gaze_right, gaze_up   the iris/sclera shifted inside the opening (listening / thinking)
  brows  brows_up                         raised (question, interest, "acha!")
         brows_in                         inner ends down and in (concern, "hmm, dikkat hai")
  mouth  smile.png (into mouths/)         corners lifted and widened, lips closed: the warm resting face
  poses  head_tilt_l, head_tilt_r, head_down, head_up, lean_in   whole-frame body/head variants (for
         clip-style crossfades and review; the live card does these with CSS transforms)

Writes path_b/reactions/*.png, path_b/reactions.json and path_b/reactions_sheet.png (contact sheet).

    python tran_moment/path_b/generate_reactions.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import map_coordinates

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from generate_mouths import _alpha as mouth_alpha, _smooth  # noqa: E402

PREP = HERE.parent / "prepared"
OUT = HERE / "reactions"
MOUTHS = HERE / "mouths"


def warp(rgb: np.ndarray, dx: np.ndarray, dy: np.ndarray) -> np.ndarray:
    """Output pixel (y, x) samples source (y - dy, x - dx): positive dy moves content DOWN."""
    H, W = rgb.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    src = np.array([yy - dy, xx - dx], dtype=np.float32)
    out = np.empty_like(rgb, dtype=np.float32)
    for c in range(3):
        out[..., c] = map_coordinates(rgb[..., c].astype(np.float32), src, order=1, mode="nearest")
    return out


def box_alpha(shape: tuple[int, int], x0: float, x1: float, y0: float, y1: float, fx: float, fy: float) -> np.ndarray:
    H, W = shape
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    ax = _smooth((xx - x0) / fx) * _smooth((x1 - xx) / fx)
    ay = _smooth((yy - y0) / fy) * _smooth((y1 - yy) / fy)
    return np.clip(ax * ay, 0, 1).astype(np.float32)


def save_layer(rgb: np.ndarray, alpha01: np.ndarray, path: Path) -> None:
    a = (np.clip(alpha01, 0, 1) * 255).astype(np.uint8)
    im = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    im.putalpha(Image.fromarray(a, "L"))
    im.save(path)


# ---------------------------------------------------------------- eyes
def eye_geom(p: dict, side: str) -> dict:
    o, i, t, b = p[f"eye_{side}_outer"], p[f"eye_{side}_inner"], p[f"eye_{side}_top"], p[f"eye_{side}_bottom"]
    x0, x1 = min(o[0], i[0]), max(o[0], i[0])
    return {"cx": (x0 + x1) / 2, "w": x1 - x0, "top": float(t[1]), "bottom": float(b[1]), "x0": x0, "x1": x1}


def lid_field(shape, g: dict, amt: float) -> tuple[np.ndarray, np.ndarray]:
    """Upper lid skin stretched down over the opening: closes `amt` of the eye height, almond-shaped."""
    H, W = shape
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    eye_h = max(6.0, g["bottom"] - g["top"])
    k = eye_h * 1.3                       # how much lid skin above the eye is used
    cover = eye_h * amt                   # how much of the opening the lid covers
    y_start, y_end = g["top"] - k, g["top"] + cover
    stretch = 1 - k / (k + cover)         # the lid band [y_start, top] is stretched over [y_start, y_end]
    ell = np.clip(1 - ((xx - g["cx"]) / (g["w"] / 2 * 1.05)) ** 2, 0, 1) ** 0.5  # almond: nothing at the corners
    band = (yy >= y_start) & (yy <= y_end)
    dy = np.where(band, (yy - y_start) * stretch, 0.0)
    # below the lid edge the rest of the opening is compressed: displacement fades from `cover` to 0
    below = (yy > y_end) & (yy <= g["bottom"] + 4)
    rem = max(2.0, (g["bottom"] + 4) - y_end)
    dy = np.where(below, cover * stretch / max(stretch, 1e-6) * 0 + cover * np.clip(1 - (yy - y_end) / rem, 0, 1), dy)
    dy = (dy * ell).astype(np.float32)
    return np.zeros_like(dy), dy


def gaze_field(shape, g: dict, dx_px: float, dy_px: float) -> tuple[np.ndarray, np.ndarray]:
    H, W = shape
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    cy = (g["top"] + g["bottom"]) / 2
    ry = max(4.0, (g["bottom"] - g["top"]) / 2 * 0.9)
    rx = g["w"] / 2 * 0.92
    r = ((xx - g["cx"]) / rx) ** 2 + ((yy - cy) / ry) ** 2
    m = np.clip(1 - r, 0, 1) ** 0.6
    return (dx_px * m).astype(np.float32), (dy_px * m).astype(np.float32)


def eyes_alpha(shape, gl: dict, gr: dict) -> np.ndarray:
    pad = 14
    a = np.zeros(shape, np.float32)
    for g in (gl, gr):
        a = np.maximum(a, box_alpha(shape, g["x0"] - pad, g["x1"] + pad, g["top"] - 26, g["bottom"] + 10, 9, 7))
    return a


# ---------------------------------------------------------------- brows
def brow_field(shape, p: dict, mode: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    H, W = shape
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dx = np.zeros(shape, np.float32); dy = np.zeros(shape, np.float32); alpha = np.zeros(shape, np.float32)
    for side in ("l", "r"):
        g = eye_geom(p, side)
        by = float(p[f"brow_{side}"][1])
        x0, x1 = g["x0"] - 16, g["x1"] + 16
        band = box_alpha(shape, x0, x1, by - 34, by + 30, 10, 14)
        vert = np.exp(-((yy - by) / 16.0) ** 2)
        if mode == "up":
            dy -= 7.0 * band * vert            # whole brow up
        else:  # "in": inner end down and toward the nose
            inner_x = g["x1"] if side == "l" else g["x0"]
            near = np.clip(1 - np.abs(xx - inner_x) / (g["w"] * 0.8), 0, 1) ** 1.2
            dy += 5.0 * band * vert * near
            dx += (6.0 if side == "l" else -6.0) * band * vert * near
        alpha = np.maximum(alpha, box_alpha(shape, x0 - 6, x1 + 6, by - 42, by + 34, 12, 12))
    return dx, dy, alpha


# ---------------------------------------------------------------- smile (mouth layer)
def smile_field(shape, p: dict, amt: float) -> tuple[np.ndarray, np.ndarray]:
    H, W = shape
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    lx, ly = p["mouth_left"]; rx, ry = p["mouth_right"]
    mw = rx - lx
    dx = np.zeros(shape, np.float32); dy = np.zeros(shape, np.float32)
    for (cx, cy, sgn) in ((lx, ly, -1.0), (rx, ry, 1.0)):
        r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (mw * 0.42)
        w = np.clip(1 - r, 0, 1) ** 1.4
        dy -= 6.0 * amt * w                    # corner up
        dx += sgn * 4.0 * amt * w              # corner out
    # cheeks lift a touch with it
    for (cx, cy) in ((lx - mw * 0.25, ly - mw * 0.35), (rx + mw * 0.25, ry - mw * 0.35)):
        r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (mw * 0.5)
        dy -= 2.5 * amt * np.clip(1 - r, 0, 1) ** 1.5
    return dx, dy


# ---------------------------------------------------------------- poses (whole frame)
def pose(img: Image.Image, p: dict, kind: str, bg: tuple) -> Image.Image:
    W, H = img.size
    cx, chin = float(p["chin"][0]), float(p["chin"][1])
    if kind in ("head_tilt_l", "head_tilt_r"):
        ang = 5.0 if kind == "head_tilt_l" else -5.0
        return img.rotate(ang, resample=Image.BICUBIC, center=(cx, chin + 90), fillcolor=bg)
    if kind == "head_down":   # chin toward the chest: down a little, slightly compressed
        return img.transform((W, H), Image.AFFINE, (1, 0, 0, 0, 1 / 0.975, -(12 + chin * (1 - 1 / 0.975))), resample=Image.BICUBIC, fillcolor=bg)
    if kind == "head_up":
        return img.transform((W, H), Image.AFFINE, (1, 0, 0, 0, 1 / 1.02, (8 + chin * (1 - 1 / 1.02))), resample=Image.BICUBIC, fillcolor=bg)
    if kind == "lean_in":     # toward the camera: scale about the chest
        s = 1.07; ox, oy = W / 2, chin + 140
        return img.transform((W, H), Image.AFFINE, (1 / s, 0, ox - ox / s, 0, 1 / s, oy - oy / s), resample=Image.BICUBIC, fillcolor=bg)
    return img


def main() -> int:
    jpg, meta = PREP / "face.jpg", PREP / "face.json"
    if not jpg.exists() or not meta.exists():
        print("run tran_moment/prepare_portrait.py first", file=sys.stderr)
        return 1
    img = Image.open(jpg).convert("RGB")
    rgb = np.asarray(img).astype(np.float32)
    H, W = rgb.shape[:2]
    p = json.loads(meta.read_text(encoding="utf-8"))["points"]
    OUT.mkdir(exist_ok=True)
    edge = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = tuple(int(v) for v in np.median(edge, axis=0))
    layers: dict[str, dict] = {}
    gl, gr = eye_geom(p, "l"), eye_geom(p, "r")
    ea = eyes_alpha((H, W), gl, gr)
    ebox = [int(min(gl["x0"], gr["x0"]) - 20), int(min(gl["top"], gr["top"]) - 30), int(max(gl["x1"], gr["x1"]) + 20), int(max(gl["bottom"], gr["bottom"]) + 14)]

    # eyes: blink frames
    for name, amt in (("eyes_closed", 1.0), ("eyes_half", 0.55)):
        dx = np.zeros((H, W), np.float32); dy = np.zeros((H, W), np.float32)
        for g in (gl, gr):
            ddx, ddy = lid_field((H, W), g, amt); dx += ddx; dy += ddy
        save_layer(warp(rgb, dx, dy), ea, OUT / f"{name}.png")
        layers[name] = {"file": f"{name}.png", "kind": "eyes", "box": ebox, "closed": amt}
    # eyes: gaze
    for name, (gx, gy) in (("gaze_left", (-5.0, 0.0)), ("gaze_right", (5.0, 0.0)), ("gaze_up", (0.0, -3.5))):
        dx = np.zeros((H, W), np.float32); dy = np.zeros((H, W), np.float32)
        for g in (gl, gr):
            ddx, ddy = gaze_field((H, W), g, gx, gy); dx += ddx; dy += ddy
        save_layer(warp(rgb, dx, dy), ea, OUT / f"{name}.png")
        layers[name] = {"file": f"{name}.png", "kind": "eyes", "box": ebox, "gaze": [gx, gy]}
    # brows
    for name, mode in (("brows_up", "up"), ("brows_in", "in")):
        dx, dy, a = brow_field((H, W), p, mode)
        save_layer(warp(rgb, dx, dy), a, OUT / f"{name}.png")
        ys, xs = np.where(a > 0.02)
        layers[name] = {"file": f"{name}.png", "kind": "brows", "box": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]}
    # smile: a resting mouth shape, saved with the mouth set so the puppet can use it as "rest while warm"
    MOUTHS.mkdir(exist_ok=True)
    dx, dy = smile_field((H, W), p, 1.0)
    ma = mouth_alpha((H, W), p).astype(np.float32) / 255.0
    save_layer(warp(rgb, dx, dy), ma, MOUTHS / "smile.png")
    layers["smile"] = {"file": "../mouths/smile.png", "kind": "mouth", "note": "closed lips, corners lifted"}
    # poses (whole frame)
    for name in ("head_tilt_l", "head_tilt_r", "head_down", "head_up", "lean_in"):
        pose(img, p, name, bg).save(OUT / f"{name}.png")
        layers[name] = {"file": f"{name}.png", "kind": "pose"}

    (HERE / "reactions.json").write_text(json.dumps({"canvas": {"width": W, "height": H}, "layers": layers,
        "note": "Warps of prepared/face.jpg (no model). eyes/brows layers composite over base.png (or over a mouth shape) in the card's puppet; poses are whole frames."}, indent=1), encoding="utf-8")

    # contact sheet for review
    names = ["base"] + list(layers)
    thumbs = []
    for n in names:
        if n == "base":
            im = img.copy()
        elif layers[n]["kind"] == "pose":
            im = Image.open(OUT / layers[n]["file"]).convert("RGB")
        else:
            im = img.copy().convert("RGBA")
            lay = Image.open((MOUTHS / "smile.png") if n == "smile" else OUT / layers[n]["file"]).convert("RGBA")
            im.alpha_composite(lay); im = im.convert("RGB")
        crop = im.crop((120, 120, 520, 560)) if layers.get(n, {}).get("kind") in ("eyes", "brows", "mouth") else im
        crop = crop.resize((200, int(200 * crop.height / crop.width)))
        canvas = Image.new("RGB", (200, 290), (20, 20, 24)); canvas.paste(crop, (0, 0))
        ImageDraw.Draw(canvas).text((6, 272), n, fill=(255, 255, 255))
        thumbs.append(canvas)
    cols = 6
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 204, rows * 294), (10, 10, 12))
    for i, t in enumerate(thumbs):
        sheet.paste(t, ((i % cols) * 204, (i // cols) * 294))
    sheet.save(HERE / "reactions_sheet.png")
    print(f"wrote {len(layers)} reaction layers to {OUT}, reactions.json, mouths/smile.png and reactions_sheet.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
