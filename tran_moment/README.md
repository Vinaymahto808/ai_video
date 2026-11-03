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

## Expression grid (2026-11-03): the preferred source

One rendered image with 12 expressions of the same person (6 x 2 cells: neutral, soft smile, big smile,
"ah", talking E, surprised, "oh", talking I, glance aside, warm smile, eyes closed, wink), e.g.
`voice_clone/src/expressions_grid.png`:

```
python tran_moment/path_b/expressions_from_grid.py --grid voice_clone/src/expressions_grid.png
python tran_moment/path_b/generate_reactions.py      # brows_up/in + eyes_half warps on the new base
python tran_moment/path_b/expressions_from_grid.py --grid voice_clone/src/expressions_grid.png   # again: restores the grid smile.png
```
Cell 0 becomes the base portrait; every other cell is aligned on six landmarks and colour-matched to it. The
talking cells become the speech mouth shapes (real lips, not warps); smiles/surprise become whole-face layers
and glance/eyes-closed/wink eyes-only layers (`path_b/expressions/`, `expressions.json`, review sheet
`expressions_sheet.png`). The card fades expressions over 250 ms (`setReaction` in web/embed.html): warm ->
smile_warm, enthusiastic -> smile_big, question -> surprised, listening -> soft smile + glances, thinking ->
glance. For a sharper result generate the grid at a higher resolution (cells are 296x444 here, upscaled 2.2x).

### Morph frames (smooth expression changes)

```
python tran_moment/path_b/morph_frames.py     # after expressions_from_grid.py; -> path_b/expressions/<name>/fNN.webp
```
For every expression the 478 face landmarks of the base and of the expression are interpolated and both
images are piecewise-affine warped (Delaunay triangles) to each in-between shape, then blended: one frame
per ~1.5 px of the largest landmark move (6-24 frames, ~1 MB of WebP in total). The card plays the
sequence forward to show an expression (~260 ms) and backward to relax it; a blink is eyes_closed forward
(70 ms) then back (90 ms); a new expression waits for the previous one to relax, so the face never jumps.

### Training body motion from a video of the current face (2026-11-03)

`voice_clone/src/sample.mp4` (10 s, AI-rendered, this Neha talking to camera) is registered as the
`neha_sample` source in `motion/extract_motion.py`:

```
python tran_moment/motion/extract_motion.py --only neha_sample   # MediaPipe pose curves -> motion/tracks/neha_sample.json
python tran_moment/motion/fit_gestures.py                        # gestures + variants + idle stats -> avatar_widget/src/motion-data.js
python tran_moment/motion/export_css.py                          # same curves as CSS keyframes -> human_bot/web/learned_gestures.css
```
The card loads `learned_gestures.css` after its hand-written keyframes, so every gesture class
(`g-nod`, `g-tilt`, `g-wobble`, ...) now plays a curve learned from footage, with up to six takes per
gesture chosen at random. More footage of her (longer, calmer, no cuts) improves the idle statistics most.
