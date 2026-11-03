"""Concurrency / latency probe for human_bot: N simulated customers, each opens a session and asks T questions.

    python human_bot/tools/loadtest.py --users 10 --turns 2 --base http://127.0.0.1:4001

Reports per-turn: time to the `sources` event (knowledge), to the first `sentence` (TTFS: the moment the
card can start the first clip), to `final`, plus session open time and error counts. Uses only the stdlib
(threads), so it runs anywhere the bot runs. Provider rate limits (Groq 429) show up as slow turns that fall
back to the offline script; the report flags them.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import threading
import time
import urllib.request

QUESTIONS = [
    "meri creta ka ac thanda nahi kar raha", "periodic service kitne mein hogi swift ki", "brake se awaaz aa rahi hai",
    "gurgaon mein pickup milega?", "insurance claim ka process kya hai", "battery dead hai car start nahi ho rahi",
    "denting painting ka estimate chahiye", "noida workshop ka timing kya hai", "clutch slip ho raha hai", "wheel alignment karwana hai",
]


def one_user(base: str, turns: int, out: list, errors: list, uid: int) -> None:
    try:
        t0 = time.perf_counter()
        r = urllib.request.urlopen(urllib.request.Request(base + "/session", data=json.dumps({"persona": "auto-car-repair", "context": {"visitor": f"loadtest{uid:03d}x"}}).encode(),
                                                          headers={"Content-Type": "application/json"}), timeout=30)
        sid = json.loads(r.read())["session_id"]
        out.append(("session", time.perf_counter() - t0, False))
    except Exception as ex:  # noqa: BLE001
        errors.append(f"session: {type(ex).__name__}: {ex}")
        return
    for _ in range(turns):
        q = random.choice(QUESTIONS)
        req = urllib.request.Request(base + "/chat", data=json.dumps({"session_id": sid, "text": q}).encode(), headers={"Content-Type": "application/json"})
        t = time.perf_counter(); ev = None; srcs = first = final = None; provider = ""
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                for line in resp:
                    line = line.decode().strip()
                    if line.startswith("event:"):
                        ev = line[6:].strip()
                    elif line.startswith("data:"):
                        now = time.perf_counter() - t
                        if ev == "meta":
                            provider = json.loads(line[5:]).get("provider", "")
                        elif ev == "sources" and srcs is None:
                            srcs = now
                        elif ev == "sentence" and first is None:
                            first = now
                        elif ev == "final":
                            final = now
                        elif ev == "error":
                            errors.append("turn error: " + line[5:][:120])
        except Exception as ex:  # noqa: BLE001
            errors.append(f"chat: {type(ex).__name__}: {ex}")
            continue
        offline = provider == "offline"
        out.append(("knowledge", srcs if srcs is not None else float("nan"), offline))
        out.append(("first_sentence", first if first is not None else float("nan"), offline))
        out.append(("final", final if final is not None else float("nan"), offline))
    try:
        urllib.request.urlopen(urllib.request.Request(base + "/end", data=json.dumps({"session_id": sid}).encode(), headers={"Content-Type": "application/json"}), timeout=30).read()
    except Exception:  # noqa: BLE001
        pass


def pct(vals: list[float], p: float) -> float:
    vals = sorted(v for v in vals if v == v)
    if not vals:
        return float("nan")
    return vals[min(len(vals) - 1, int(round((len(vals) - 1) * p)))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:4001")
    ap.add_argument("--users", type=int, default=10)
    ap.add_argument("--turns", type=int, default=2)
    ap.add_argument("--ramp", type=float, default=0.2, help="seconds between user starts")
    a = ap.parse_args()
    out: list = []; errors: list = []
    threads = []
    t0 = time.perf_counter()
    for i in range(a.users):
        th = threading.Thread(target=one_user, args=(a.base, a.turns, out, errors, i), daemon=True)
        th.start(); threads.append(th); time.sleep(a.ramp)
    for th in threads:
        th.join()
    wall = time.perf_counter() - t0
    print(f"{a.users} users x {a.turns} turns in {wall:.1f}s  errors={len(errors)}")
    for key in ("session", "knowledge", "first_sentence", "final"):
        vals = [v for k, v, _ in out if k == key]
        off = sum(1 for k, _, o in out if k == key and o)
        if vals:
            print(f"  {key:15s} n={len(vals):3d}  p50 {pct(vals, .5):.2f}s  p90 {pct(vals, .9):.2f}s  max {max(v for v in vals if v == v):.2f}s" + (f"  offline-script turns={off}" if off else ""))
    for e in errors[:5]:
        print("  !", e)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
