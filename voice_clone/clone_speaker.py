"""Make the speaker embeddings for the OpenVoice converter (runs in tools/openvoice-venv).

    tools\\openvoice-venv\\Scripts\\python.exe voice_clone\\clone_speaker.py --source
        base voice: voice_clone/work/piper_samples/*.wav -> voice_models/clone/piper_source.pt (synthetic voice)
    tools\\openvoice-venv\\Scripts\\python.exe voice_clone\\clone_speaker.py --name yash --wav voice_clone\\work\\audio\\yash.wav
        consented speaker -> voice_models/clone/yash.target.pt   (needs voice_clone/consent/consent.json + signed document)

The reference is cut into ~6 s pieces and their embeddings are averaged. 30–60 s of clean speech by this speaker
alone (no music, no second voice) is enough for OpenVoice; more gives a steadier tone.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
OUT = ROOT / "voice_models" / "clone"


def chunks(wav: Path, tmp: Path, seconds: float = 6.0) -> list[str]:
    import librosa
    import soundfile as sf

    y, sr = librosa.load(str(wav), sr=22050, mono=True)
    n = int(seconds * sr)
    paths = []
    for i in range(0, len(y), n):
        piece = y[i:i + n]
        if len(piece) < sr * 2:          # last tiny piece
            continue
        p = tmp / f"c{i // n:03d}.wav"
        sf.write(str(p), piece, sr)
        paths.append(str(p))
    return paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--source", action="store_true", help="embedding of the base Piper voice")
    g.add_argument("--name", help="speaker name for the consented voice, e.g. yash")
    ap.add_argument("--wav", type=Path, help="reference WAV of the consented speaker (from get_audio.py)")
    a = ap.parse_args(argv)
    import torch
    from openvoice_service import load_converter

    conv = load_converter()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.source:
        refs = sorted(str(p) for p in (HERE / "work" / "piper_samples").glob("*.wav"))
        if not refs:
            sys.exit("run voice_clone/make_piper_samples.py first")
        se = conv.extract_se(refs)
        torch.save(se, OUT / "piper_source.pt")
        print(f"base voice embedding from {len(refs)} samples -> {OUT / 'piper_source.pt'}")
        return 0

    from consent import require_consent

    consent = require_consent()
    if not a.wav or not a.wav.exists():
        sys.exit("--wav: reference WAV of the consented speaker (voice_clone/get_audio.py --ranges ...)")
    with tempfile.TemporaryDirectory() as d:
        refs = chunks(a.wav, Path(d))
        if len(refs) < 3:
            sys.exit("reference too short — need at least ~20 s of speech")
        se = conv.extract_se(refs)
    torch.save(se, OUT / f"{a.name}.target.pt")
    (OUT / f"{a.name}.json").write_text(json.dumps({
        "speaker": consent["speaker_name"], "consent_signed_on": consent["signed_on"], "reference": str(a.wav),
        "pieces": len(refs), "made_on": date.today().isoformat(), "model": "OpenVoice V2 tone colour converter (MIT)",
    }, indent=2), encoding="utf-8")
    print(f"speaker '{a.name}' ({consent['speaker_name']}) from {len(refs)} pieces -> {OUT / (a.name + '.target.pt')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
