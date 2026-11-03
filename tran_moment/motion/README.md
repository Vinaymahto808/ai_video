# motion — body motion learned from footage (CPU, no model training)

The puppet avatar in `avatar_widget/src/avatar.js` moves a still portrait with five channels
(pitch, roll, dx, dy, zoom). Until 2026-11-02 every gesture was a hand-typed keyframe table. This
folder replaces that table with curves **measured from reference footage** and statistics for the
idle state. Nothing here trains a neural network: it is landmark extraction plus curve fitting, and
it runs on this laptop in under two minutes.

```
tran_moment/motion/
  extract_motion.py   MediaPipe Face Landmarker → per-frame pitch/yaw/roll, nose travel, scale, blink, jaw
  fit_gestures.py     tracks → keyframes per gesture + idle statistics → avatar_widget/src/motion-data.js
  models/             face_landmarker.task (MediaPipe float16, downloaded 2026-11-02)
  tracks/             one JSON per source (generated)
  report.md           per-gesture provenance and ranges (generated)
```

## Run

```powershell
cd C:\Users\Galaxy\Desktop\VIDEO
python -m pip install mediapipe opencv-python-headless      # once; mediapipe 1.2.0 was used
python tran_moment\motion\extract_motion.py                  # ~1 min; writes tracks/*.json
python tran_moment\motion\fit_gestures.py                    # writes avatar_widget/src/motion-data.js + report.md
node --test avatar_widget\test\unit\avatar-motion.test.mjs   # data checks
node avatar_widget\test\motion.mjs                           # headless render checks (frame cover, mouth, poses)
```

## Sources and what came out

| gesture | source | how |
|---|---|---|
| small_nod, look_up, both_hands, open_palm, greeting | `prepared/clips/<name>.mp4`, first half of each ping-pong loop | whole clip fitted, passed its semantic rule |
| slow_nod | mined from the presenter segment (9.6–10.8 s) | the `slow_nod.mp4` clip lifts the chin, so it failed the chin-down rule and was replaced |
| nod, head_wobble, lean_in | clean windows mined from the 0–55 s presenter segment of `sample_audio.mp4` | feature rule (biggest clean nod, strongest roll alternation, biggest move toward camera) |
| tilt_head | clean window mined from `greeting.mp4` (1.0–1.7 s) | longest held tilt |
| idle, pause, freeze | rest (no keyframes) | idle motion comes from `IDLE_STATS` |

Each name has a semantic rule (nods pitch chin-down, look_up chin-up, head_wobble rolls both
ways, tilt_head holds a roll, lean_in/both_hands move toward the camera). A clip or mined window
that breaks its rule is never shipped under that name; `GESTURE_SOURCES` records what replaced it.

Fitting: 3-frame smoothing, linear detrend so every track starts and ends at exact rest, clamp to
the frame budget (roll ±3.6°, pitch ±6°, travel ±1.5 %, zoom −0.005..0.04) by scaling the whole
channel so the shape survives, then Ramer–Douglas–Peucker down to at most 7 interior keyframes.
A channel that moves less than the noise floor is dropped from that gesture. `avatar.js` merges
`LEARNED_GESTURES` over its hand-authored table, so a name without footage still has a track, and
`gestureOrigin(name)` reports which one is in use.

Idle statistics (`IDLE_STATS`) are robust (MAD) measures from the quietest clean 2 s windows.
**Caveat:** the presenter talks in every clip, so these are a talking baseline, not true rest, and
only ~3 s qualified. `avatar.js` clamps each value (drift amplitude, retarget interval, blink
interval/duration, breathing period) to a sane range before use. A 20–30 s clip of someone sitting
quietly in frame would replace these numbers properly: drop it in, add it to `SOURCES` in
`extract_motion.py`, re-run both scripts.

## Quality gates that mattered

- The wide shot's face is ~33 px tall, below what BlazeFace finds in a full frame. `extract_motion.py`
  tracks a square crop around the last face (sweeping when lost); detection went from 0 % to 68 %.
- Even so the wide shot jitters. A window is only used if it has no misses, a face ≥ 24 px, no
  per-frame jump over 4°, and plausible travel (≤ 15° pitch/roll, ≤ 8 % width). Without these gates
  three gestures landed in one jittery 48 s burst.
- Signs were checked against the footage: the nod clip must go chin-down positive, the look-up clip
  negative, and roll is flipped to CSS `rotate()` convention (clockwise positive).

## Likeness and consent

Only pose curves (angles, percentages, blink/jaw scalars) are written. No frame, crop, landmark
mesh or embedding of the presenter leaves the extractor, and the generated module carries no
biometric identifier. The footage itself remains TEST-ONLY under `prepared/IDLE_SOURCE.md` and
`CONSENT.md`; this process does not change that status, and the rendered portrait is still the
CGI Neha image.
