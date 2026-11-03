"""Turn raw conversation logs into a clean SFT set + a style audit.

    python human_bot/train/build_dataset.py --in training/conversations.jsonl --out training/sft

It does five things, in order, and prints what it changed:

1. Parse   — accepts {"messages":[...]} or {"turns":[{"role","text"}]}.
2. Clean   — strips control/replacement chars, normalises whitespace, verifies role order.
3. Audit   — flags every assistant turn that breaks the human-style contract
             (too long, stacked questions, markdown, assistant-speak, written numbers).
4. Split   — 90/10 train/val, deduplicated (exact hash + near-duplicate ratio).
5. Emit    — train.jsonl / val.jsonl in chat format + style_report.json + dataset_card.md

Why audit instead of rewriting: you do not want to train on text a regex produced.
Flag it, then fix the source (real calls) or regenerate it with a bigger model.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import statistics
from collections import Counter
from pathlib import Path

BANNED = re.compile(
    r"\b(as an ai|i cannot|i can't help with that|certainly[!,]|great question|"
    r"i'?d be happy to|please note that)\b", re.I
)
MARKDOWN = re.compile(r"(\*\*|^#+\s|^\s*[-*•]\s|\|.*\|)", re.M)
WRITTEN_MONEY = re.compile(r"₹|\bRs\.?\s?\d|\bINR\s?\d")
ALLOWED_TOOL = re.compile(r"^\[(tool|tool_result|retrieved):")


def parse_row(row: dict) -> list[dict] | None:
    if isinstance(row.get("messages"), list):
        msgs = row["messages"]
    elif isinstance(row.get("turns"), list):
        msgs = [{"role": t.get("role"), "content": t.get("text", "")} for t in row["turns"]]
    else:
        return None
    out = []
    for m in msgs:
        role, content = m.get("role"), (m.get("content") or "")
        if role not in ("system", "user", "assistant") or not str(content).strip():
            return None
        out.append({"role": role, "content": str(content)})
    if not any(m["role"] == "assistant" for m in out):
        return None
    return out


def clean(text: str) -> str:
    text = text.replace("\ufffd", "").replace("\u200b", "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def audit_assistant(text: str, max_words: int = 40) -> list[str]:
    if ALLOWED_TOOL.match(text):
        return []
    flags = []
    words = len(text.split())
    if words > max_words:
        flags.append(f"too_long:{words}w")
    if text.count("?") > 1:
        flags.append("stacked_questions")
    if MARKDOWN.search(text):
        flags.append("markdown")
    if BANNED.search(text):
        flags.append("assistant_speak")
    if WRITTEN_MONEY.search(text):
        flags.append("written_money")
    if text.count("!") > 1:
        flags.append("over_excited")
    if not re.search(r"[.!?।]$", text.strip()):
        flags.append("no_terminal_punct")
    return flags


def dedupe(rows: list[dict]) -> tuple[list[dict], int]:
    seen_hash, kept, dropped = set(), [], 0
    for r in rows:
        key = hashlib.sha256(json.dumps([m["content"] for m in r["messages"]], ensure_ascii=False).encode()).hexdigest()
        if key in seen_hash:
            dropped += 1
            continue
        seen_hash.add(key)
        blat = " ".join(m["content"] for m in r["messages"] if m["role"] != "system")
        if any(difflib.SequenceMatcher(None, blat, prev[0]).ratio() > 0.94 for prev in kept[-40:]):
            dropped += 1
            continue
        kept.append((blat, r))
    return [r for _, r in kept], dropped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="training/conversations.jsonl")
    ap.add_argument("--out", dest="out", default="training/sft")
    ap.add_argument("--val-fraction", type=float, default=0.1)
    ap.add_argument("--max-words", type=int, default=40)
    ap.add_argument("--drop-noncompliant", action="store_true",
                    help="drop rows where any assistant turn breaks the style contract")
    a = ap.parse_args()

    src = Path(a.inp)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    raw, bad, rows = 0, 0, []
    for line in src.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not line.strip():
            continue
        raw += 1
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        msgs = parse_row(row)
        if not msgs:
            bad += 1
            continue
        for m in msgs:
            m["content"] = clean(m["content"])
        rows.append({"messages": msgs, "tags": row.get("tags", [])})

    rows, dups = dedupe(rows)

    flagged = []
    for i, r in enumerate(rows):
        for j, m in enumerate(r["messages"]):
            if m["role"] != "assistant":
                continue
            f = audit_assistant(m["content"], a.max_words)
            if f:
                flagged.append({"row": i, "turn": j, "flags": f, "text": m["content"][:140]})

    if a.drop_noncompliant:
        bad_rows = {f["row"] for f in flagged}
        rows = [r for i, r in enumerate(rows) if i not in bad_rows]

    n_val = max(1, int(len(rows) * a.val_fraction)) if len(rows) > 2 else 0
    val, train = rows[:n_val], rows[n_val:]

    for name, part in (("train.jsonl", train), ("val.jsonl", val)):
        (out / name).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in part) + ("\n" if part else ""),
            encoding="utf-8",
        )

    asst = [m["content"] for r in rows for m in r["messages"] if m["role"] == "assistant"
            and not ALLOWED_TOOL.match(m["content"])]
    words = [len(t.split()) for t in asst] or [0]
    report = {
        "source": str(src),
        "rows_read": raw,
        "rows_dropped_unparseable": bad,
        "rows_dropped_duplicate": dups,
        "rows_kept": len(rows),
        "split": {"train": len(train), "val": len(val)},
        "tags": dict(Counter(t for r in rows for t in r.get("tags", []))),
        "assistant_turns": len(asst),
        "avg_words_per_turn": round(statistics.mean(words), 1),
        "median_words_per_turn": statistics.median(words),
        "pct_over_budget": round(100 * sum(w > a.max_words for w in words) / len(words), 1),
        "style_flags": dict(Counter(f.split(":")[0] for x in flagged for f in x["flags"])),
        "flagged_examples": flagged[:20],
    }
    (out / "style_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    card = f"""# SFT dataset card

- source: `{src}`
- kept: **{len(rows)}** conversations (train {len(train)} / val {len(val)})
- dropped: {bad} unparseable, {dups} duplicate
- avg assistant turn: **{report['avg_words_per_turn']} words** (target 12-18 for spoken replies)
- turns over budget: **{report['pct_over_budget']}%**
- style flags: {json.dumps(report['style_flags'])}

## Read this before training
{len(rows)} examples is a *prompt-tuning* set, not a fine-tuning set. At this size, LoRA will
memorise the 14 conversations and get worse at everything else. Use this set to:
  1. validate that the style contract in `persona/prompting.py` produces the replies you want;
  2. build the eval rubric;
  3. seed a bigger collection run (real calls / simulated customers).
Fine-tune only after ~300-1000 real or high-fidelity transcripts, and always hold out 10%
of *customers*, not turns, so you measure generalisation.
"""
    (out / "dataset_card.md").write_text(card, encoding="utf-8")

    print(json.dumps({k: v for k, v in report.items() if k != "flagged_examples"}, indent=2, ensure_ascii=False))
    print(f"\nwrote {out}/train.jsonl, {out}/val.jsonl, style_report.json, dataset_card.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
