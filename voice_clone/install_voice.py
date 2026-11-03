"""Step 4 — install the trained voice so the avatar speaks with it.

    uv run python voice_clone/install_voice.py --onnx "C:/Users/Galaxy/Downloads/hans_hi.onnx" --language hi

Copies <voice>.onnx and <voice>.onnx.json to voice_models/clone/, sets PIPER_VOICE_HI (or PIPER_VOICE_EN) in .env
and writes three test sentences to voice_clone/work/samples/ through the live assistant's own speech path.
Restart the server afterwards.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from consent import require_consent  # noqa: E402

DEST = ROOT / "voice_models" / "clone"
ENV = ROOT / ".env"
SAMPLES = {
    "hi": ["नमस्ते! Hans Hyundai में आपका स्वागत है।", "Creta की कीमत ₹11.10 लाख से शुरू होती है।",
           "क्या मैं आपके लिए टेस्ट ड्राइव बुक कर दूँ?"],
    "en": ["Namaste! Welcome to Hans Hyundai.", "The Creta starts at ₹11.10 lakh ex-showroom.",
           "Shall I book a test drive for you?"],
}


def set_env(key: str, value: str) -> None:
    text = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    if re.search(rf"^{key}=.*$", text, flags=re.M):
        text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\n{key}={value}\n"
    ENV.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--onnx", type=Path, required=True)
    ap.add_argument("--language", choices=["hi", "en"], required=True)
    a = ap.parse_args(argv)
    require_consent()
    cfg = a.onnx.with_name(a.onnx.name + ".json")
    if not a.onnx.exists() or not cfg.exists():
        sys.exit(f"need both {a.onnx} and {cfg}")
    DEST.mkdir(parents=True, exist_ok=True)
    shutil.copy2(a.onnx, DEST / a.onnx.name)
    shutil.copy2(cfg, DEST / cfg.name)
    rel = (DEST / a.onnx.name).relative_to(ROOT).as_posix()
    set_env("PIPER_VOICE_HI" if a.language == "hi" else "PIPER_VOICE_EN", rel)
    print(f"installed {rel}; .env updated")

    from app.voice.providers import PiperTts
    from app.config import get_settings

    tts = PiperTts(get_settings().piper_binary, {a.language: str(DEST / a.onnx.name), "default": str(DEST / a.onnx.name)})
    out = HERE / "work" / "samples"
    out.mkdir(parents=True, exist_ok=True)
    for i, text in enumerate(SAMPLES[a.language], 1):
        speech = tts.synthesize(text, a.language)
        (out / f"{a.language}_{i}.wav").write_bytes(speech.audio)
    print(f"listen: {out} — then restart the server")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
