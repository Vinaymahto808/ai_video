# Path C — neural video (GPU host, later)

Not possible on this laptop. Inference only — still no training. Use when you rent an 8–16 GB NVIDIA card (cloud T4 / L4 / A10, or RTX 3060/4090).

Pipelines (both MIT, commercial-ok per project licence notes):

| Piece | Role |
|---|---|
| MuseTalk | Lip-sync from TTS audio |
| LivePortrait | Expression + head motion from the still + a ~10 s talking clip |
| LiveTalking (`lipku/LiveTalking`) | Real-time host the VIDEO app already adapters to |

**Do not** use Wav2Lip on the dealership sites (non-commercial licence).

## On the GPU box

Follow `docs/14_REALTIME_VIDEO_AVATAR.md` and `scripts/setup_livetalking_musetalk.sh`. Native PyTorch/ONNX, not a macOS/CoreML port.

1. Copy `../prepared/face.jpg` (and later a 10 s reference clip) onto the host.
2. Extract LivePortrait’s expression set for this face (~few minutes, once per persona).
3. Drive MuseTalk from the same TTS the chatbot already uses (Gemini / Piper).
4. Point this repo at the host and flip the provider:

```
AVATAR_PROVIDER=livetalking
LIVETALKING_URL=http://GPU-HOST:PORT
```

See `env.example`. Keep photo / voice-only fallback when the host is down (`docs/AVATAR_DEPLOYMENT.md`).

Shot list for the reference clip: `reference_clip.txt`.
