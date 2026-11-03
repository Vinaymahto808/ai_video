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

Synthesized from `prepared/face.jpg` (the CGI Neha portrait) with ffmpeg motion only: breathing zoom
(idle), vertical oscillation (small_nod / slow_nod), upward shift + tilt (look_up), zoom-in (both_hands /
lean_in), tilt (open_palm), zoom + settling tilt (greeting). Same file names and manifest keys as `clips/`.
The card uses this set unless the embed tag says `data-clips="sample"` (or the card URL has `?clips=sample`).
Replace files one by one with talking-head renders when a GPU/API path is available.

## clips_neha_render/ — real motion (made on Colab, not on this laptop)

`tran_moment/path_c/liveportrait_reactions_colab.ipynb` + `liveportrait_inputs.zip` render the portrait with LivePortrait,
driven by the seven presenter clips: Neha's own face then nods, blinks, looks up and moves her mouth the way the presenter
did. Unzip the result here; the card uses it with `?clips=neha_render` (or `data-clips="neha_render"`). Live lip-sync to
TTS still needs a real-time renderer (MuseTalk/LiveTalking on a GPU host, or a Tavus/HeyGen/D-ID stream).
