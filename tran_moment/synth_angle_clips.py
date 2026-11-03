"""More reaction clips for clips_neha: every angle and mood a service advisor shows on a video call.

Built from the same kit as synth_face_clips.py plus the pose / gaze warps (path_b/reactions): head turned
left / right, lowered, raised, leaning in; eyes left / right / up / half-closed; brows up / in; the smile
set; and the learned head tracks. Calm timing throughout (slow ramps, one blink, small gestures).

    python tran_moment/synth_angle_clips.py            # -> prepared/clips_neha/<name>.mp4 + manifest
    python tran_moment/synth_angle_clips.py --only look_left head_shake

make_clips.py lists each new clip under its own key and as an extra take of the nearest persona gesture,
so the card rotates them without any persona change.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("synth_face_clips", ROOT / "synth_face_clips.py")
sfc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sfc)
sc = sfc.sc

CALM = {"EXPR_IN": 0.45, "EXPR_OUT": 0.40, "GLANCE_IN": 0.24, "GLANCE_OUT": 0.28, "BROW_RAMP": 0.30,
        "MOUTH_FADE": 0.09, "IDLE_SCALE": 0.6}
R = sc.REST_S


def S(name, dur, *, track=None, nods=(), blinks=(), expr=(), glances=(), brows=(), poses=(), gazes=(), rest_mouth="rest"):
    s = sfc.Script(name, track, dur=dur, blinks=blinks, expr=expr, glances=glances, brows=brows, rest_mouth=rest_mouth,
                   pingpong=True, nods=nods)
    s.poses, s.gazes = poses, gazes
    return s


def scripts() -> list:
    # (name, duration) -> head track(s) with amplitude, pose warp, gaze, brows, expression. All end at rest.
    return [
        # ---- where she looks
        S("look_left", 3.4, gazes=(("gaze_left", 0.5, 1.4),), nods=((0.45, "tilt_head", 0.5, 1.4),), blinks=(2.9,),
          expr=(("smile_soft", 0.2, 2.6),)),
        S("look_right", 3.4, gazes=(("gaze_right", 0.5, 1.4),), nods=((0.45, "tilt_head__v2", -0.5, 1.4),), blinks=(2.9,),
          expr=(("smile_soft", 0.2, 2.6),)),
        S("look_down", 3.2, poses=(("head_down", 0.4, 1.3),), nods=((0.4, "small_nod", 0.6, 1.8),), blinks=(0.3, 2.7),
          brows=(("brows_in", 0.5, 1.0),)),
        S("look_up_long", 3.8, poses=(("head_up", 0.4, 1.8),), gazes=(("gaze_up", 0.5, 1.7),), brows=(("brows_up", 0.5, 1.7),),
          blinks=(0.25, 3.3)),
        # ---- head turned: roll + slide from the learned tilt track with the eyes leading the turn
        # (the kit's head_tilt_l/r pose warps rotate the whole frame and expose white corners, so they are not used)
        S("turn_left", 3.6, nods=((0.4, "tilt_head", 1.0, 1.8), (0.5, "look_up__v2", 0.25, 1.8)), gazes=(("gaze_left", 0.5, 1.5),),
          blinks=(3.0,), expr=(("smile_soft", 0.3, 2.8),)),
        S("turn_right", 3.6, nods=((0.4, "tilt_head__v2", -1.0, 1.8), (0.5, "look_up", 0.25, 1.8)), gazes=(("gaze_right", 0.5, 1.5),),
          blinks=(3.0,), expr=(("smile_soft", 0.3, 2.8),)),
        S("lean_close", 3.6, poses=(("lean_in", 0.4, 1.8),), nods=((0.4, "lean_in", 0.6, 1.4),), expr=(("smile_warm", 0.4, 2.4),),
          brows=(("brows_up", 0.5, 0.6),), blinks=(2.9,)),
        # ---- agreement / disagreement
        S("agree", 3.2, nods=((0.4, "nod", 0.8, 1.2), (1.6, "small_nod__v2", 0.6, 1.2)), expr=(("smile_warm", 0.2, 2.6),), blinks=(2.6,)),
        S("head_shake", 3.6, nods=((0.4, "head_wobble", 0.8, 1.3), (1.7, "head_wobble__v3", -0.7, 1.3)), brows=(("brows_in", 0.3, 2.4),),
          gazes=(("gaze_left", 0.4, 0.5), ("gaze_right", 1.5, 0.5)), blinks=(3.0,)),
        # ---- moods
        S("concerned", 3.8, nods=((0.4, "tilt_head", 0.6, 2.0), (1.9, "slow_nod", 0.5, 1.4)), brows=(("brows_in", 0.3, 2.9),),
          blinks=(0.3, 3.2), poses=(("head_down", 1.6, 1.2),)),
        S("doubt", 3.6, nods=((0.4, "tilt_head__v2", -0.6, 1.8),), brows=(("brows_in", 0.4, 1.4), ("brows_up", 2.0, 0.8)),
          gazes=(("gaze_up", 0.5, 1.0),), blinks=(2.9,)),
        S("apologetic", 4.0, poses=(("head_down", 0.4, 2.2),), nods=((0.5, "slow_nod", 0.6, 1.5),), brows=(("brows_in", 0.3, 2.8),),
          blinks=(0.4, 3.4), expr=(("smile_soft", 2.4, 1.0),)),
        S("thank", 3.8, nods=((0.5, "slow_nod", 0.7, 1.4),), expr=(("smile_warm", 0.2, 3.0),), blinks=(0.9, 3.2),
          gazes=(("eyes_half", 0.9, 0.5),)),
        S("laugh", 3.8, expr=(("smile_big", 0.3, 2.6),), nods=((0.5, "small_nod", 0.6, 1.0), (1.3, "small_nod__v3", 0.6, 1.0)),
          gazes=(("eyes_half", 0.6, 1.6),), poses=(("head_up", 0.4, 1.4),), blinks=(3.2,)),
        S("attentive", 4.0, poses=(("lean_in", 0.3, 2.6),), expr=(("smile_soft", 0.2, 3.2),), nods=((1.4, "small_nod__v2", 0.5, 1.4),),
          glances=((2.3, 0.4),), blinks=(0.8, 3.4)),
        S("surprised_up", 3.6, expr=(("surprised", 0.4, 1.2),), poses=(("head_up", 0.4, 1.4),), brows=(("brows_up", 0.3, 1.6),),
          nods=((2.2, "small_nod", 0.5, 1.2),), blinks=(2.9,)),
        S("relax", 4.2, nods=((1.0, "slow_nod__v2", 0.4, 1.8),), gazes=(("eyes_half", 1.2, 1.2),), expr=(("smile_soft", 0.3, 3.4),),
          blinks=(0.6, 3.6)),
    ]


def main(argv: list[str]) -> int:
    only = set(argv[argv.index("--only") + 1:]) if "--only" in argv else None
    for k, v in CALM.items():
        setattr(sfc, k, v)
    tracks, idle = sc.load_tracks()
    kit = sfc.Kit()
    spec = importlib.util.spec_from_file_location("make_clips", ROOT / "make_clips.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    mk.OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        for s in scripts():
            if only and s.name not in only:
                continue
            missing = [g[1] for g in s.nods if g[1] not in tracks]
            if missing:
                print(f"skip {s.name}: tracks missing {missing}")
                continue
            raw = Path(td) / f"{s.name}.mp4"
            n = sfc.render(s, kit, tracks, idle, raw)
            dst = mk.OUT / f"{s.name}.mp4"
            mk.normalise(raw, dst, pingpong=True)
            print(f"{s.name:14s} {n:4d} frames -> {dst.relative_to(ROOT)} ({dst.stat().st_size // 1024} KB)")
    m = mk.write_manifest()
    print("manifest:", json.dumps({"states": {k: len(v) for k, v in m["states"].items()},
                                   "gestures": {k: len(v) for k, v in m["gestures"].items()}}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
