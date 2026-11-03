"""Voice conversion service: turns Piper's speech into the consented speaker's tone (OpenVoice V2, MIT).

Runs in its own Python 3.10 environment (OpenVoice pins old library versions):

    tools\\openvoice-venv\\Scripts\\python.exe voice_clone\\openvoice_service.py            # http://127.0.0.1:8020

    POST /convert?speaker=<name>   body: WAV (any rate)  ->  WAV in the speaker's voice
    GET  /health                   {"ok": true, "speakers": [...]}

Speakers are made by clone_speaker.py (voice_models/clone/<name>.target.pt). The base voice's own embedding
(piper_source.pt) is made once from Piper samples. Listens on 127.0.0.1 only. The app (VOICE_CONVERT_URL) falls
back to plain Piper speech when this service is not running.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
CKPT = ROOT / "tools" / "openvoice" / "checkpoints_v2" / "converter"
SPEAKERS = ROOT / "voice_models" / "clone"


def ensure_checkpoint() -> Path:
    """Download the OpenVoice V2 tone-colour converter (MIT) once."""
    if not (CKPT / "checkpoint.pth").exists():
        from huggingface_hub import hf_hub_download

        for f in ("converter/config.json", "converter/checkpoint.pth"):
            hf_hub_download("myshell-ai/OpenVoiceV2", f, local_dir=str(CKPT.parent))
    return CKPT


def load_converter():
    import torch
    from openvoice.api import OpenVoiceBaseClass, ToneColorConverter

    class NoWatermarkConverter(ToneColorConverter):
        # upstream passes enable_watermark on to a base class that rejects it; the watermark model is not needed
        def __init__(self, config_path: str, device: str) -> None:
            OpenVoiceBaseClass.__init__(self, config_path, device=device)
            self.watermark_model = None
            self.version = getattr(self.hps, "_version_", "v1")

    torch.set_num_threads(max(1, (os.cpu_count() or 4) // 2))
    ckpt = ensure_checkpoint()
    conv = NoWatermarkConverter(str(ckpt / "config.json"), device="cpu")
    conv.load_ckpt(str(ckpt / "checkpoint.pth"))
    return conv


class Converter:
    def __init__(self) -> None:
        import torch

        self.torch = torch
        self.model = load_converter()
        self.lock = threading.Lock()
        self.source = torch.load(SPEAKERS / "piper_source.pt", map_location="cpu", weights_only=True)
        self.targets: dict[str, object] = {}

    def speakers(self) -> list[str]:
        return sorted(p.name[: -len(".target.pt")] for p in SPEAKERS.glob("*.target.pt"))

    def target(self, name: str):
        if name not in self.targets:
            p = SPEAKERS / f"{name}.target.pt"
            if not p.exists():
                raise KeyError(name)
            self.targets[name] = self.torch.load(p, map_location="cpu", weights_only=True)
        return self.targets[name]

    def convert(self, wav: bytes, speaker: str, tau: float = 0.3) -> bytes:
        tgt = self.target(speaker)
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / "in.wav", Path(d) / "out.wav"
            src.write_bytes(wav)
            with self.lock:
                self.model.convert(audio_src_path=str(src), src_se=self.source, tgt_se=tgt, output_path=str(out), tau=tau)
            return out.read_bytes()


def make_handler(conv: Converter):
    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if urlparse(self.path).path == "/health":
                self._send(200, json.dumps({"ok": True, "speakers": conv.speakers()}).encode())
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            if u.path != "/convert":
                return self._send(404, b'{"error":"not found"}')
            q = parse_qs(u.query)
            speaker = (q.get("speaker") or [""])[0]
            tau = float((q.get("tau") or ["0.3"])[0])
            n = int(self.headers.get("Content-Length") or 0)
            if not speaker or not 0 < n <= 20_000_000:
                return self._send(400, b'{"error":"speaker and a WAV body are required"}')
            body = self.rfile.read(n)
            t = time.perf_counter()
            try:
                out = conv.convert(body, speaker, tau)
            except KeyError:
                return self._send(404, json.dumps({"error": f"unknown speaker {speaker}"}).encode())
            except Exception as ex:  # noqa: BLE001
                return self._send(500, json.dumps({"error": f"{type(ex).__name__}: {ex}"[:300]}).encode())
            self.log_message("converted %d bytes for %s in %.2f s", n, speaker, time.perf_counter() - t)
            self._send(200, out, "audio/wav")

    return H


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8020)
    a = ap.parse_args()
    conv = Converter()
    print(f"OpenVoice converter ready on http://127.0.0.1:{a.port} — speakers: {conv.speakers() or 'none yet'}")
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(conv)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
