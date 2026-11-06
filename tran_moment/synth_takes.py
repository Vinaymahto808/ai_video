"""Render extra TAKES of every Neha reaction clip (name_2.mp4, name_3.mp4 ...) so the card never loops the
same few seconds: each take uses another learned variant of the gesture (nod__v2, nod__v3 ... mined from
footage by motion/fit_gestures.py), its own blink / glance / expression timing, an idle-drift phase of its
own and a slightly calmer amplitude. make_clips.key_of() strips the _N suffix, so every take joins the same
manifest key and the card's no-immediate-repeat picker rotates them without persona edits.

Timing is the live puppet's calm register (expression in/out 0.42/0.36 s, glances 0.22/0.26 s), not the
faster defaults of synth_face_clips.py, so a take and the live face move at the same pace.

    python tran_moment/synth_takes.py                 # 2 extra takes of every reaction loop (~3 min CPU)
    python tran_moment/synth_takes.py --takes 4       # takes _2.._4
    python tran_moment/synth_takes.py --only nod idle --seed 7

Deterministic for a given --seed. The `talking` clip is skipped (its lips follow one fixed audio line;
spoken clips come from synth_talk_clips.py).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("synth_face_clips", ROOT / "synth_face_clips.py")
sf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sf)
sc = sf.sc

CALM = {"EXPR_IN": 0.42, "EXPR_OUT": 0.36, "GLANCE_IN": 0.22, "GLANCE_OUT": 0.26, "BROW_RAMP": 0.2}


def variants(tracks: dict, base: str) -> list[str]:
    """every learned take of a gesture track: nod, nod__v2, nod__v3 ..."""
    root = base.split("__")[0]
    return sorted(t for t in tracks if t == root or t.startswith(root + "__v"))


def clip_dur(script, tracks: dict) -> float:
    return script.dur or sc.REST_S * 2 + tracks[script.track]["dur"] / 1000.0


def make_take(s, k: int, tracks: dict, rng: random.Random):
    """a copy of Script `s` with another gesture variant and jittered event timing"""
    track = s.track
    if track:
        vs = variants(tracks, track)
        others = [v for v in vs if v != track]
        if others:
            track = others[(k - 2) % len(others)]
    t = sf.Script(s.name, track, dur=s.dur, rest_mouth=s.rest_mouth, pingpong=s.pingpong)
    old = clip_dur(s, tracks)
    new = clip_dur(t, tracks)
    R = sc.REST_S
    ratio = new / old if old else 1.0

    def at(x: float, j: float = 0.0) -> float:          # rescale a time to the new track length, then jitter
        x = R + (x - R) * ratio if x > R else x
        return round(min(max(0.15, x + rng.uniform(-j, j)), new - 0.25), 3)

    t.blinks = tuple(sorted(at(b, 0.4) for b in s.blinks))
    if not t.blinks or new - t.blinks[-1] > 2.8:             # long clip: one more blink so the eyes never stare
        t.blinks += (at(new - 0.9, 0.3),)
    t.glances = tuple((at(g, 0.35), round(h * rng.uniform(0.85, 1.2), 2)) for g, h in s.glances)
    t.expr = tuple((n, at(st, 0.08), round(h * ratio, 2)) for n, st, h in s.expr)
    t.brows = tuple((n, at(st, 0.08), round(h * ratio, 2)) for n, st, h in s.brows)
    t.winks, t.nods = s.winks, s.nods
    return t


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--takes", type=int, default=3, help="highest take number to render (default 3: _2 and _3)")
    ap.add_argument("--only", nargs="*", help="clip names (default: every reaction loop)")
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args(argv[1:])
    for k, v in CALM.items():
        setattr(sf, k, v)
    tracks, idle = sc.load_tracks()
    cues = json.loads((sf.PB / "cache" / "sample.visemes.json").read_text(encoding="utf-8"))["mouthCues"]
    kit = sf.Kit()
    spec = importlib.util.spec_from_file_location("make_clips", ROOT / "make_clips.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    rng = random.Random(a.seed)
    done = 0
    with tempfile.TemporaryDirectory() as td:
        for s in sf.scripts(tracks, cues):
            if s.mouth or (a.only and s.name not in a.only):
                continue
            for k in range(2, a.takes + 1):
                take = make_take(s, k, tracks, rng)
                sf.IDLE_PHASE = rng.uniform(1.5, 9.0)
                sf.GESTURE_AMP = rng.uniform(0.85, 1.0)
                raw = Path(td) / f"{s.name}_{k}.mp4"
                n = sf.render(take, kit, tracks, idle, raw)
                dst = mk.OUT / f"{s.name}_{k}.mp4"
                mk.normalise(raw, dst, pingpong=take.pingpong)
                done += 1
                print(f"{dst.name:18s} {n:4d} frames  track={take.track or '-':16s} amp={sf.GESTURE_AMP:.2f} "
                      f"({dst.stat().st_size // 1024} KB)", flush=True)
    m = mk.write_manifest()
    print(f"{done} take(s); manifest:", json.dumps({"states": {k: len(v) for k, v in m["states"].items()},
                                                   "gestures": {k: len(v) for k, v in m["gestures"].items()}}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
