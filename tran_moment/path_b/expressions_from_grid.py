"""Build the puppet from a 12-expression headshot grid (one person, same framing, 6 x 2 cells).

Instead of warping a single photo, every "moment" comes from a real rendered expression:

  cell  0 neutral      -> the base portrait (prepared/face.jpg + face.json, same framing as before)
        1 smile_soft   3 mouth_ah   4 talk_e   5 surprised   6 mouth_o   7 talk_i
        2 smile_big    8 glance     9 smile_warm   10 eyes_closed   11 wink

  mouths/     speech shapes cut from the talking cells (mouth region, aligned on the mouth corners):
              A <- mouth_ah, E <- talk_e, I <- talk_i, O,U <- mouth_o, L <- talk_e, FV <- talk_i,
              closed, X (rest) <- neutral, smile <- smile_warm
  expressions/ face-region layers (aligned on eyes + mouth, colour-matched to the base, feathered):
              smile_soft, smile_big, surprised, smile_warm (whole face);
              glance, eyes_closed, wink (eyes region only, so the mouth does not jump)

Every cell is aligned to the base with a similarity transform fitted on six landmarks (eye corners and
mouth corners), then its skin tones are matched to the base inside the face ellipse, so a layer never
shows as a differently-coloured patch. CPU only (MediaPipe landmarks, numpy, PIL).

    python tran_moment/path_b/expressions_from_grid.py --grid voice_clone/src/expressions_grid.png
    python tran_moment/path_b/generate_reactions.py          # brows_up/in + eyes_half on the new base
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
from generate_mouths import SHAPES, RHUBARB, _alpha as mouth_alpha, _smooth  # noqa: E402
from prepare_portrait import OUT_H, OUT_W, frame, landmarks  # noqa: E402

PREP = HERE.parent / "prepared"
MOUTHS = HERE / "mouths"
EXPR = HERE / "expressions"

CELLS = {0: "neutral", 1: "smile_soft", 2: "smile_big", 3: "mouth_ah", 4: "talk_e", 5: "surprised",
         6: "mouth_o", 7: "talk_i", 8: "glance", 9: "smile_warm", 10: "eyes_closed", 11: "wink"}
VISEMES = {"A": "mouth_ah", "E": "talk_e", "I": "talk_i", "O": "mouth_o", "U": "mouth_o", "L": "talk_e", "FV": "talk_i",
           "closed": "neutral", "rest": "neutral", "smile": "smile_warm"}
FACE_EXPR = ["smile_soft", "smile_big", "surprised", "smile_warm"]
EYES_EXPR = ["glance", "eyes_closed", "wink"]
ALIGN_KEYS = ["eye_l_outer", "eye_l_inner", "eye_r_inner", "eye_r_outer", "mouth_left", "mouth_right"]


def similarity(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """Umeyama: s, R, t with dst ~= s R src + t."""
    ms, md = src.mean(0), dst.mean(0)
    a, b = src - ms, dst - md
    cov = b.T @ a / len(src)
    U, S, Vt = np.linalg.svd(cov)
    d = np.sign(np.linalg.det(U @ Vt))
    D = np.diag([1, d])
    R = U @ D @ Vt
    s = float(np.trace(np.diag(S) @ D) / (a ** 2).sum() * len(src))
    t = md - s * R @ ms
    return s, R, t


def align(cell: Image.Image, p_cell: dict, p_base: dict, bg: tuple) -> Image.Image:
    src = np.array([p_cell[k] for k in ALIGN_KEYS], float)
    dst = np.array([p_base[k] for k in ALIGN_KEYS], float)
    s, R, t = similarity(src, dst)
    M = s * R
    Mi = np.linalg.inv(M)
    ti = -Mi @ t
    coeffs = (Mi[0, 0], Mi[0, 1], ti[0], Mi[1, 0], Mi[1, 1], ti[1])  # x_src = a x + b y + c
    return cell.transform((OUT_W, OUT_H), Image.AFFINE, coeffs, resample=Image.BICUBIC, fillcolor=bg)


def ellipse_alpha(shape, cx, cy, rx, ry, feather=0.14) -> np.ndarray:
    H, W = shape
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2)
    return np.clip((1 - r) / feather + 1, 0, 1).astype(np.float32) ** 1.2 if feather else (r <= 1).astype(np.float32)


def face_masks(p: dict, shape) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ex = (p["eye_l_outer"][0] + p["eye_r_outer"][0]) / 2
    ey = (p["eye_l_outer"][1] + p["eye_r_outer"][1]) / 2
    dist = abs(p["eye_r_outer"][0] - p["eye_l_outer"][0])
    chin, brow = p["chin"][1], (p["brow_l"][1] + p["brow_r"][1]) / 2
    face = ellipse_alpha(shape, ex, (ey + p["lip_bottom"][1]) / 2, dist * 0.82, (chin - brow) * 0.66)
    eyes = ellipse_alpha(shape, ex, ey + 2, dist * 0.78, dist * 0.26)
    core = ellipse_alpha(shape, ex, (ey + p["lip_bottom"][1]) / 2, dist * 0.55, (chin - brow) * 0.45, feather=0)
    return face, eyes, core


def colour_match(img: np.ndarray, ref: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Per-channel gain/offset so the cell's face statistics equal the base's inside `mask`."""
    out = img.copy()
    m = mask > 0.5
    for c in range(3):
        a, b = img[..., c][m], ref[..., c][m]
        sa, sb = a.std() + 1e-6, b.std() + 1e-6
        out[..., c] = (img[..., c] - a.mean()) * (sb / sa) + b.mean()
    return np.clip(out, 0, 255)


def save_layer(rgb: np.ndarray, alpha01: np.ndarray, path: Path) -> None:
    im = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    im.putalpha(Image.fromarray((np.clip(alpha01, 0, 1) * 255).astype(np.uint8), "L"))
    im.save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", type=Path, required=True)
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--rows", type=int, default=2)
    a = ap.parse_args()
    grid = Image.open(a.grid).convert("RGB")
    cw, ch = grid.width / a.cols, grid.height / a.rows
    cells = {}
    for i, name in CELLS.items():
        r, c = divmod(i, a.cols)
        cells[name] = grid.crop((round(c * cw), round(r * ch), round((c + 1) * cw), round((r + 1) * ch)))
    print(f"grid {grid.size}, cell {round(cw)}x{round(ch)}")

    # 1. base portrait from the neutral cell, same framing rules as prepare_portrait.py
    base_pts_cell = landmarks(cells["neutral"])
    base = frame(cells["neutral"], base_pts_cell)
    PREP.mkdir(exist_ok=True)
    base.save(PREP / "face.jpg", quality=94)
    p_base = landmarks(base)
    (PREP / "face.json").write_text(json.dumps({"version": 1, "width": OUT_W, "height": OUT_H, "source": str(a.grid) + "#cell0",
        "points": {k: [round(x, 1), round(y, 1)] for k, (x, y) in p_base.items()}}, indent=1), encoding="utf-8")
    base_rgb = np.asarray(base).astype(np.float32)
    H, W = base_rgb.shape[:2]
    edge = np.concatenate([base_rgb[0], base_rgb[-1], base_rgb[:, 0], base_rgb[:, -1]])
    bg = tuple(int(v) for v in np.median(edge, axis=0))
    face_m, eyes_m, core_m = face_masks(p_base, (H, W))

    # 2. align + colour-match every other cell into the base frame
    aligned: dict[str, np.ndarray] = {"neutral": base_rgb}
    for name, cell in cells.items():
        if name == "neutral":
            continue
        try:
            pc = landmarks(cell)
        except SystemExit:
            print(f"  ! no face in cell {name}; skipped"); continue
        im = align(cell, pc, p_base, bg)
        aligned[name] = colour_match(np.asarray(im).astype(np.float32), base_rgb, core_m)

    # 3. speech shapes from the talking cells (mouth region, the same alpha the warp generator used)
    MOUTHS.mkdir(exist_ok=True)
    m_alpha = mouth_alpha((H, W), p_base).astype(np.float32) / 255.0
    Image.fromarray(base_rgb.astype(np.uint8)).save(MOUTHS / "base.png")
    for shape, src_name in VISEMES.items():
        if src_name not in aligned:
            continue
        fname = SHAPES[shape]["file"] if shape in SHAPES else "smile.png"
        save_layer(aligned[src_name], m_alpha, MOUTHS / fname)
    mouths_json = {
        "canvas": {"width": W, "height": H},
        "mouth_alpha_box": [int(v) for v in np.argwhere(m_alpha > 0.02).min(0)[::-1].tolist() + np.argwhere(m_alpha > 0.02).max(0)[::-1].tolist()],
        "shapes": {k: v["file"] for k, v in SHAPES.items()},
        "rhubarb": RHUBARB,
        "open": {k: v["open"] for k, v in SHAPES.items()},
        "width": {k: v["width"] for k, v in SHAPES.items()},
        "source": "expression grid cells (real renders), aligned on the mouth corners; see expressions_from_grid.py",
    }
    (HERE / "mouths.json").write_text(json.dumps(mouths_json, indent=1), encoding="utf-8")

    # 4. expression layers
    EXPR.mkdir(exist_ok=True)
    layers: dict[str, dict] = {}
    for name in FACE_EXPR + EYES_EXPR:
        if name not in aligned:
            continue
        region = "eyes" if name in EYES_EXPR else "face"
        save_layer(aligned[name], eyes_m if region == "eyes" else face_m, EXPR / f"{name}.png")
        layers[name] = {"file": f"{name}.png", "region": region}
    (HERE / "expressions.json").write_text(json.dumps({"canvas": {"width": W, "height": H}, "layers": layers,
        "moods": {"warm": "smile_warm", "enthusiastic": "smile_big", "neutral": None, "thinking": None, "concerned": None,
                  "backchannel": "smile_soft", "greeting": "smile_warm", "listening": "smile_soft", "question": "surprised"},
        "note": "face layers composite over base.png under the speech mouth; eyes layers are used for glances/blinks"}, indent=1), encoding="utf-8")

    # 5. contact sheet
    names = ["neutral"] + list(layers) + ["A", "E", "I", "O"]
    tiles = []
    for n in names:
        im = Image.fromarray(base_rgb.astype(np.uint8)).convert("RGBA")
        if n in layers:
            im.alpha_composite(Image.open(EXPR / layers[n]["file"]).convert("RGBA"))
        elif n in SHAPES:
            im.alpha_composite(Image.open(MOUTHS / SHAPES[n]["file"]).convert("RGBA"))
        t = im.convert("RGB").crop((100, 60, 540, 560)).resize((176, 200))
        tile = Image.new("RGB", (176, 218), (16, 16, 20)); tile.paste(t, (0, 0))
        ImageDraw.Draw(tile).text((4, 203), n, fill=(255, 255, 255)); tiles.append(tile)
    cols = 6
    sheet = Image.new("RGB", (cols * 180, ((len(tiles) + cols - 1) // cols) * 222), (8, 8, 10))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * 180, (i // cols) * 222))
    sheet.save(HERE / "expressions_sheet.png")
    print(f"base from cell 0 -> {PREP / 'face.jpg'}; {len(VISEMES)} speech shapes -> {MOUTHS}; {len(layers)} expression layers -> {EXPR}; sheet expressions_sheet.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
