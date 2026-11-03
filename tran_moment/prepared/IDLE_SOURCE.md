# idle.mp4 — LOCAL TEST ASSET ONLY

- Source: `tran_moment/sample_audio.mp4` (Galaxy Toyota showroom walkthrough), shot 00:01:19.7–00:01:23.5,
  portrait crop 202x360 at x=204, upscaled 2x (406x720), forward+reverse ping-pong, audio removed.
- Person: the female presenter (00:00–00:55 speaker). **No consent record exists for her face.**
  `voice_clone/consent/consent.json` names only Yash Narula (00:56–01:28) and is still an unsigned template.
- Therefore this file must NOT ship on autocarrepair.in / galaxytoyota.in / hanshyundai.com (DPDP Act 2023,
  see `tran_moment/CONSENT.md`). It exists to exercise the `<video>` idle path of `human_bot/web/embed.html`
  at 127.0.0.1. Replace with a rendered Neha loop (LivePortrait / HeyGen from `prepared/face.jpg`) or obtain
  her signed consent before any deployment. Delete this file to fall back to CSS motion on the still.

## clips/ — reaction set (same source, same TEST-ONLY status)

| clip | source shot | used for |
|---|---|---|
| idle.mp4 | 1:19.7–1:23.5 counter | state idle / gestures idle, pause |
| small_nod.mp4 | 1:22.3–1:23.5 (0.85×) | state listening (mic on, backchannel) / small_nod, nod |
| slow_nod.mp4 | 1:20.0–1:21.5 (0.7×) | slow_nod, tilt_head (concerned) |
| look_up.mp4 | 1:17.2–1:18.0 seated, glance away (0.8×) | state thinking / look_up |
| both_hands.mp4 | 1:18.0–1:19.5 seated, hand gestures | both_hands, lean_in (enthusiastic) |
| open_palm.mp4 | 0:44.3–0:47.2 by the car | open_palm, nod (warm) |
| greeting.mp4 | 0:10.5–0:13.5 outdoors (tight crop, soft) | state greeting |

`manifest.json` maps persona gesture names → clips; `human_bot/persona/*.json → avatar.gestures` maps emotion → gesture.
Replace every file with Neha-rendered equivalents of the same names before any deployment.

## clips_neha/ — the default set (no likeness issue)

Rebuilt 2026-11-03 from the current `prepared/face.jpg` (AI Neha portrait, white border cropped) by
`tran_moment/synth_clips.py`: the portrait is moved by Neha's own learned gesture tracks
(`avatar_widget/src/motion-data.js`, fitted from footage by `motion/fit_gestures.py`) on top of idle
breathing and micro-drift, rendered with OpenCV affine warps on CPU, then normalised and ping-ponged by
`tran_moment/make_clips.py` (720x960, 24 fps, muted, h264). 21 files: idle, small_nod (+2 takes), nod (+2),
slow_nod (+1), tilt_head (+1), head_wobble (+2), look_up (+1), open_palm, both_hands, lean_in, greeting,
listening. `manifest.json` is written by make_clips.py (set "neha"; `backchannel` state → head_wobble).
Face motion added the same afternoon by `tran_moment/synth_face_clips.py`: blinks (eyes_closed morph), glances,
brows_up / brows_in, smile_soft / smile_warm / smile_big / surprised expression sequences and real-lip visemes
from the puppet kit (`path_b/expressions.json`, `reactions.json`, `mouths.json`), composited in the card's order
before the head motion. Extra clips: `surprised.mp4` (question) and `talking.mp4` (9.7 s, lips driven by the
Rhubarb cues of `path_b/sample.wav`, not ping-ponged; manifest state `speaking`). Re-run
`python tran_moment/synth_face_clips.py` after a portrait change, a new expression grid or a motion refit
(`synth_clips.py` alone gives head motion without the face layers).

Conversation clips (`tran_moment/synth_talk_clips.py`, 2026-11-03): seven `talk_*.mp4` lines of a service advisor
(greeting, ask, explain, reassure, price, booking read-back, close), voice = Edge hi-IN Swara via the project's
Devanagari transliteration with per-emotion prosody, lips = Edge word boundaries + a letter map (Rhubarb is used
instead when `path_b/tools/rhubarb.exe` exists; the installer download was not run here), plus blinks, a glance
in long pauses, brows up on the question, smiles by emotion and a small nod at every phrase break. Audio is
muxed in (AAC) so each file is a standalone demo; the card mutes clips and speaks live TTS. The mp3 and viseme
JSON per line are kept in `clips_neha/audio/`. Manifest state `speaking` lists them. Lines live in LINES at the
top of the script: edit, re-run. Delivery is deliberately calm (CALM profile + slower TTS rate, polite aap/ji
register) after the 2026-11-03 review that the first version moved too fast.

Angle and mood clips (`tran_moment/synth_angle_clips.py`, 2026-11-03): 17 more loops so the card has every
direction and mood: look_left / look_right / look_down / look_up_long, turn_left / turn_right, lean_close,
agree, head_shake, concerned, doubt, apologetic, thank, laugh, attentive, surprised_up, relax. Built from the
gaze warps (gaze_left/right/up, eyes_half), the head_down / head_up / lean_in pose warps blended in and out,
brows, the smile set and the calm learned tracks. The head_tilt_l/r pose warps are not used (they rotate the
whole frame and expose white corners); turns use the tilt track with the eyes leading. make_clips.py lists each
under its own key and as extra takes of the nearest persona gesture (tilt_head, look_up, slow_nod, nod ...).

Curated 2026-11-03 (AIVA-style brief, see research/aiva/FEATURES.md): the numbered head-only takes, the old
`talking.mp4`, head_shake, laugh, doubt, surprised_up, look_up_long, turn_left/right and the stray presenter
folder were deleted; 30 clips remain. `clips_neha/demo/aiva_style_call.mp4` (99 s, audio) strings the seven
spoken lines with listening / agree / relax loops in between, the way AIVA's agent speaks then listens on a
video call; `demo/welcome.mp4` is the greeting alone (AIVA shows a welcome video on the launcher). Real image-to-video renders (Kling/Runway from `ref_720x1280.png` + CLIP_PROMPTS.md)
can replace files one by one via `python tran_moment/make_clips.py <raw_dir>`.

## clips_neha_render/ — real motion (made on Colab, not on this laptop)

`tran_moment/path_c/liveportrait_reactions_colab.ipynb` + `liveportrait_inputs.zip` render the portrait with LivePortrait,
driven by the seven presenter clips: Neha's own face then nods, blinks, looks up and moves her mouth the way the presenter
did. Unzip the result here; the card uses it with `?clips=neha_render` (or `data-clips="neha_render"`). Live lip-sync to
TTS still needs a real-time renderer (MuseTalk/LiveTalking on a GPU host, or a Tavus/HeyGen/D-ID stream).
