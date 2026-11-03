"""Extract head-motion tracks from reference footage with MediaPipe Face Landmarker (CPU only).

For every frame this records the quantities the puppet renderer in avatar_widget/src/avatar.js
can actually play back:

    pitch, yaw, roll   degrees, from the facial transformation matrix (pitch > 0 = chin down)
    dx, dy             nose-tip travel, percent of frame width / height, relative to frame 0
    scale              inter-ocular distance relative to frame 0 (> 1 = closer to camera)
    blink              mean of eyeBlinkLeft / eyeBlinkRight blendshapes (0..1)
    jaw                jawOpen blendshape (0..1)

Only pose curves leave this script. No pixels, no identity embedding, no face crop is written, so
the TEST-ONLY presenter footage in prepared/clips can be used as a *motion* source (see
tran_moment/motion/README.md for the reasoning and limits).

    python tran_moment/motion/extract_motion.py                 # all clips + presenter segment
    python tran_moment/motion/extract_motion.py --only idle small_nod
    python tran_moment/motion/extract_motion.py --no-long       # skip sample_audio.mp4

Writes tran_moment/motion/tracks/<name>.json.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
TM = HERE.parent
MODEL = HERE / "models" / "face_landmarker.task"
CLIPS = TM / "prepared" / "clips"
LONG = TM / "sample_audio.mp4"
OUT = HERE / "tracks"

# Landmark indices (MediaPipe 478-point mesh)
NOSE_TIP = 1
EYE_L_OUTER = 33
EYE_R_OUTER = 263

# The prepared clips are forward+reverse ping-pong loops (prepared/IDLE_SOURCE.md); only the first
# half is real footage. The presenter segment of sample_audio.mp4 is 0:00-0:55 (IDLE_SOURCE.md).
SOURCES = {
    "idle":       {"path": CLIPS / "idle.mp4",       "half": True},
    "small_nod":  {"path": CLIPS / "small_nod.mp4",  "half": True},
    "slow_nod":   {"path": CLIPS / "slow_nod.mp4",   "half": True},
    "look_up":    {"path": CLIPS / "look_up.mp4",    "half": True},
    "both_hands": {"path": CLIPS / "both_hands.mp4", "half": True},
    "open_palm":  {"path": CLIPS / "open_palm.mp4",  "half": True},
    "greeting":   {"path": CLIPS / "greeting.mp4",   "half": True},
    "presenter":  {"path": LONG, "half": False, "start_s": 0.0, "end_s": 55.0, "crop_track": True},
    # 2026-11-03: AI-rendered clip of the current (grid) Neha talking to camera, 478x850 @ 24 fps, 10 s.
    # Same face as the puppet, big and well lit: the best source for her own head motion and idle rhythm.
    "neha_sample": {"path": TM.parent / "voice_clone" / "src" / "sample.mp4", "half": False, "start_s": 0.0, "end_s": 10.1, "crop_track": False},
}


def euler_from_matrix(m: np.ndarray) -> tuple[float, float, float]:
    """(pitch, yaw, roll) in degrees from MediaPipe's camera-space face matrix.

    MediaPipe's matrix maps canonical face space to camera space (x right, y up, z toward viewer).
    Decompose as R = Rz(roll) * Ry(yaw) * Rx(pitch) and flip pitch so chin-down is positive, which is
    the puppet's convention (positive pitch = head travels down the frame).
    """
    r = m[:3, :3]
    sy = math.hypot(r[0, 0], r[1, 0])
    if sy > 1e-6:
        x = math.atan2(r[2, 1], r[2, 2])
        y = math.atan2(-r[2, 0], sy)
        z = math.atan2(r[1, 0], r[0, 0])
    else:
        x = math.atan2(-r[1, 2], r[1, 1])
        y = math.atan2(-r[2, 0], sy)
        z = 0.0
    # Signs checked against the footage: the nod clip must go positive, the look-up clip negative,
    # and CSS rotate() is clockwise-positive on screen while the camera-space z rotation is not.
    pitch = math.degrees(x)
    yaw = math.degrees(y)
    roll = -math.degrees(z)
    return pitch, yaw, roll


def crop_candidates(width: int, height: int, last: tuple[float, float, float] | None) -> list[tuple[int, int, int]]:
    """Square crops (x, y, side) to try. Around the last face when known, else a sweep.

    BlazeFace resizes the whole input to ~128-192 px, so a head that is small in a wide shot is
    lost unless we hand it a crop. Side = 3x the last face height, minimum 200 px.
    """
    if last is not None:
        cx, cy, fh = last
        side = int(min(height, max(200, fh * 3.2)))
        x = int(min(max(0, cx - side / 2), width - side))
        y = int(min(max(0, cy - side / 2), height - side))
        return [(x, y, side)]
    side = height
    step = max(1, (width - side) // 3) if width > side else 1
    xs = list(range(0, max(1, width - side + 1), step)) if width > side else [0]
    cands = [(x, 0, side) for x in xs]
    half = side // 2
    cands += [(x, 0, half) for x in range(0, width - half + 1, max(1, half // 2))]
    cands += [(x, half // 2, half) for x in range(0, width - half + 1, max(1, half // 2))]
    return cands


def blend(result, name: str) -> float:
    if not result.face_blendshapes:
        return 0.0
    for cat in result.face_blendshapes[0]:
        if cat.category_name == name:
            return float(cat.score)
    return 0.0


def extract(name: str, spec: dict, landmarker_factory) -> dict | None:
    path: Path = spec["path"]
    if not path.exists():
        print(f"  skip {name}: missing {path}")
        return None
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    start_f = int(round(spec.get("start_s", 0.0) * fps))
    end_f = total
    if spec.get("end_s") is not None:
        end_f = min(total, int(round(spec["end_s"] * fps)))
    if spec.get("half"):
        end_f = min(end_f, total // 2)
    if start_f:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)

    import mediapipe as mp

    landmarker = landmarker_factory()
    frames = []
    misses = 0
    base = None
    last = None   # (cx, cy, face_h) in full-frame pixels, for crop tracking on wide shots
    t0 = time.time()
    idx = start_f
    use_crop = bool(spec.get("crop_track"))
    while idx < end_f:
        ok, bgr = cap.read()
        if not ok:
            break
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        t = (idx - start_f) / fps
        found = None
        if not use_crop:
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            res = landmarker.detect_for_video(image, int(round((idx - start_f) * 1000.0 / fps)))
            if res.face_landmarks and res.facial_transformation_matrixes:
                found = (res, 0, 0, width, height)
        else:
            # IMAGE-mode detections on crops: VIDEO mode needs monotonic timestamps per stream, and
            # we may try several crops for one frame.
            for (cx0, cy0, side) in crop_candidates(width, height, last):
                crop = np.ascontiguousarray(rgb[cy0:cy0 + side, cx0:cx0 + side])
                if crop.size == 0:
                    continue
                res = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=crop))
                if res.face_landmarks and res.facial_transformation_matrixes:
                    found = (res, cx0, cy0, side, side)
                    break
        if not found:
            misses += 1
            last = None
            frames.append({"t": round(t, 4), "ok": False})
            idx += 1
            continue
        res, ox, oy, cw, ch = found
        lm = res.face_landmarks[0]
        m = np.array(res.facial_transformation_matrixes[0], dtype=float).reshape(4, 4)
        pitch, yaw, roll = euler_from_matrix(m)
        # landmarks back to full-frame normalised coordinates
        nx = (ox + lm[NOSE_TIP].x * cw) / width
        ny = (oy + lm[NOSE_TIP].y * ch) / height
        ex = (lm[EYE_L_OUTER].x - lm[EYE_R_OUTER].x) * cw
        ey = (lm[EYE_L_OUTER].y - lm[EYE_R_OUTER].y) * ch
        iod = math.hypot(ex, ey)
        ys = [p.y for p in lm]
        face_h = (max(ys) - min(ys)) * ch
        last = (ox + lm[NOSE_TIP].x * cw, oy + lm[NOSE_TIP].y * ch, face_h)
        rec = {
            "t": round(t, 4), "ok": True,
            "pitch": pitch, "yaw": yaw, "roll": roll,
            "nx": nx, "ny": ny, "iod": iod,
            "blink": (blend(res, "eyeBlinkLeft") + blend(res, "eyeBlinkRight")) / 2.0,
            "jaw": blend(res, "jawOpen"),
        }
        if base is None:
            base = rec
        rec["dx"] = (nx - base["nx"]) * 100.0
        rec["dy"] = (ny - base["ny"]) * 100.0
        rec["scale"] = (iod / base["iod"]) if base["iod"] > 1e-6 else 1.0
        for k in ("pitch", "yaw", "roll", "dx", "dy", "scale", "blink", "jaw", "nx", "ny", "iod"):
            rec[k] = round(float(rec[k]), 4)
        frames.append(rec)
        idx += 1
    cap.release()
    landmarker.close()
    n = len(frames)
    took = time.time() - t0
    print(f"  {name:11s} {n:5d} frames @ {fps:.0f} fps, {misses} misses, {took:.1f}s ({n / max(took, 1e-6):.0f} fps)")
    return {
        "name": name, "source": str(path.relative_to(TM) if path.is_relative_to(TM) else path.relative_to(TM.parent)), "fps": fps, "width": width, "height": height,
        "frame_range": [start_f, start_f + n], "ping_pong_half": bool(spec.get("half")),
        "detected": n - misses, "missed": misses, "frames": frames,
        "units": {"pitch": "deg (chin down > 0)", "yaw": "deg", "roll": "deg", "dx": "% frame width from frame 0",
                  "dy": "% frame height from frame 0", "scale": "inter-ocular / frame 0", "blink": "0..1", "jaw": "0..1"},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="*", help="subset of source names")
    ap.add_argument("--no-long", action="store_true", help="skip the presenter segment of sample_audio.mp4")
    a = ap.parse_args(argv)
    if not MODEL.exists():
        print(f"missing model {MODEL}", file=sys.stderr)
        return 1

    import mediapipe as mp
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core.base_options import BaseOptions

    def factory(mode="VIDEO"):
        opts = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(MODEL)),
            running_mode=vision.RunningMode.IMAGE if mode == "IMAGE" else vision.RunningMode.VIDEO,
            num_faces=1,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
            min_face_detection_confidence=0.4,
            min_face_presence_confidence=0.4,
            min_tracking_confidence=0.4,
        )
        return vision.FaceLandmarker.create_from_options(opts)

    OUT.mkdir(parents=True, exist_ok=True)
    names = a.only or list(SOURCES)
    if a.no_long:
        names = [n for n in names if n != "presenter"]
    print(f"mediapipe {mp.__version__}, model {MODEL.name}")
    written = 0
    for name in names:
        spec = SOURCES.get(name)
        if not spec:
            print(f"  unknown source {name}")
            continue
        track = extract(name, spec, (lambda: factory("IMAGE")) if spec.get("crop_track") else factory)
        if track:
            (OUT / f"{name}.json").write_text(json.dumps(track), encoding="utf-8")
            written += 1
    print(f"wrote {written} track files to {OUT}")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
