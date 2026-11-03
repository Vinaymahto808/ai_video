"""Write a few sentences in the base Piper voice (synthetic, no real person) for the converter's source embedding.

    uv run python voice_clone/make_piper_samples.py

Output: voice_clone/work/piper_samples/*.wav — then: tools/openvoice-venv python voice_clone/clone_speaker.py --source
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

SENTENCES = [
    "Namaste! Galaxy Toyota mein aapka swagat hai.",
    "The Glanza is a stylish hatchback with a dual tone interior.",
    "Aap test drive ke liye apna naam aur phone number bata dijiye.",
    "Our showroom is open from nine thirty in the morning to seven thirty in the evening.",
    "Fortuner ek powerful SUV hai jo har road par chalti hai.",
    "Would you like to know about the latest offers and easy finance options?",
    "Exchange value aur on road price ki poori jaankari showroom par milegi.",
    "Thank you for calling, have a wonderful day.",
]


def main() -> int:
    from app.voice.providers import get_tts

    tts = get_tts()
    out = HERE / "work" / "piper_samples"
    out.mkdir(parents=True, exist_ok=True)
    for i, text in enumerate(SENTENCES, 1):
        speech = tts._synthesize(text, "en")  # uncached, unconverted base voice
        (out / f"piper_{i:02d}.wav").write_bytes(speech.audio)
    print(f"wrote {len(SENTENCES)} samples to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
