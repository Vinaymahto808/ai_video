# Face Avatar Setup Guide

Oct 15, 2026 · Harpreet Sachdev

How to prepare a face image and wire it into a talking, reacting avatar — the CPU / no-GPU path you can do now, and the GPU path for later. For the three-brand AI Video Sales Assistant.

**This folder** (`tran_moment/`) is the working kit. Source portrait: `face.jpg`. Prepared crops, mouth layers, demos and Path C notes live next to it. Nothing here trains a model.

---

## What this covers

There is no model training on a laptop without a GPU — and you don't need any. What you actually do is prepare a face image and point one of three avatar pipelines at it. The face is animated at runtime from the AI's reply audio; the knowledge base never touches it.

Pick your path by hardware:

1. **Path A — CPU, browser 3D avatar (do this now).** The avatar renders in the visitor's browser. No GPU on your side. Demo: `path_a/index.html`.
2. **Path B — CPU, 2D photo avatar (do this now).** A real photo with a swapping mouth, driven by viseme timings generated on CPU. Demo: `path_b/overlay.html`. This is also what the live widget already does via `AVATAR_PROVIDER=photo`.
3. **Path C — neural video (later, needs a cloud GPU).** Photoreal lip-sync + head motion. Not possible on this laptop; plan a GPU host. Notes: `path_c/`.

You need: Node.js and Python already on the machine (the VIDEO project has both), one clear face image or a ~10-second clip per brand persona, and the TTS you already use for audio output.

---

## Face image prep checklist

Every path starts from one good source image. Get this right once and all three paths behave.

1. Front-facing, neutral expression. Head straight, eyes open, mouth closed or slightly open. No extreme angle or tilt.
2. Even, soft lighting. No hard shadow on one side, no blown-out highlights. Flat studio light works best.
3. High resolution, face fills the frame. At least 512×512 px on the face; 1024×1024 is better. Sharp, not blurry.
4. Plain or clean background. A solid or lightly blurred background; busy backgrounds confuse the face crop.
5. Nothing covering the face. No sunglasses, mask, hair over the eyes, or hand near the mouth.
6. One face only. Crop out anyone else in the frame.
7. Save as PNG or high-quality JPG, sRGB colour.

For Path C you also need a short reference clip (~10 s) of the same person talking naturally, same lighting — that is what gives realistic head motion.

### This folder's `face.jpg`

| Check | Result (Oct 15, 2026) |
|---|---|
| One face, front-facing | Yes |
| Lighting | Even studio; black backdrop (good) |
| File | 500×500 RGBA, saved as `.jpg` (actually PNG bytes) |
| Mouth | Slightly open / soft smile — usable; a closed-mouth reshoot animates cleaner |
| Resolution | Full frame 500 px — **below** the 512 px *on the face* target. Re-export at 1024+ if you can. |
| Covering | None. Clip-on mic on the shirt is fine (below the chin). |
| Consent | Required before a real staff likeness ships (DPDP Act 2023). Keep the signed form with the voice-clone pack. |

Run the automated check any time you drop a new portrait here:

```powershell
cd C:\Users\Galaxy\Desktop\VIDEO
uv run --with mediapipe python tran_moment\check_and_prepare.py
```

That writes `prepared/face.jpg` + `prepared/face.json` (landmarks) and `prepared/report.json`.

---

## Path A — CPU browser 3D avatar (TalkingHead)

Recommended for the widget. The 3D avatar renders in the visitor's browser, so no GPU on your server. Licence MIT (`met4citizen/TalkingHead`).

1. **Build the avatar character.** Create a Ready Player Me avatar from your face image at [readyplayer.me](https://readyplayer.me), or pick a stock one. Export the `.glb` URL — that is your "face". Paste it into `path_a/avatar.json`.
2. **Add the library.** The demo loads TalkingHead + Three.js from a CDN (no install). For the live widget, pull the same modules into the page that already hosts `galaxy-assistant.js`.
3. **Point it at the avatar.** Initialise TalkingHead with the `.glb` URL and the brand's mood/pose defaults (`path_a/index.html` already does this).
4. **Feed it audio + visemes.** When the backend returns a reply, send the TTS audio plus its viseme timings to `head.speakAudio()`. If your TTS gives word timings (Gemini/Piper can), map them to visemes; otherwise use the author's companion [HeadTTS](https://github.com/met4citizen/HeadTTS). The demo falls back to the browser voice plus a CPU viseme map so it works with no API key.
5. **Wire reactions.** Trigger `setMood('happy')`, nods and blinks on greeting / confirmation events so the face reacts, not just lip-syncs. Buttons on the demo.
6. **Test on a mid-range phone.** Rendering runs on the viewer's device — confirm it holds ~24–30fps and falls back to voice-only on a weak device.

Open the demo (needs a local web server — file:// blocks ES modules):

```powershell
cd C:\Users\Galaxy\Desktop\VIDEO\tran_moment
python -m http.server 8765
```

Then http://127.0.0.1:8765/path_a/

This replaces the current photo canvas provider with a far more lifelike face, at zero GPU cost to you. Wiring into `galaxy-assistant.js` is a separate change; keep Path B / `AVATAR_PROVIDER=photo` until the 3D path is tested on phones.

---

## Path B — 2D photo avatar on CPU (Rhubarb)

Use this if you want the actual photo of a real person rather than a 3D character. Lower fidelity, but runs on any laptop. Licence MIT. The live VIDEO widget already animates one portrait in a canvas (`AVATAR_PROVIDER=photo`); this path adds **cut mouth layers + Rhubarb timings** as an alternative.

1. **Cut mouth layers from the photo.** From the prepped face, make ~8 mouth shapes (closed, A, E, I, O, U, F/V, L) as transparent PNGs aligned to the same mouth position. `path_b/generate_mouths.py` builds a first pass from landmarks; a designer can replace the PNGs in one sitting.
2. **Install Rhubarb Lip Sync.** `path_b\install_rhubarb.ps1` downloads the CPU Windows binary — no GPU, no Python model.
3. **Generate visemes from audio.** `path_b/generate_visemes.py` runs Rhubarb over a WAV (or a letter-timed fallback if Rhubarb is not installed).
4. **Swap mouths in the widget.** `path_b/overlay.html` shows the matching mouth PNG over the photo at each timestamp while the audio plays, plus a blink and a slight head bob.
5. **Pre-generate for fixed lines.** For greetings and confirmations, run Rhubarb once and cache the viseme JSON under `path_b/cache/` so there is no per-turn cost.

Push this portrait into the **existing** widget (canvas lip-warp, no mouth PNGs):

```powershell
uv run python tran_moment\apply_to_widget.py
```

Then restart `.\run.ps1` and open http://127.0.0.1:8010/preview?site=hans

For live, free-text replies you would run Rhubarb per turn (fast on CPU for short clips) or stream word timings straight from the TTS if it provides them.

---

## Path C — neural video on a GPU host (later)

Photoreal lip-sync with real head motion. Not possible on this laptop — this needs a cloud GPU. Keep it for the custom-avatar phase. See `path_c/README.md` and the project docs (`docs/14_REALTIME_VIDEO_AVATAR.md`, `docs/08_MODEL_LICENSES.md`).

1. Rent one GPU host. An 8–16 GB NVIDIA card (cloud T4 / L4 / A10, or an RTX 3060/4090) is enough.
2. Set up the pipelines. Install MuseTalk (lip-sync) and LivePortrait (expression + head motion) — both MIT. On the GPU host, run the native PyTorch/ONNX builds, not a macOS/CoreML port. This repo's installer is `scripts/setup_livetalking_musetalk.sh`.
3. Prepare the driver. Feed the prepped face image to LivePortrait to extract its expression set (the ~few-minute per-face step). Supply the ~10 s reference clip for natural head motion (`path_c/reference_clip.txt` is the shot list).
4. Drive from audio. Send the TTS audio to MuseTalk to generate lip-synced frames, composited with LivePortrait's motion.
5. Flip the provider. Set `AVATAR_PROVIDER=livetalking` in `.env` once latency is acceptable; keep the text / voice-only fallback for when the host is down.

No part of this trains a model — it is still inference, just on a GPU.

---

## Three brands

One persona image can be shared while you test. For production, drop one source image per site:

| Brand | Folder |
|---|---|
| Galaxy Toyota | `brands/galaxy_toyota/` |
| Hans Hyundai | `brands/hans_hyundai/` |
| Auto Car Repair | `brands/autocarrepair/` |

Copy `face.jpg` into a brand folder, run `check_and_prepare.py --photo brands\<id>\face.jpg --out brands\<id>\prepared`.

---

## Common pitfalls

- Don't try to run MuseTalk/LivePortrait live on CPU. You'll get seconds per frame, not real time. CPU is only for Paths A and B, or offline pre-render.
- Don't use Wav2Lip for the dealership sites. Its licence bans commercial use (noted in `docs/08_MODEL_LICENSES.md`).
- Get consent before using a real person's face or voice. The brief requires it (DPDP Act 2023); a staff member's likeness needs signed consent, same as the voice clone (blocker B2).
- A bad source image can't be fixed downstream. Re-shoot rather than animating a blurry or badly-lit photo.
- Keep the face small in the widget. The brief is explicit: the avatar should not fill the screen — leave room for the car visuals.
- Always keep a fallback. If the avatar fails to load, drop to voice-only, then text, without breaking the conversation.
