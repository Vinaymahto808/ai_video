"""Human-likeness scorer.

Two modes:
  --heuristic (default)  fast, deterministic, runs here. 0-100.
  --judge                asks an LLM to score the same transcript against the rubric.

    python human_bot/eval/humanness.py --in training/sft/val.jsonl
    python human_bot/eval/humanness.py --in call_transcript.json --format transcript --judge

The heuristic deliberately measures *speech* properties, not "helpfulness". A reply can
be perfectly useful and still sound like a brochure — that is exactly what we catch.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

BANNED = re.compile(r"\b(as an ai|i cannot|certainly[!,]|great question|i'?d be happy to|please note that)\b", re.I)
MARKDOWN = re.compile(r"(\*\*|^#+\s|^\s*[-*•]\s|\|.*\|)", re.M)
MONEY_WRITTEN = re.compile(r"₹|\bRs\.?\s?\d")
CONTRACTIONS = re.compile(r"\b\w+'(s|t|re|ll|ve|d|m)\b", re.I)
FILLERS = re.compile(r"\b(toh|dekhiye|actually|basically|you know|matlab|umm|uhh)\b", re.I)
ACK = re.compile(r"^(haan|han|ji|samajh|theek|bilkul|right|okay|ok|sure|got it|acha|accha)\b", re.I)

JUDGE = """You are an expert rater of conversational naturalness in sales calls.
Score the ASSISTANT turns only, 1-5 on each dimension, and return JSON:
{"warmth":1-5,"brevity":1-5,"turn_taking":1-5,"specificity":1-5,"listen_feel":1-5,
 "human_score":0-100,"top_issues":["..."],"best_line":"..."}
Definitions: warmth = acknowledges the person; brevity = speaks like speech not a document;
turn_taking = one question at a time, no monologues; specificity = uses what the customer said;
listen_feel = would a customer believe this is a person. Be strict: 5 = indistinguishable from a
good human rep. Return JSON only."""


def turns_from(obj: dict) -> list[dict]:
    if "messages" in obj:
        return [{"role": m["role"], "text": m["content"]} for m in obj["messages"] if m["role"] != "system"]
    if "turns" in obj:
        return [{"role": t["role"], "text": t.get("text", "")} for t in obj["turns"]]
    if isinstance(obj.get("transcript"), list):
        return obj["transcript"]
    return []


def score_turns(turns: list[dict], max_words: int = 40) -> dict:
    asst = [t["text"] for t in turns if t["role"] == "assistant" and t["text"].strip()]
    user = [t["text"] for t in turns if t["role"] == "user" and t["text"].strip()]
    if not asst:
        return {"human_score": 0, "error": "no assistant turns"}

    words = [len(t.split()) for t in asst]
    avg = statistics.mean(words)
    over = sum(w > max_words for w in words) / len(words)
    stacked = sum(t.count("?") > 1 for t in asst) / len(asst)
    no_q = sum("?" in t for t in asst) / len(asst)
    md = sum(bool(MARKDOWN.search(t)) for t in asst) / len(asst)
    banned = sum(bool(BANNED.search(t)) for t in asst) / len(asst)
    money = sum(bool(MONEY_WRITTEN.search(t)) for t in asst) / len(asst)
    contraction = sum(bool(CONTRACTIONS.search(t)) for t in asst) / len(asst)
    filler_rate = len(FILLERS.findall(" ".join(asst))) / max(1, sum(words)) * 100
    ack_rate = sum(bool(ACK.search(t)) for t in [*asst[1:]]) / max(1, len(asst) - 1)
    talk_ratio = sum(words) / max(1, sum(words) + sum(len(u.split()) for u in user))
    longest = max(words)

    # 100 points. Each defect subtracts; the two style positives add.
    s = 100.0
    s -= over * 35
    s -= stacked * 20
    s -= md * 20
    s -= banned * 25
    s -= money * 10
    s -= max(0, avg - 18) * 1.4           # ideal spoken turn 12-18 words
    s -= max(0, longest - 55) * 0.5
    s -= max(0, talk_ratio - 0.55) * 60   # agent should not dominate the call
    s -= max(0, 0.6 - ack_rate) * 25
    s -= max(0, 0.4 - contraction) * 15
    s -= max(0, filler_rate - 2.5) * 6
    s += min(4, no_q * 4)                 # it should be asking things
    human = round(max(0, min(100, s)), 1)

    return {
        "human_score": human,
        "turns": len(asst),
        "avg_words": round(avg, 1),
        "longest_turn_words": longest,
        "pct_over_budget": round(100 * over, 1),
        "pct_stacked_questions": round(100 * stacked, 1),
        "pct_markdown": round(100 * md, 1),
        "pct_assistant_speak": round(100 * banned, 1),
        "pct_written_money": round(100 * money, 1),
        "contraction_rate": round(100 * contraction, 1),
        "ack_rate": round(100 * ack_rate, 1),
        "filler_per_100w": round(filler_rate, 1),
        "talk_ratio_agent": round(talk_ratio, 2),
        "verdict": "human-like" if human >= 80 else "usable" if human >= 60 else "robotic",
    }


def judge(turns: list[dict]) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from llm.client import stream_reply  # noqa: E402

    plain = "\n".join(f"{t['role']}: {t['text']}" for t in turns)
    raw = "".join(stream_reply([
        {"role": "system", "content": JUDGE},
        {"role": "user", "content": plain[:8000]},
    ]))
    m = re.search(r"\{[\s\S]*\}", raw)
    return json.loads(m.group(0)) if m else {"raw": raw[:500]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--format", choices=["auto", "jsonl", "transcript"], default="auto")
    ap.add_argument("--max-words", type=int, default=40)
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    path = Path(a.inp)
    rows: list[list[dict]] = []
    if a.format in ("auto", "jsonl") and path.suffix == ".jsonl":
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                rows.append(turns_from(json.loads(line.lstrip("\ufeff"))))
    else:
        rows.append(turns_from(json.loads(path.read_text(encoding="utf-8-sig"))))

    results = [score_turns(r, a.max_words) for r in rows]
    agg = {
        "conversations": len(results),
        "human_score_avg": round(statistics.mean(r["human_score"] for r in results), 1),
        "human_score_min": min(r["human_score"] for r in results),
        "human_score_max": max(r["human_score"] for r in results),
        "verdicts": {v: sum(r["verdict"] == v for r in results) for v in ("human-like", "usable", "robotic")},
        "worst": sorted(results, key=lambda r: r["human_score"])[:3],
    }
    if a.judge and rows:
        agg["llm_judge"] = judge(rows[0])

    if a.json:
        print(json.dumps(agg, indent=2))
    else:
        print(f"conversations: {agg['conversations']}")
        print(f"human score : {agg['human_score_avg']}/100  (min {agg['human_score_min']}, max {agg['human_score_max']})")
        print(f"verdicts    : {agg['verdicts']}")
        for w in agg["worst"]:
            print(f"  - {w['human_score']:>5}  {w['verdict']:<10} {w['avg_words']}w/turn, "
                  f"{w['pct_over_budget']}% over budget, {w['pct_markdown']}% markdown")
        if "llm_judge" in agg:
            print(json.dumps(agg["llm_judge"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
