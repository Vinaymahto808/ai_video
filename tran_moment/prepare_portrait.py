"""Turn any portrait photo into the puppet's prepared assets: prepared/face.jpg (640x852, face framed like
the original Neha portrait) + prepared/face.json (the landmark points the card's puppet and the mouth-shape
generator use). CPU only (MediaPipe FaceLandmarker from tran_moment/motion/models).

    python tran_moment/prepare_portrait.py --photo voice_clone/src/face.png
    python tran_moment/path_b/generate_mouths.py        # then: aligned mouth shapes for the new face

Framing target (what the old face.json had, so overlays, blink bands and mouth patches keep working):
eyes centred at 26.5% of the height, outer-eye distance 25% of the width. Missing picture area (a square
photo does not reach 3:4) is filled with the photo's own edge colour, so a plain studio background extends.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
MODEL = HERE / "motion" / "models" / "face_landmarker.task"
OUT_W, OUT_H = 640, 852
# Target framing. A head-and-shoulders photo is scaled so it fills the frame's height (no blank band under
# the shoulders) and the eyes sit around a third of the way down; the outer-eye distance is only a lower
# bound (never smaller than a quarter of the width, which is what the old portrait had).
EYE_Y, EYE_DIST = 0.27, 0.25  # eyes high: the card's captions sit on the chest, not on the mouth

# MediaPipe FaceMesh indices -> the names the puppet expects ("l" = image left = the subject's right eye)
POINTS = {
    "mouth_left": 61, "mouth_right": 291, "lip_top": 0, "lip_upper_inner": 13, "lip_lower_inner": 14, "lip_bottom": 17,
    "chin": 152, "nose_bottom": 2, "nose_tip": 1,
    "eye_l_outer": 33, "eye_l_inner": 133, "eye_l_top": 159, "eye_l_bottom": 145, "brow_l": 105,
    "eye_r_inner": 362, "eye_r_outer": 263, "eye_r_top": 386, "eye_r_bottom": 374, "brow_r": 334,
}


def landmarks(img: Image.Image) -> dict[str, tuple[float, float]]:
    import mediapipe as mp
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core.base_options import BaseOptions
    opts = vision.FaceLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(MODEL)), running_mode=vision.RunningMode.IMAGE,
                                        num_faces=1, min_face_detection_confidence=0.4, min_face_presence_confidence=0.4)
    with vision.FaceLandmarker.create_from_options(opts) as lm:
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.asarray(img.convert("RGB"))))
    if not res.face_landmarks:
        raise SystemExit("no face found in the photo")
    pts = res.face_landmarks[0]
    w, h = img.size
    return {name: (pts[i].x * w, pts[i].y * h) for name, i in POINTS.items()}


def frame(img: Image.Image, p: dict) -> Image.Image:
    """Scale + translate so the eyes land where the old portrait had them; pad with the edge colour."""
    ex = (p["eye_l_outer"][0] + p["eye_r_outer"][0]) / 2
    ey = (p["eye_l_outer"][1] + p["eye_r_outer"][1]) / 2
    dist = abs(p["eye_r_outer"][0] - p["eye_l_outer"][0])
    scale = max((EYE_DIST * OUT_W) / max(1.0, dist), OUT_H / img.height)  # fill the height, eyes never too small
    scaled = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    # where the eye centre must sit in the output; never leave blank space above the photo
    tx, ty = OUT_W / 2 - ex * scale, min(0.0, EYE_Y * OUT_H - ey * scale)
    # pad generously with the edge colour, then crop
    edge = np.asarray(scaled.convert("RGB"))
    colour = tuple(int(v) for v in np.median(np.concatenate([edge[0], edge[-1], edge[:, 0], edge[:, -1]]), axis=0))
    pad = max(OUT_W, OUT_H)
    padded = ImageOps.expand(scaled.convert("RGB"), border=pad, fill=colour)
    left, top = round(pad - tx), round(pad - ty)
    return padded.crop((left, top, left + OUT_W, top + OUT_H))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photo", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=HERE / "prepared")
    a = ap.parse_args()
    img = Image.open(a.photo)
    img = ImageOps.exif_transpose(img).convert("RGB")
    p = landmarks(img)
    out = frame(img, p)
    a.out.mkdir(parents=True, exist_ok=True)
    out.save(a.out / "face.jpg", quality=94)
    q = landmarks(out)  # measure again on the final frame so the JSON is exact
    data = {"version": 1, "width": OUT_W, "height": OUT_H, "source": str(a.photo), "points": {k: [round(x, 1), round(y, 1)] for k, (x, y) in q.items()}}
    (a.out / "face.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    eyes = abs(q["eye_r_outer"][0] - q["eye_l_outer"][0])
    print(f"wrote {a.out / 'face.jpg'} ({OUT_W}x{OUT_H}) and face.json; eye distance {eyes:.0f}px, eyes at y={q['eye_l_outer'][1]:.0f}, mouth at y={q['lip_upper_inner'][1]:.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
