# `tran_moment` — face avatar kit

Working files for the **Face Avatar Setup Guide** (three-brand AI Video Sales Assistant). Start with [`FACE_AVATAR_SETUP.md`](FACE_AVATAR_SETUP.md).

| File | What it is |
|---|---|
| `face.jpg` | Source portrait (one face, studio, black backdrop) |
| `check_and_prepare.py` | Quality check + MediaPipe crop → `prepared/` |
| `apply_to_widget.py` | Copy prepared face into the live widget (`app/static/avatar/`) |
| `path_a/` | CPU 3D TalkingHead demo (browser, no server GPU) |
| `path_b/` | CPU 2D photo + mouth layers + Rhubarb visemes |
| `path_c/` | GPU path notes (LiveTalking / MuseTalk / LivePortrait) — later |
| `brands/` | One folder per site if you use different personas |

Do not train anything on this laptop. Point a pipeline at the photo and drive it from TTS audio.

## Changing the portrait (2026-11-03)

```
python tran_moment/prepare_portrait.py --photo <photo.png>   # -> prepared/face.jpg (640x852) + face.json (MediaPipe, CPU)
python tran_moment/path_b/generate_mouths.py                 # -> path_b/mouths/*.png + mouths.json for the card's puppet
```
`human_bot` serves `prepared/face.jpg` as `/avatar.jpg` (card, launcher bubble, voice widget) and the puppet
assets as `/avatar_puppet/*`; no restart needed for the files. The voice widget reads `face.json` to place its
blink band and mouth patch. The **Simli live face** is a separate asset on the Simli account: generate a new
face from the same photo in the Simli dashboard and set `SIMLI_FACE_ID`; until then run the bot with
`HUMAN_BOT_AVATAR_PROVIDER=puppet`, or the old face would be composited over the new portrait. The previous
Neha puppet assets are kept in `path_b/mouths_old_neha/`. Consent rule: only a synthetic or consented face.

## Reaction frames (2026-11-03)

```
python tran_moment/path_b/generate_reactions.py    # -> path_b/reactions/*.png, reactions.json, mouths/smile.png, reactions_sheet.png
```
Warps of the prepared portrait, no model: eyes (eyes_closed, eyes_half for blinks; gaze_left/right/up),
brows (brows_up, brows_in), the smiling rest mouth, and whole-frame poses (head_tilt_l/r, head_down, head_up,
lean_in). The card's puppet (`web/embed.html` setReaction/puppetDraw) composes base → brows → eyes → mouth:
thinking = gaze up + brows up; listening = glances aside every 4-8 s; a question or an enthusiastic line raises
the brows, a concerned line draws them in; a warm mood rests on the smile. Review all frames in
`path_b/reactions_sheet.png`. Re-run after every portrait change (after generate_mouths.py).
