# Learned body motion — fit report

Tracks: neha_sample, idle, small_nod, slow_nod, look_up, both_hands, open_palm, greeting, presenter

| gesture | source | dur ms | pitch° | roll° | dx % | dy % | zoom | channels kept |
|---|---|---:|---:|---:|---:|---:|---:|---|
| small_nod | clip small_nod.mp4 | 1620 | 6.337 | 2.39 | 1.746 | 1.29 | 0.011 | pitch, roll, dx, dy, zoom |
| look_up | clip look_up.mp4 | 1100 | 3.546 | 2.195 | 1.706 | 1.5 | 0.028 | pitch, roll, dx, dy, zoom |
| both_hands | clip both_hands.mp4 | 2052 | 6.68 | 3.825 | 2.09 | 1.635 | 0.031 | pitch, roll, dx, dy, zoom |
| open_palm | clip open_palm.mp4 | 3600 | 10.499 | 5.448 | 2.612 | 1.79 | 0.034 | pitch, roll, dx, dy, zoom |
| greeting | clip greeting.mp4 | 3600 | 7.779 | 3.872 | 2.144 | 2.275 | 0.014 | pitch, roll, dx, dy, zoom |
| nod | mined presenter 51.1-52.0s | 1242 | 6.0 | 6.338 | 0.823 | 0.661 | 0.014 | pitch, roll, dx, dy, zoom |
| slow_nod | mined presenter 9.6-10.8s | 1674 | 6.091 | 4.352 | 0.221 | 1.393 | 0.026 | pitch, roll, dx, dy, zoom |
| head_wobble | mined presenter 27.0-28.4s | 1890 | 3.489 | 6.533 | 0.735 | 1.818 | 0.041 | pitch, roll, dx, dy, zoom |
| tilt_head | mined greeting 1.0-1.7s | 1100 | 4.157 | 3.6 | 1.093 | 1.039 | 0.04 | pitch, roll, dx, dy, zoom |
| lean_in | mined presenter 21.7-22.8s | 1620 | 6.545 | 3.6 | 0.686 | 1.535 | 0.04 | pitch, roll, dx, dy, zoom |
| nod__v2 | variant of nod: mined presenter 53.8-54.6s | 1242 | 6.0 | 3.153 | 2.13 | 1.596 | 0.045 | pitch, roll, dx, dy, zoom |
| nod__v3 | variant of nod: mined presenter 52.7-53.6s | 1242 | 6.0 | 3.503 | 0.66 | 0.961 | 0.032 | pitch, roll, dx, dy, zoom |
| small_nod__v2 | variant of small_nod: mined neha_sample 5.4-6.1s | 1013 | 5.983 | 2.365 | 1.5 | 1.279 | 0.005 | pitch, roll, dx, dy, zoom |
| small_nod__v3 | variant of small_nod: mined small_nod 0.0-0.6s | 918 | 5.777 | 1.629 | 0.812 | 1.177 | 0.023 | pitch, roll, dx, dy, zoom |
| slow_nod__v2 | variant of slow_nod: mined idle 2.6-3.6s | 1600 | 6.0 | 1.631 | 1.5 | 1.233 | 0.013 | pitch, roll, dx, dy, zoom |
| head_wobble__v2 | variant of head_wobble: mined neha_sample 2.2-3.2s | 1407 | 5.018 | 3.815 | 1.608 | 1.313 | 0.03 | pitch, roll, dx, dy, zoom |
| head_wobble__v3 | variant of head_wobble: mined presenter 43.9-44.8s | 1400 | 1.817 | 2.492 | 0.664 | 0.569 | 0.045 | pitch, roll, dx, dy, zoom |
| tilt_head__v2 | variant of tilt_head: mined neha_sample 4.2-5.0s | 1238 | 4.382 | 3.6 | 1.5 | 1.511 | 0.012 | pitch, roll, dx, dy, zoom |
| look_up__v2 | variant of look_up: mined idle 0.7-1.9s | 1620 | 6.0 | 2.163 | 1.5 | 1.116 | 0.04 | pitch, roll, dx, dy, zoom |
| idle | rest | 800 |  |  |  |  |  | — |
| pause | rest | 600 |  |  |  |  |  | — |
| freeze | rest | 120 |  |  |  |  |  | — |

## Idle statistics

```json
{
  "micro_std": {
    "pitch": 1.012,
    "roll": 0.546,
    "dx": 0.285
  },
  "retarget_s": {
    "mean": 5.5,
    "std": 1.3,
    "note": "replaced: talking footage"
  },
  "blink_s": {
    "mean": 4.0,
    "std": 1.2,
    "dur": 0.14,
    "count": 7,
    "note": "replaced: talking footage"
  },
  "breath_period_s": 4.6,
  "calm_seconds": 4.0,
  "calm_windows_by_source": {
    "neha_sample": 1,
    "idle": 1,
    "presenter": 2
  },
  "note": "quietest clean windows of a talking presenter, not true rest; values are clamped in avatar.js | calm profile: drift halved, blink/retarget set to resting values"
}
```

Ranges are peak-to-peak after rest-to-rest detrending and clamping (roll ≤ 3.6°, zoom −0.005..0.04).
A channel below the noise floor (pitch 0.6°, roll 0.5°, dx/dy 0.15 %, zoom 0.004) is dropped from that gesture.
