"""Stage 2: rewrite the third-person call summaries into Neha's own voice with the LLM chain
(Groq -> OpenAI -> Gemini, same breakers as the bot). Facts are the contract: every number in the
source must survive, nothing may be added, the result is Hinglish, present tense, max ~2 sentences.
Results are cached in kb/seed/qa_rewrites.json so a 429 half-way through costs nothing; a row that
fails validation keeps its stage-1 text (flagged needs_voice=1 in the CSV for a later pass).

    python kb/tools/rewrite_qa.py [--only-flagged] [--limit N]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "human_bot"))
from llm.client import stream_reply, provider_name  # noqa: E402

CSV = ROOT / "kb" / "seed" / "acr_customer_qa.clean.csv"
CACHE = ROOT / "kb" / "seed" / "qa_rewrites.json"

SYSTEM = (
    "You rewrite call-centre notes into what Neha, Auto Car Repair (Delhi NCR multi-brand workshop) ki female service advisor, "
    "would say to the customer on the phone. Rules: first person (main/hum), present tense, Hinglish in Roman script, warm and short "
    "(max 2 sentences, under 60 words). Keep EVERY fact, number, price, branch name, timing and caveat exactly; add nothing; "
    "never promise anything the note does not. Drop references to 'agent', 'customer', 'advisor said', call outcomes and "
    "meta remarks. If the note says a thing must be confirmed by the team/advisor, say 'main team se confirm karke bataungi'. "
    "Output only the rewritten line, no quotes, no explanation."
)

NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
BAD_RE = re.compile(r"\b(agent|the customer|customer ka|customer ko|customer ne|caller)\b", re.I)


def ask(question: str, answer: str) -> str:
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"Customer question: {question}\nCall note: {answer}\n\nNeha's line:"}]
    out = "".join(stream_reply(msgs, fallback=None)).strip()
    out = re.sub(r"^\s*(neha|neha's line)\s*:\s*", "", out, flags=re.I).strip().strip('"')
    return out


def valid(src: str, out: str) -> str | None:
    if not out or len(out) < 12:
        return "empty"
    if len(out.split()) > 90:
        return "too long"
    if BAD_RE.search(out):
        return "third person"
    missing = [n for n in NUM_RE.findall(src) if n not in out]
    if missing:
        return f"numbers lost {missing}"
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-flagged", action="store_true", default=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--pause", type=float, default=1.2)
    a = ap.parse_args()
    rows = list(csv.DictReader(CSV.open(encoding="utf-8")))
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    # a provider failure (429, timeout) is not a verdict on the row: forget it so the next run retries
    cache = {k: v for k, v in cache.items() if not str(v.get("error") or "").startswith("error ")}
    todo = [r for r in rows if (int(r["needs_voice"]) if a.only_flagged else True) and r["id"] not in cache]
    if a.limit:
        todo = todo[: a.limit]
    print(f"provider={provider_name()} rows={len(rows)} to_rewrite={len(todo)} cached={len(cache)}", flush=True)
    ok = fail = 0
    for i, r in enumerate(todo, 1):
        src = r["answer"] + (" " + r["answer_extra"] if r["answer_extra"] else "")
        try:
            out = ask(r["question"], src)
            why = valid(src, out)
            if why and "numbers lost" in why:  # one retry with the numbers spelled out as a hard constraint
                out = ask(r["question"], src + "\n(Keep these exactly: " + ", ".join(NUM_RE.findall(src)) + ")")
                why = valid(src, out)
        except Exception as ex:  # noqa: BLE001
            out, why = "", f"error {type(ex).__name__}: {str(ex)[:80]}"
        cache[r["id"]] = {"text": out if not why else "", "src": src, "error": why}
        ok += not why; fail += bool(why)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[{i}/{len(todo)}] {r['id'][:28]:28s} {'ok' if not why else 'FAIL ' + why}", flush=True)
        if provider_name() == "offline":
            print("all providers down; stopping, rerun later", flush=True); break
        time.sleep(a.pause)
    print(f"done ok={ok} fail={fail}", flush=True)


if __name__ == "__main__":
    main()
