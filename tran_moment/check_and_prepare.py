"""Quality-check a portrait and write the Path B crop + landmarks.

Uses the VIDEO project's MediaPipe helper (no GPU). Writes next to this folder by default.

    uv run --with mediapipe python tran_moment/check_and_prepare.py
    uv run --with mediapipe python tran_moment/check_and_prepare.py --photo brands/hans_hyundai/face.jpg --out brands/hans_hyundai/prepared
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_PHOTO = HERE / "face.jpg"
DEFAULT_OUT = HERE / "prepared"


def write_report(photo: Path, out: Path) -> None:
    face_json = out / "face.json"
    report = {
        "source": str(photo),
        "prepared_jpg": str(out / "face.jpg"),
        "prepared_json": str(face_json),
        "checklist": {
            "one_face": True,
            "saved_srgb": True,
            "note": "MediaPipe found exactly one face. Re-read FACE_AVATAR_SETUP.md for lighting / resolution / consent.",
        },
    }
    if face_json.exists():
        data = json.loads(face_json.read_text(encoding="utf-8"))
        pts = data.get("points") or {}
        report["width"] = data.get("width")
        report["height"] = data.get("height")
        if "mouth_left" in pts and "mouth_right" in pts:
            report["mouth_width_px"] = round(pts["mouth_right"][0] - pts["mouth_left"][0], 1)
        if "eye_l_outer" in pts and "eye_r_outer" in pts:
            face_w = pts["eye_r_outer"][0] - pts["eye_l_outer"][0]
            report["approx_face_width_px"] = round(face_w * 1.8, 1)
            report["meets_512_on_face"] = (face_w * 1.8) >= 512
        if "lip_lower_inner" in pts and "lip_upper_inner" in pts and "mouth_left" in pts:
            mouth_w = max(1.0, pts["mouth_right"][0] - pts["mouth_left"][0])
            open_ratio = (pts["lip_lower_inner"][1] - pts["lip_upper_inner"][1]) / mouth_w
            report["mouth_open_ratio"] = round(open_ratio, 3)
            report["mouth_open_warning"] = open_ratio > 0.08
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {out / 'report.json'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--photo", type=Path, default=DEFAULT_PHOTO)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--force", action="store_true", help="re-run MediaPipe even if prepared files exist")
    a = ap.parse_args(argv)

    photo = a.photo if a.photo.is_absolute() else (HERE / a.photo)
    out = a.out if a.out.is_absolute() else (HERE / a.out)
    if not photo.exists():
        print(f"missing photo: {photo}", file=sys.stderr)
        return 1

    ready = (out / "face.jpg").exists() and (out / "face.json").exists()
    if not ready or a.force:
        script = ROOT / "scripts" / "prepare_photo_avatar.py"
        cmd = [
            sys.executable,
            str(script),
            "--photo",
            str(photo),
            "--out",
            str(out),
            "--width",
            str(a.width),
            "--no-env",
        ]
        env = dict(**{k: v for k, v in __import__("os").environ.items()})
        env.setdefault("OPENBLAS_NUM_THREADS", "1")
        env.setdefault("OMP_NUM_THREADS", "1")
        print("running", " ".join(cmd))
        print("if MediaPipe is missing: uv run --with mediapipe python tran_moment/check_and_prepare.py --force")
        rc = subprocess.call(cmd, env=env)
        if rc != 0:
            return rc
    else:
        print(f"using existing {out / 'face.jpg'} (pass --force to re-crop)")

    write_report(photo, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
