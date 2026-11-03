"""Fit the puppet's gesture keyframes and idle statistics from extracted motion tracks.

Reads tran_moment/motion/tracks/*.json (from extract_motion.py) and writes
avatar_widget/src/motion-data.js, the module avatar.js imports for its body motion, plus a
human-readable tran_moment/motion/report.md.

Each gesture in the persona vocabulary (human_bot/persona/*.json -> avatar.gestures) gets a track:

    clip-fitted   the whole reference clip of that name, reduced to a few keyframes per channel
    mined         a short window found automatically inside the 55 s presenter segment by a
                  feature rule (biggest nod, strongest side-to-side roll alternation, longest held
                  tilt, largest move toward the camera)
    rest          no motion (pause, freeze)

Channel mapping to avatar.js (same units it already uses):

    pitch  deg  head nod, chin down > 0         clamp +-6
    roll   deg  head tilt                       clamp +-3.6 (ROLL_LIMIT; shape preserved by scaling)
    dx/dy  %    travel across the frame         clamp +-1.5
    zoom   -    scale delta around BASE_ZOOM    clamp -0.005..0.04 (frame must stay covered)

Every track starts and ends at exact rest so a gesture can be dropped by a barge-in without a jump.

    python tran_moment/motion/fit_gestures.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TRACKS = HERE / "tracks"
ROOT = HERE.parent.parent
OUT_JS = ROOT / "avatar_widget" / "src" / "motion-data.js"
OUT_MD = HERE / "report.md"

CHANNELS = ("pitch", "roll", "dx", "dy", "zoom")
CLAMP = {"pitch": 6.0, "roll": 3.6, "dx": 1.5, "dy": 1.5}
ZOOM_MIN, ZOOM_MAX = -0.005, 0.04
NOISE = {"pitch": 0.6, "roll": 0.5, "dx": 0.15, "dy": 0.15, "zoom": 0.004}
MAX_KEYS = 7          # interior keyframes per channel
RDP_FRACTION = 0.10   # tolerance as a fraction of the channel's range

# Which clip feeds which vocabulary name (prepared/clips/manifest.json mapping, first clip wins).
# `idle` is deliberately not fitted from the idle clip: the idle *state* is micro-motion driven by
# IDLE_STATS, and the clip is the presenter talking at a counter, not resting.
CLIP_FOR = {
    "small_nod": "small_nod", "slow_nod": "slow_nod", "look_up": "look_up",
    "both_hands": "both_hands", "open_palm": "open_palm", "greeting": "greeting",
}
# What each name must look like. A clip-fitted track that breaks its rule is not used under that
# name (the slow_nod clip, for instance, lifts the chin); mining then looks for a window that fits.
RULES = {
    "small_nod": "pitch_down", "nod": "pitch_down", "slow_nod": "pitch_down",
    "look_up": "pitch_up", "head_wobble": "alternate_roll", "tilt_head": "roll_hold",
    "lean_in": "toward", "both_hands": "toward",
}
# Mining windows (seconds) per name and the fallback when nothing clean fits the rule:
#   "<clip>"  derive from that clip's fitted track; None -> the authored track in avatar.js stays.
MINED = {
    "nod":         {"win": (0.45, 0.95), "fallback": "small_nod"},
    "small_nod":   {"win": (0.35, 0.80), "fallback": None},
    "slow_nod":    {"win": (1.00, 1.60), "fallback": "small_nod"},
    "look_up":     {"win": (0.60, 1.20), "fallback": None},
    "head_wobble": {"win": (0.80, 1.40), "fallback": None},
    "tilt_head":   {"win": (0.70, 1.20), "fallback": None},
    "lean_in":     {"win": (1.00, 1.80), "fallback": "both_hands"},
    "both_hands":  {"win": (1.00, 1.60), "fallback": None},
}
REST = {"idle": 800, "pause": 600, "freeze": 120}
# How many extra takes to mine per gesture (exported as name__v2, name__v3 for random variation).
VARIANTS = {"nod": 2, "small_nod": 2, "slow_nod": 1, "head_wobble": 2, "tilt_head": 1, "look_up": 1}
MINE_SOURCES = ["neha_sample", "presenter", "open_palm", "greeting", "idle", "both_hands", "slow_nod", "small_nod", "look_up"]

# Window quality gates: the wide shot has a ~33 px face, so tracking spikes must be rejected.
MIN_FACE_PX = 24
MAX_STEP_DEG = 4.0     # per-frame |delta pitch| or |delta roll|
MAX_RANGE_DEG = 15.0   # peak-to-peak pitch / roll inside one gesture
MAX_RANGE_DX = 8.0     # percent of frame width


# ---------------------------------------------------------------- track helpers
def load(name: str) -> dict | None:
    p = TRACKS / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def series(track: dict) -> dict[str, np.ndarray]:
    """Dense per-frame arrays with misses linearly interpolated."""
    fr = track["frames"]
    t = np.array([f["t"] for f in fr], dtype=float)
    ok = np.array([f.get("ok", False) for f in fr])
    out = {"t": t, "ok": ok}
    for k in ("pitch", "yaw", "roll", "dx", "dy", "scale", "blink", "jaw", "iod"):
        v = np.array([f.get(k, np.nan) if f.get("ok") else np.nan for f in fr], dtype=float)
        if np.isnan(v).all():
            v = np.zeros_like(t)
        else:
            idx = np.arange(len(v))
            good = ~np.isnan(v)
            v = np.interp(idx, idx[good], v[good])
        out[k] = v
    out["zoom"] = out["scale"] - 1.0
    out["face_px"] = out["iod"] * 1.8
    return out


def window_ok(s: dict, i0: int, i1: int) -> bool:
    """Reject windows with misses, tiny faces, tracking spikes or implausible travel."""
    if i1 - i0 < 4 or (~s["ok"][i0:i1]).any():
        return False
    if s["face_px"][i0:i1].min() < MIN_FACE_PX:
        return False
    p, r = s["pitch"][i0:i1], s["roll"][i0:i1]
    if np.abs(np.diff(p)).max() > MAX_STEP_DEG or np.abs(np.diff(r)).max() > MAX_STEP_DEG:
        return False
    if np.ptp(p) > MAX_RANGE_DEG or np.ptp(r) > MAX_RANGE_DEG or np.ptp(s["dx"][i0:i1]) > MAX_RANGE_DX:
        return False
    return True


def smooth(v: np.ndarray, k: int = 3) -> np.ndarray:
    if k <= 1 or len(v) < k:
        return v
    pad = k // 2
    vp = np.pad(v, (pad, pad), mode="edge")
    return np.convolve(vp, np.ones(k) / k, mode="valid")


def rest_to_rest(v: np.ndarray) -> np.ndarray:
    """Remove the linear trend between first and last sample so both ends are exactly zero."""
    if len(v) < 2:
        return np.zeros_like(v)
    line = np.linspace(v[0], v[-1], len(v))
    return v - line


def rdp(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    (x0, y0), (x1, y1) = points[0], points[-1]
    dmax, imax = 0.0, 0
    for i in range(1, len(points) - 1):
        x, y = points[i]
        if x1 == x0:
            d = abs(y - y0)
        else:
            d = abs((y1 - y0) * x - (x1 - x0) * y + x1 * y0 - y1 * x0) / math.hypot(y1 - y0, x1 - x0)
        if d > dmax:
            dmax, imax = d, i
    if dmax > eps:
        left = rdp(points[: imax + 1], eps)
        right = rdp(points[imax:], eps)
        return left[:-1] + right
    return [points[0], points[-1]]


def keyframes(v: np.ndarray) -> list[list[float]]:
    """[[progress, value], ...] with exact zero ends and at most MAX_KEYS interior points.

    The channel's global maximum and minimum are always kept as keyframes, so a small second lobe
    (the return swing of a head wobble) survives simplification; RDP runs on the segments between.
    """
    n = len(v)
    pts = [(i / (n - 1), float(v[i])) for i in range(n)]
    rng = float(np.ptp(v)) or 1.0
    fixed = sorted({0, n - 1, int(np.argmax(v)), int(np.argmin(v))})
    eps = rng * RDP_FRACTION
    while True:
        simp: list[tuple[float, float]] = []
        for a, b in zip(fixed[:-1], fixed[1:]):
            seg = rdp(pts[a:b + 1], eps)
            simp.extend(seg if not simp else seg[1:])
        if len(simp) - 2 <= MAX_KEYS or eps > rng * 2:
            break
        eps *= 1.4
    simp[0] = (0.0, 0.0)
    simp[-1] = (1.0, 0.0)
    out = []
    for p, val in simp:
        p, val = round(p, 3), round(val, 3)
        if out and p <= out[-1][0]:
            continue
        out.append([p, val])
    out[-1] = [1.0, 0.0]
    return out


# ---------------------------------------------------------------- semantic checks (mirror avatar.js)
def sample(track: dict, ch: str, p: float) -> float:
    kf = track.get(ch)
    if not kf:
        return 0.0
    a, b = kf[0], kf[-1]
    for i in range(1, len(kf)):
        if p <= kf[i][0]:
            a, b = kf[i - 1], kf[i]
            break
    span = (b[0] - a[0]) or 1.0
    k = min(1.0, max(0.0, (p - a[0]) / span))
    return a[1] + (b[1] - a[1]) * (k * k * (3 - 2 * k))


def peak(track: dict, ch: str, sign: float = 1.0) -> float:
    return max(sign * sample(track, ch, i / 100.0) for i in range(101))


def satisfies(track: dict, rule: str | None) -> bool:
    if not rule:
        return True
    if rule == "pitch_down":
        return peak(track, "pitch") > 1.5 and peak(track, "pitch") >= peak(track, "pitch", -1)
    if rule == "pitch_up":
        return peak(track, "pitch", -1) > 0.8 and peak(track, "pitch", -1) >= peak(track, "pitch")
    if rule == "alternate_roll":
        return peak(track, "roll") > 1.0 and peak(track, "roll", -1) > 1.0
    if rule == "roll_hold":
        return max(peak(track, "roll"), peak(track, "roll", -1)) > 1.0
    if rule == "toward":
        return peak(track, "zoom") > 0.005 or peak(track, "dy") > 0.3
    return True


def clamp_channel(ch: str, v: np.ndarray) -> np.ndarray:
    if ch == "zoom":
        return np.clip(v, ZOOM_MIN, ZOOM_MAX)
    lim = CLAMP[ch]
    peak = float(np.max(np.abs(v))) if len(v) else 0.0
    if peak > lim:
        v = v * (lim / peak)  # keep the shape, shrink the amplitude
    return v


def fit_window(s: dict, i0: int, i1: int, fps: float) -> tuple[dict, dict]:
    """Fit one gesture from frames [i0, i1). Returns (track, ranges)."""
    dur_ms = int(round((i1 - i0) / fps * 1000))
    track: dict = {"dur": max(120, dur_ms)}
    ranges = {}
    for ch in CHANNELS:
        v = s[ch][i0:i1].astype(float)
        v = smooth(v, 3)
        v = rest_to_rest(v)
        v = clamp_channel(ch, v)
        rng = float(np.ptp(v)) if len(v) else 0.0
        ranges[ch] = round(rng, 3)
        if rng < NOISE[ch]:
            continue
        track[ch] = keyframes(v)
    return track, ranges


# ---------------------------------------------------------------- mining the presenter segment
def mine(s: dict, fps: float, kind: str, win: tuple[float, float], taken: list[tuple[int, int]]) -> tuple[int, int, float] | None:
    """Slide windows over one track and return (i0, i1, score) of the best clean window for `kind`.

    `taken` lists windows already assigned to other gestures on this track; overlaps are skipped so
    two vocabulary names never play the same footage.
    """
    n = len(s["t"])
    wmin, wmax = int(win[0] * fps), int(win[1] * fps)
    best = None
    step = max(1, int(fps / 10))
    for w in range(wmin, wmax + 1, max(1, (wmax - wmin) // 4 or 1)):
        for i0 in range(0, n - w, step):
            i1 = i0 + w
            if not window_ok(s, i0, i1):
                continue
            if any(i0 < b and a < i1 for a, b in taken):
                continue
            p = rest_to_rest(smooth(s["pitch"][i0:i1], 3))
            r = rest_to_rest(smooth(s["roll"][i0:i1], 3))
            z = rest_to_rest(smooth(s["zoom"][i0:i1], 3))
            if kind in ("nod", "small_nod", "slow_nod"):
                # one clean down-up bump: big positive peak, little negative excursion
                score = float(p.max() - 1.5 * max(0.0, -p.min()))
                if p.max() < 2.0 or p.argmax() < len(p) * 0.15 or p.argmax() > len(p) * 0.7:
                    continue
                if kind == "small_nod":
                    score -= 0.5 * max(0.0, p.max() - 4.0)  # prefer a modest one
            elif kind == "look_up":
                score = float(-p.min() - 1.5 * max(0.0, p.max()))
                if -p.min() < 1.5 or p.argmin() < len(p) * 0.15 or p.argmin() > len(p) * 0.75:
                    continue
            elif kind == "head_wobble":
                sign = np.sign(r)
                crossings = int(np.sum(sign[1:] * sign[:-1] < 0))
                if crossings < 2 or r.max() < 1.0 or -r.min() < 1.0:
                    continue
                score = float(min(r.max(), -r.min())) * min(crossings, 4) - 0.3 * float(np.ptp(p))
            elif kind == "both_hands":
                score = float(z.max()) * 100 + 0.3 * float(np.ptp(p))
                if z.max() < 0.006:
                    continue
            elif kind == "tilt_head":
                # roll goes out and holds: large mean |roll| in the middle 60% relative to the peak
                mid = r[int(len(r) * 0.2): int(len(r) * 0.8)]
                amp = float(np.max(np.abs(r)))
                if amp < 1.2:
                    continue
                score = float(np.mean(np.abs(mid))) - 0.3 * float(np.max(np.abs(p)))
            elif kind == "lean_in":
                score = float(z.max()) * 100 - 0.2 * float(np.max(np.abs(r)))
                if z.max() < 0.006:
                    continue
            else:
                continue
            if best is None or score > best[2]:
                best = (i0, i1, score)
    return best


# ---------------------------------------------------------------- idle statistics
def idle_stats(tracks: dict[str, dict | None]) -> dict:
    """Micro-motion, drift retargeting, blink and breathing statistics from calm footage.

    The footage never shows the presenter at rest (she talks in every clip), so "calm" here means
    the quietest clean 2 s windows: no misses, a trackable face, mean per-frame head velocity under
    2 deg and under 12 deg of pitch / 9 deg of roll travel. Pooled over every track. Statistics are
    robust (median absolute deviation) because the wide shot still carries tracking jitter, and
    avatar.js clamps each one to the frame budget, so a talking-presenter baseline cannot make the
    idle portrait thrash. Re-run with real resting footage to tighten these.
    """
    parts = []
    used = {}
    for name, tr in tracks.items():
        if not tr:
            continue
        s = series(tr)
        fps = tr["fps"]
        w = int(2 * fps)
        for i0 in range(0, len(s["t"]) - w + 1, w // 2):
            i1 = i0 + w
            if not window_ok(s, i0, i1):
                continue
            p, r = s["pitch"][i0:i1], s["roll"][i0:i1]
            vel = np.abs(np.diff(p)).mean() + np.abs(np.diff(r)).mean()
            if vel < 2.0 and np.ptp(p) < 12 and np.ptp(r) < 9:
                parts.append({k: s[k][i0:i1] for k in ("pitch", "roll", "dx", "dy", "zoom", "blink", "t")} | {"fps": fps})
                used[name] = used.get(name, 0) + 1
    if not parts:
        return {}

    def robust_std(v: np.ndarray) -> float:
        med = np.median(v)
        return float(1.4826 * np.median(np.abs(v - med)))

    def micro(ch):
        vals = []
        for p in parts:
            v = p[ch]
            k = int(1.0 * p["fps"]) | 1
            hp = v - smooth(v, k) if len(v) > k else v - v.mean()
            vals.append(hp)
        return robust_std(np.concatenate(vals))

    def retarget_interval():
        gaps = []
        for p in parts:
            v = smooth(p["roll"] + p["pitch"], int(0.6 * p["fps"]) | 1)
            d = np.diff(v)
            ext = np.where(d[1:] * d[:-1] < 0)[0]
            if len(ext) > 1:
                gaps.extend(np.diff(ext) / p["fps"])
        gaps = [g for g in gaps if g > 0.8]
        return (float(np.mean(gaps)), float(np.std(gaps))) if gaps else (5.5, 1.3)

    def blinks():
        intervals, durs = [], []
        for p in parts:
            b = p["blink"] > 0.45
            on = np.where(np.diff(b.astype(int)) == 1)[0]
            off = np.where(np.diff(b.astype(int)) == -1)[0]
            if len(on) > 1:
                intervals.extend(np.diff(on) / p["fps"])
            for o in on:
                f = off[off > o]
                if len(f):
                    durs.append((f[0] - o) / p["fps"])
        return intervals, durs

    def breathing_period():
        periods = []
        for p in parts:
            v = p["zoom"] - smooth(p["zoom"], int(6 * p["fps"]) | 1) if len(p["zoom"]) > 6 * p["fps"] else p["zoom"] - p["zoom"].mean()
            if len(v) < 3 * p["fps"]:
                continue
            v = v - v.mean()
            ac = np.correlate(v, v, mode="full")[len(v) - 1:]
            lo, hi = int(2 * p["fps"]), int(min(len(ac) - 1, 6 * p["fps"]))
            if hi > lo:
                periods.append((lo + int(np.argmax(ac[lo:hi]))) / p["fps"])
        return float(np.median(periods)) if periods else 4.6

    ri = retarget_interval()
    bi, bd = blinks()
    return {
        "micro_std": {"pitch": round(micro("pitch"), 3), "roll": round(micro("roll"), 3), "dx": round(micro("dx"), 3)},
        "retarget_s": {"mean": round(ri[0], 2), "std": round(ri[1], 2)},
        "blink_s": {"mean": round(float(np.mean(bi)), 2) if bi else 3.8, "std": round(float(np.std(bi)), 2) if bi else 1.2,
                    "dur": round(float(np.median(bd)), 3) if bd else 0.12, "count": len(bi)},
        "breath_period_s": round(breathing_period(), 2),
        "calm_seconds": round(sum(len(p["t"]) / p["fps"] for p in parts) / 2, 1),  # windows overlap by half
        "calm_windows_by_source": used,
        "note": "quietest clean windows of a talking presenter, not true rest; values are clamped in avatar.js",
    }


# ---------------------------------------------------------------- main
def main() -> int:
    tracks = {n: load(n) for n in ["neha_sample", "idle", "small_nod", "slow_nod", "look_up", "both_hands", "open_palm", "greeting", "presenter"]}
    missing = [n for n, t in tracks.items() if t is None]
    if missing:
        print("missing tracks:", ", ".join(missing), "(run extract_motion.py)")

    gestures: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    rows = []

    rejected: dict[str, str] = {}
    for name, clip in CLIP_FOR.items():
        tr = tracks.get(clip)
        if not tr:
            continue
        s = series(tr)
        g, rng = fit_window(s, 0, len(s["t"]), tr["fps"])
        if not satisfies(g, RULES.get(name)):
            rejected[name] = f"clip {Path(tr['source']).name} breaks rule {RULES.get(name)}"
            print(f"  ! {name}: {rejected[name]}; will mine instead")
            continue
        gestures[name] = g
        sources[name] = {"kind": "clip", "clip": tr["source"], "frames": tr["frame_range"], "detected": f"{tr['detected']}/{tr['detected'] + tr['missed']}"}
        rows.append((name, "clip " + Path(tr["source"]).name, g["dur"], rng))

    pool = {n: (tracks[n], series(tracks[n])) for n in MINE_SOURCES if tracks.get(n)}
    taken: dict[str, list[tuple[int, int]]] = {n: [] for n in pool}
    for name, spec in MINED.items():
        if name in gestures:
            continue  # clip fit passed its rule
        hit = None
        for src, (tr, s) in pool.items():
            h = mine(s, tr["fps"], name, spec["win"], taken[src])
            if not h:
                continue
            g, _ = fit_window(s, h[0], h[1], tr["fps"])
            if not satisfies(g, RULES.get(name)):
                continue  # survived mining but not the final (clamped, simplified) track
            if hit is None or h[2] > hit[1][2]:
                hit = (src, h)
        if hit:
            src, (i0, i1, score) = hit
            tr, s = pool[src]
            taken[src].append((i0, i1))
            g, rng = fit_window(s, i0, i1, tr["fps"])
            gestures[name] = g
            sources[name] = {"kind": "mined", "clip": tr["source"], "window_s": [round(s["t"][i0], 2), round(s["t"][i1 - 1], 2)], "score": round(score, 3)}
            if name in rejected:
                sources[name]["replaced_clip"] = rejected[name]
            rows.append((name, f"mined {src} {s['t'][i0]:.1f}-{s['t'][i1 - 1]:.1f}s", g["dur"], rng))
        elif spec["fallback"] and spec["fallback"] in gestures:
            base = gestures[spec["fallback"]]
            g = json.loads(json.dumps(base))
            if name == "nod" and "pitch" in g:       # a plain nod is the small nod, a little bigger
                g["pitch"] = [[p, round(v * 1.5, 3)] for p, v in g["pitch"]]
            if name == "slow_nod":                    # or the same nod at half speed
                g["dur"] = int(base["dur"] * 2)
            gestures[name] = g
            sources[name] = {"kind": "derived", "from": spec["fallback"], "note": rejected.get(name, "no clean footage")}
            rows.append((name, f"derived from {spec['fallback']}", g["dur"], {}))
        else:
            rows.append((name, "no clean footage -> authored track in avatar.js", 0, {}))
    # Variants: a second (and third) clean window per gesture from other footage, exported as
    # `<name>__v2` / `__v3`. avatar.js picks one at random for each request, so a nod is never the
    # identical curve twice in a row, which is what makes repeated gestures read as a tic.
    for name, want in VARIANTS.items():
        spec = MINED.get(name)
        if not spec or name not in gestures:
            continue
        for k in range(2, want + 2):
            hit = None
            for src, (tr, s) in pool.items():
                h = mine(s, tr["fps"], name, spec["win"], taken[src])
                if not h:
                    continue
                g, _ = fit_window(s, h[0], h[1], tr["fps"])
                if not satisfies(g, RULES.get(name)):
                    continue
                if hit is None or h[2] > hit[1][2]:
                    hit = (src, h)
            if not hit:
                break
            src, (i0, i1, score) = hit
            tr, s = pool[src]
            taken[src].append((i0, i1))
            g, rng = fit_window(s, i0, i1, tr["fps"])
            vname = f"{name}__v{k}"
            gestures[vname] = g
            sources[vname] = {"kind": "variant", "of": name, "clip": tr["source"], "window_s": [round(s["t"][i0], 2), round(s["t"][i1 - 1], 2)], "score": round(score, 3)}
            rows.append((vname, f"variant of {name}: mined {src} {s['t'][i0]:.1f}-{s['t'][i1 - 1]:.1f}s", g["dur"], rng))

    for name, dur in REST.items():
        gestures[name] = {"dur": dur}
        sources[name] = {"kind": "rest"}
        rows.append((name, "rest", dur, {}))

    idle = idle_stats(tracks)

    # ---- write JS module
    def js(o):
        return json.dumps(o, separators=(",", ":"))

    lines = [
        "/**",
        " * Body-motion data learned from reference footage. GENERATED by tran_moment/motion/fit_gestures.py;",
        " * do not edit by hand, re-run the fit. Channels and units match avatar.js (pitch/roll deg, dx/dy %,",
        " * zoom delta). Keyframes are [progress 0..1, value]; every track starts and ends at exact rest.",
        " *",
        " * Sources: prepared/clips/*.mp4 (first half of each ping-pong loop) and the 0-55 s presenter segment of",
        " * sample_audio.mp4, reduced to pose curves only. See tran_moment/motion/report.md for provenance.",
        " */",
        "export const LEARNED_GESTURES = Object.freeze({",
    ]
    for name in gestures:
        lines.append(f"  {name}: {js(gestures[name])},")
    lines.append("});")
    lines.append("")
    lines.append("export const GESTURE_SOURCES = Object.freeze(" + json.dumps(sources, indent=2) + ");")
    lines.append("")
    lines.append("/** Idle statistics from calm footage: micro-motion std, drift retarget interval, blinks, breathing. */")
    lines.append("export const IDLE_STATS = Object.freeze(" + json.dumps(idle, indent=2) + ");")
    lines.append("")
    OUT_JS.write_text("\n".join(lines), encoding="utf-8")

    # ---- report
    md = ["# Learned body motion — fit report", "",
          f"Tracks: {', '.join(n for n, t in tracks.items() if t)}", "",
          "| gesture | source | dur ms | pitch° | roll° | dx % | dy % | zoom | channels kept |",
          "|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for name, src, dur, rng in rows:
        kept = ", ".join(ch for ch in CHANNELS if ch in gestures.get(name, {})) or "—"
        md.append(f"| {name} | {src} | {dur} | {rng.get('pitch', '')} | {rng.get('roll', '')} | {rng.get('dx', '')} | {rng.get('dy', '')} | {rng.get('zoom', '')} | {kept} |")
    md += ["", "## Idle statistics", "", "```json", json.dumps(idle, indent=2), "```", "",
           "Ranges are peak-to-peak after rest-to-rest detrending and clamping (roll ≤ 3.6°, zoom −0.005..0.04).",
           "A channel below the noise floor (pitch 0.6°, roll 0.5°, dx/dy 0.15 %, zoom 0.004) is dropped from that gesture."]
    OUT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"wrote {OUT_JS.relative_to(ROOT)} ({len(gestures)} gestures) and {OUT_MD.relative_to(ROOT)}")
    for name, src, dur, rng in rows:
        print(f"  {name:12s} {dur:5d} ms  {src:40s} " + " ".join(f"{ch}={rng[ch]}" for ch in CHANNELS if ch in rng))
    print("  idle:", json.dumps(idle))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
