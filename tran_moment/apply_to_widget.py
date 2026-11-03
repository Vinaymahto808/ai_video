"""Copy the prepared portrait into the live widget (AVATAR_PROVIDER=photo).

Does not start the server. Restart run.ps1 after this.

    uv run python tran_moment/apply_to_widget.py
    uv run python tran_moment/apply_to_widget.py --env    # also set AVATAR_PROVIDER=photo
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = HERE / "prepared"
DST = ROOT / "app" / "static" / "avatar"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=Path, default=SRC)
    ap.add_argument("--env", action="store_true", help="set AVATAR_PROVIDER=photo in .env")
    a = ap.parse_args(argv)

    jpg, js = a.src / "face.jpg", a.src / "face.json"
    if not jpg.exists() or not js.exists():
        print("run check_and_prepare.py first — missing prepared/face.jpg or face.json", file=sys.stderr)
        return 1
    DST.mkdir(parents=True, exist_ok=True)
    shutil.copy2(jpg, DST / "face.jpg")
    shutil.copy2(js, DST / "face.json")
    print(f"copied {jpg.name} and {js.name} → {DST}")
    print("the widget also serves face-studio.jpg by default (AVATAR_PHOTO_URL).")
    print("either run: uv run python scripts/style_avatar_background.py")
    print("or set AVATAR_PHOTO_URL=/static/avatar/face.jpg in .env")
    if a.env:
        sys.path.insert(0, str(ROOT / "scripts"))
        from prepare_photo_avatar import set_env

        set_env("AVATAR_PROVIDER", "photo")
        set_env("AVATAR_PHOTO_URL", "/static/avatar/face.jpg")
        set_env("AVATAR_FACE_URL", "/static/avatar/face.json")
        print("set AVATAR_PROVIDER=photo and photo URLs in .env — restart the server")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
