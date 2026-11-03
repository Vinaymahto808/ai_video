# Video-clip prompts for Neha (image-to-video)

Source image for every clip: `tran_moment/prepared/face.jpg` (the neutral cell of the expression grid). Use
it as the **first frame** in an image-to-video tool, and where the tool allows it also as the **last frame**
(Kling, Luma, Pika "start + end frame"): same image at both ends = a perfect loop with no cut.

The card plays these clips muted, crossfading between them (`manifest.json`, 350 ms). The voice is separate
(TTS), so **the lips must not talk in any clip**: mouth closed or a relaxed closed-lip smile, no words.

## Technical settings (same for every clip)

- Aspect 3:4 portrait (e.g. 768×1024 or 1080×1440), 24 or 30 fps, 4 to 5 s, no audio.
- Camera locked, no zoom, no pan, no cut, no lighting change. Background stays the plain light blue.
- Framing exactly as the source: head and shoulders, eyes a third of the way down, navy blazer, cream shirt.
- Start and end on the same neutral pose (needed for the ping-pong loop).
- Motion small and slow: real people on a video call move a few degrees, not a lot.

## Shared identity and style block (paste at the start of every prompt)

```
Same woman as the reference image, identical face, hair, earrings, navy blazer and cream shirt, plain
light-blue studio background, soft even front lighting, 3:4 portrait, head and shoulders, locked camera,
photorealistic video-call look, natural skin, no text, no logos, no extra people, mouth closed (she is not
speaking), subtle natural micro-movement only.
```

## Negative prompt (every clip)

```
talking, lip movement, speech, open mouth, teeth, laughing, hands covering face, camera zoom, camera pan,
cut, flicker, morphing, face change, different person, text, watermark, logo, blur, distortion, extra
fingers, cartoon, exaggerated expression, fast motion, hair flying, background change, lighting change
```

## The clips (file name → prompt)

| File | What the card uses it for | Prompt (after the shared block) |
|---|---|---|
| `idle.mp4` | resting state, also `pause` | She sits calmly looking at the camera, breathing gently, a slow blink around the middle, the head drifts a couple of degrees and settles back, a faint friendly resting smile with closed lips. Nothing else moves. |
| `listening.mp4` (use as `small_nod.mp4`) | listening to the customer | She listens attentively, head tilted a few degrees to one side, eyes on the camera, one small slow nod of agreement, closed-lip smile, a blink, then back to the neutral pose. |
| `small_nod.mp4` | neutral agreement | One small, quick nod of the head (chin down about two degrees and back), eyes stay on the camera, closed lips, otherwise still. |
| `nod.mp4` | warm agreement | One clear friendly nod, slightly deeper than a small nod, with a warm closed-lip smile that fades back to neutral at the end. |
| `slow_nod.mp4` | concern / understanding | One slow, gentle, understanding nod lasting about two seconds, eyebrows soften, eyes stay on the camera, closed lips, ends in the neutral pose. |
| `tilt_head.mp4` | concern | The head tilts about five degrees to the left over one second, holds with a soft concerned look (brows very slightly drawn in), then returns upright. Closed lips. |
| `head_wobble.mp4` | backchannel "haan ji" (Indian agreement) | A gentle Indian head wobble: the head rocks side to side twice, small and relaxed, with a warm closed-lip smile and bright eyes, then settles. |
| `look_up.mp4` | thinking | She glances up and slightly to the side as if recalling something, holds for a second with a thoughtful look, then looks back at the camera with a small closed-lip smile. |
| `open_palm.mp4` | warm explanation | A small welcoming gesture: one hand rises into the bottom of the frame with an open palm, a short friendly motion, then lowers out of frame; closed-lip smile; the head barely moves. |
| `both_hands.mp4` | enthusiastic | Both hands come up briefly into the lower frame in a light, enthusiastic "come, let me help" gesture, eyes bright, closed-lip smile, hands lower again; the framing of the face does not change. |
| `lean_in.mp4` | enthusiastic / interest | She leans a few centimetres toward the camera with interest, eyes bright, slight smile, then eases back to the resting pose. No zoom, only her body moves. |
| `greeting.mp4` | first seconds of the call | A warm welcome: a soft smile grows, one small nod, eyes bright and on the camera, then the smile relaxes to the resting face. Closed lips throughout. |

Generate two or three takes of the frequent ones (`idle`, `small_nod`, `nod`, `listening`) so the card can
rotate them (`manifest.json` lists several files per key; no immediate repeats).

## Recommended tools and settings

- **Kling 2.x** (image-to-video, "start frame" + "end frame" = the same image, mode Professional, 5 s,
  creativity low / relevance high). Best loops.
- **Runway Gen-4 Turbo** (image + prompt, 5 s, motion 2-3 of 10, "camera static").
- **Luma Dream Machine / Ray** (start + end keyframe = the same image, loop option on).
- **Google Veo 3** (image-to-video; ask for "no audio, mouth closed"; strongest realism, no end-frame).
- Avoid "talking avatar" tools (HeyGen, D-ID, Synthesia) for these clips: they move the lips.

## After generation: normalise and install

```
# one clip: 3:4, 24 fps, no audio, h264 that every browser plays, ping-pong so it loops without a cut
ffmpeg -i in.mp4 -an -vf "scale=720:960:force_original_aspect_ratio=increase,crop=720:960,fps=24" -c:v libx264 -pix_fmt yuv420p -crf 20 -movflags +faststart tmp.mp4
ffmpeg -i tmp.mp4 -filter_complex "[0:v]reverse[r];[0:v][r]concat=n=2:v=1:a=0" -c:v libx264 -pix_fmt yuv420p -crf 20 -movflags +faststart tran_moment/prepared/clips_neha/small_nod.mp4
```
Then list the files in `tran_moment/prepared/clips_neha/manifest.json` under the right keys (copy the
structure of `prepared/clips/manifest.json`; add `"head_wobble"` under `gestures` and `"backchannel"` under
`states` if you generated it). Open the card with `?clips=neha_video` to play the clips instead of the puppet:
`http://127.0.0.1:4001/embed?persona=auto-car-repair&clips=neha_video` (the launcher tag: `data-clips="neha_video"`).
