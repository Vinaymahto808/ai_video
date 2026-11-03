"""Smooth expression animation: morph frames between the base face and every expression (CPU, OpenCV).

A cross-fade between two stills ghosts and "flickers" because the geometry differs. A morph moves the
geometry too: for every in-between step t the 478 face landmarks are interpolated, both images are
piecewise-affine warped to that shape (Delaunay triangles) and then blended. Played as a short sequence
it reads as a muscle moving, not a slide show.

For each expression in expressions.json (built by expressions_from_grid.py) this writes
    path_b/expressions/<name>/f01.webp ... fNN.webp      cropped to the layer's region box
and rewrites expressions.json with  {region, box:[x,y,w,h], frames:[...]}  per expression.
The number of frames is automatic: one frame per ~3 px of the largest landmark move (4..14).

    python tran_moment/path_b/morph_frames.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy.spatial import Delaunay

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
from expressions_from_grid import CELLS, EYES_EXPR, FACE_EXPR, align, colour_match, face_masks  # noqa: E402
from prepare_portrait import MODEL, landmarks  # noqa: E402

PREP = HERE.parent / "prepared"
EXPR = HERE / "expressions"
MIN_FRAMES, MAX_FRAMES, PX_PER_FRAME = 14, 40, 0.6   # 2026-11-03: ~2.5x denser for smoother reactions
# reaction warps (generate_reactions.py) that become morph sequences too, with their own alpha as the region
REACTION_SEQ = {"brows_up": "brows", "brows_in": "brows", "gaze_up": "eyes", "gaze_left": "eyes", "gaze_right": "eyes"}


def eased(k: int, n: int) -> float:
    """Frame k of n at ease-in-out spacing: the card steps frames at a constant rate, so the motion itself
    starts gently, moves through the middle and settles softly instead of snapping."""
    u = k / n
    return u * u * (3 - 2 * u)
WEBP_Q = 86


def dense_landmarks(img: np.ndarray) -> np.ndarray:
    """All 478 FaceMesh points (x, y) in pixels."""
    import mediapipe as mp
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core.base_options import BaseOptions
    opts = vision.FaceLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(MODEL)), running_mode=vision.RunningMode.IMAGE,
                                        num_faces=1, min_face_detection_confidence=0.3, min_face_presence_confidence=0.3)
    with vision.FaceLandmarker.create_from_options(opts) as lm:
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(img[..., :3].astype(np.uint8))))
    if not res.face_landmarks:
        raise RuntimeError("no face")
    h, w = img.shape[:2]
    return np.array([[p.x * w, p.y * h] for p in res.face_landmarks[0]], np.float32)


def with_border(pts: np.ndarray, w: int, h: int) -> np.ndarray:
    border = np.array([[0, 0], [w / 2, 0], [w - 1, 0], [0, h / 2], [w - 1, h / 2], [0, h - 1], [w / 2, h - 1], [w - 1, h - 1],
                       [w * 0.25, 0], [w * 0.75, 0], [w * 0.25, h - 1], [w * 0.75, h - 1]], np.float32)
    return np.vstack([pts, border])


def warp_to(img: np.ndarray, src: np.ndarray, dst: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """Piecewise-affine warp of `img` from landmark set `src` to `dst` (same triangle list)."""
    h, w = img.shape[:2]
    out = img.copy()
    for tri in tris:
        s = src[tri]; d = dst[tri]
        r1 = cv2.boundingRect(np.float32([s])); r2 = cv2.boundingRect(np.float32([d]))
        if r2[2] <= 0 or r2[3] <= 0 or r1[2] <= 0 or r1[3] <= 0:
            continue
        s_off = np.float32([[p[0] - r1[0], p[1] - r1[1]] for p in s]); d_off = np.float32([[p[0] - r2[0], p[1] - r2[1]] for p in d])
        patch = img[r1[1]:r1[1] + r1[3], r1[0]:r1[0] + r1[2]]
        if patch.size == 0:
            continue
        M = cv2.getAffineTransform(s_off, d_off)
        warped = cv2.warpAffine(patch, M, (r2[2], r2[3]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
        mask = np.zeros((r2[3], r2[2]), np.float32)
        cv2.fillConvexPoly(mask, np.int32(d_off), 1.0, 16, 0)
        y0, y1 = max(0, r2[1]), min(h, r2[1] + r2[3]); x0, x1 = max(0, r2[0]), min(w, r2[0] + r2[2])
        if y1 <= y0 or x1 <= x0:
            continue
        m = mask[y0 - r2[1]:y1 - r2[1], x0 - r2[0]:x1 - r2[0]][..., None]
        out[y0:y1, x0:x1] = out[y0:y1, x0:x1] * (1 - m) + warped[y0 - r2[1]:y1 - r2[1], x0 - r2[0]:x1 - r2[0]] * m
    return out


def main() -> int:
    meta_path = HERE / "expressions.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    base_img = Image.open(PREP / "face.jpg").convert("RGB")
    base = np.asarray(base_img).astype(np.float32)
    H, W = base.shape[:2]
    p_base = landmarks(base_img)
    face_m, eyes_m, core_m = face_masks(p_base, (H, W))
    edge = np.concatenate([base[0], base[-1], base[:, 0], base[:, -1]])
    bg = tuple(int(v) for v in np.median(edge, axis=0))
    LB = with_border(dense_landmarks(base), W, H)
    tris = Delaunay(LB).simplices

    # the aligned, colour-matched expression renders (same construction as expressions_from_grid.py)
    grid_path = Path(meta.get("grid") or (HERE.parent.parent / "voice_clone" / "src" / "expressions_grid.png"))
    grid = Image.open(grid_path).convert("RGB")
    cols, rows = 6, 2
    cw, ch = grid.width / cols, grid.height / rows
    out_meta: dict[str, dict] = {}
    for i, name in CELLS.items():
        if name not in FACE_EXPR + EYES_EXPR:
            continue
        r, c = divmod(i, cols)
        cell = grid.crop((round(c * cw), round(r * ch), round((c + 1) * cw), round((r + 1) * ch)))
        try:
            pc = landmarks(cell)
        except SystemExit:
            print(f"  ! {name}: no face; skipped"); continue
        target = colour_match(np.asarray(align(cell, pc, p_base, bg)).astype(np.float32), base, core_m)
        try:
            LT = with_border(dense_landmarks(target), W, H)
        except RuntimeError:
            print(f"  ! {name}: no dense landmarks; skipped"); continue
        region = "eyes" if name in EYES_EXPR else "face"
        mask = (eyes_m if region == "eyes" else face_m)[..., None]
        # frames: one per ~3 px of the biggest landmark move inside the region
        inside = mask[..., 0][np.clip(LB[:, 1].astype(int), 0, H - 1), np.clip(LB[:, 0].astype(int), 0, W - 1)] > 0.3
        disp = np.linalg.norm((LT - LB)[inside], axis=1).max() if inside.any() else 10.0
        n = int(np.clip(round(disp / PX_PER_FRAME), MIN_FRAMES, MAX_FRAMES))
        ys, xs = np.where(mask[..., 0] > 0.01)
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        d = EXPR / name; d.mkdir(parents=True, exist_ok=True)
        frames = []
        for k in range(1, n + 1):
            t = eased(k, n)
            Lt = (1 - t) * LB + t * LT
            wb = warp_to(base, LB, Lt, tris)
            wt = warp_to(target, LT, Lt, tris)
            morph = (1 - t) * wb + t * wt
            layer = base * (1 - mask) + morph * mask           # outside the region: untouched base
            rgba = np.dstack([np.clip(layer, 0, 255).astype(np.uint8), (mask[..., 0] * 255).astype(np.uint8)])
            crop = Image.fromarray(rgba[y0:y1, x0:x1], "RGBA")
            fname = f"f{k:02d}.webp"
            crop.save(d / fname, "WEBP", quality=WEBP_Q, method=4)
            frames.append(f"{name}/{fname}")
        out_meta[name] = {"region": region, "box": [x0, y0, x1 - x0, y1 - y0], "frames": frames, "file": f"{name}.png", "max_move_px": round(float(disp), 1)}
        print(f"  {name:12s} {region:4s} {n:2d} frames  (max move {disp:.0f}px, box {x1 - x0}x{y1 - y0})")
    # brows / gaze: the reaction warps already exist as full-canvas layers; morph base -> layer inside the
    # layer's own alpha so a raised brow or a glance is motion, not a 180 ms cross-fade
    react_dir = HERE / "reactions"
    for name, region in REACTION_SEQ.items():
        lp = react_dir / f"{name}.png"
        if not lp.exists():
            print(f"  ! {name}: no reaction layer; skipped"); continue
        layer_rgba = np.asarray(Image.open(lp).convert("RGBA")).astype(np.float32)
        a = layer_rgba[..., 3:4] / 255.0
        comp = base * (1 - a) + layer_rgba[..., :3] * a
        try:
            LT = with_border(dense_landmarks(comp), W, H)
        except RuntimeError:
            print(f"  ! {name}: no dense landmarks; skipped"); continue
        mask = a
        inside = mask[..., 0][np.clip(LB[:, 1].astype(int), 0, H - 1), np.clip(LB[:, 0].astype(int), 0, W - 1)] > 0.3
        disp = np.linalg.norm((LT - LB)[inside], axis=1).max() if inside.any() else 6.0
        n = int(np.clip(round(disp / PX_PER_FRAME), MIN_FRAMES, MAX_FRAMES))
        ys, xs = np.where(mask[..., 0] > 0.01)
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        d = EXPR / name; d.mkdir(parents=True, exist_ok=True)
        frames = []
        for k in range(1, n + 1):
            t = eased(k, n)
            Lt = (1 - t) * LB + t * LT
            wb = warp_to(base, LB, Lt, tris)
            wt = warp_to(comp, LT, Lt, tris)
            morph = (1 - t) * wb + t * wt
            layer = base * (1 - mask) + morph * mask
            rgba = np.dstack([np.clip(layer, 0, 255).astype(np.uint8), (mask[..., 0] * 255).astype(np.uint8)])
            fname = f"f{k:02d}.webp"
            Image.fromarray(rgba[y0:y1, x0:x1], "RGBA").save(d / fname, "WEBP", quality=WEBP_Q, method=4)
            frames.append(f"{name}/{fname}")
        out_meta[name] = {"region": region, "box": [x0, y0, x1 - x0, y1 - y0], "frames": frames, "file": f"../reactions/{name}.png", "max_move_px": round(float(disp), 1)}
        print(f"  {name:12s} {region:5s} {n:2d} frames  (max move {disp:.0f}px, box {x1 - x0}x{y1 - y0})")
    meta["layers"] = out_meta
    meta["frames_note"] = "frames[k] = morph base->expression at t=(k+1)/n, cropped to box; play forward to show, backward to relax"
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    total = sum(f.stat().st_size for d in EXPR.iterdir() if d.is_dir() for f in d.iterdir()) / 1e6
    print(f"wrote morph frames for {len(out_meta)} expressions ({total:.1f} MB webp); expressions.json updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
