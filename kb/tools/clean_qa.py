"""Clean the call-transcript Q&A sheet (tran_moment/prepared/acr_customer_qa_all.csv) into knowledge rows.

Stage 1 (this file, deterministic, no network): normalise text, strip call meta ("(Oct 2026 call)",
"audio unclear", "ek call mein bataya ki"), merge duplicate / near-duplicate questions (the alternate
wording survives as an alias for retrieval, times_seen are summed), flag rows that still speak in the
third person ("Agent ne ...") or carry a quoted price, and write kb/seed/acr_customer_qa.clean.csv.

Stage 2 (rewrite_qa.py) turns the flagged third-person answers into Neha's own voice with the LLM
and keeps every fact; rows it cannot rewrite keep the stage-1 text, so the index is never empty.

    python kb/tools/clean_qa.py [--in path] [--out path]
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IN = ROOT / "tran_moment" / "prepared" / "acr_customer_qa_all.csv"
DEFAULT_OUT = ROOT / "kb" / "seed" / "acr_customer_qa.clean.csv"

# transcript artefacts that are not knowledge
META_RE = [
    (re.compile(r"\s*\((?:[^()]*\b(?:call|calls|audio|unclear|transcript|batch|agents?|advisor ne|bataya|kaha gaya)\b[^()]*)\)", re.I), ""),
    (re.compile(r"\b(?:ek|one|kuch|some|another|anya|doosri|alag|different)\s+(?:call|calls|case|cases|agents?)\s+(?:mein|me|par|pe|in|ne)\b[^.;:()]{0,60}?\b(?:bataya|batayi|kaha|bola|bole|mentioned|stated|said)\s+(?:gaya|gayi|gaye)?\s*(?:ki|that)?\s*", re.I), ""),
    (re.compile(r"\b(?:agent|advisor)\s+ne\s+(?:bataya|kaha|bola|confirm kiya)\s+(?:ki|that)\s+", re.I), ""),
    (re.compile(r"\b(?:audio|voice)\s+(?:unclear|not clear|inaudible)\b[.,;]?", re.I), ""),
    (re.compile(r"\b(?:stated|mentioned)\s+(?:complimentary\s+)?on one call\b", re.I), ""),
    (re.compile(r"\b(?:koi|no)\s+reason\s+(?:nahi\s+bataya\s+gaya|was\s+given)[.,;]?", re.I), ""),
    (re.compile(r"\b(?:exact|detail)[^.]{0,40}\b(?:nahi\s+di|nahi\s+bataya|not\s+given|not\s+provided)[.,;]?", re.I), ""),
]
THIRD_RE = re.compile(r"\b(agent|advisor ne|executive|CRE|caller|customer ka|customer ko|customer ki|customer se|the customer|the agent)\b", re.I)
PRICE_RE = re.compile(r"(₹|\brs\.?\s?\d|\b\d{1,3}(?:,\d{3})+\b|\b\d{3,5}\s*(?:\+\s*tax|/-|rupees|rs)\b|\blakh\b)", re.I)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").replace(" ", " ")
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def strip_meta(s: str) -> str:
    for rx, rep in META_RE:
        s = rx.sub(rep, s)
    s = re.sub(r"\s+([,.;:?!])", r"\1", s)
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ;,")
    if s and s[-1] not in ".?!":
        s += "."
    return s


def tokens(s: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()) - {"hai", "ka", "ki", "ke", "kya", "aap", "ho", "mein", "me", "se", "ya", "aur", "to", "the", "a", "is", "do", "you", "i", "my", "meri", "mera"}


def slug(s: str, n: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:n].rstrip("-")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", type=Path, default=DEFAULT_IN)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--jaccard", type=float, default=0.6)
    a = ap.parse_args()

    raw = a.inp.read_bytes().decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(raw)))
    out: list[dict] = []
    stats = {"in": len(rows), "merged": 0, "third_person": 0, "price": 0, "short": 0}
    for r in rows:
        q = norm(r.get("question_hinglish", "")); ans = strip_meta(norm(r.get("answer_hinglish", "")))
        qe = norm(r.get("question_english", "")); ae = strip_meta(norm(r.get("answer_english", "")))
        if len(q) < 8 or len(ans) < 12:
            stats["short"] += 1
            continue
        try:
            seen = int(r.get("times_seen") or 1)
        except ValueError:
            seen = 1
        out.append({"category": norm(r.get("category", "")) or "Other", "question": q, "answer": ans,
                    "question_en": qe, "answer_en": ae, "times_seen": seen, "aliases": [], "batches": {norm(r.get("batch", ""))}})

    # merge duplicates: the row seen most often keeps its answer, the other wording becomes an alias
    out.sort(key=lambda x: -x["times_seen"])
    kept: list[dict] = []
    for r in out:
        tk = tokens(r["question"])
        hit = None
        for k in kept:
            if k["category"] != r["category"]:
                continue
            kt = tokens(k["question"])
            if tk and kt and len(tk & kt) / len(tk | kt) >= a.jaccard:
                hit = k; break
        if hit:
            hit["aliases"].append(r["question"]); hit["aliases"] += r["aliases"]
            hit["times_seen"] += r["times_seen"]; hit["batches"] |= r["batches"]
            if r["question_en"] and r["question_en"].lower() != hit["question_en"].lower():
                hit["aliases"].append(r["question_en"])
            # a longer answer from the merged row is kept as extra detail when it adds facts
            if len(r["answer"]) > len(hit["answer"]) * 1.4 and r["answer"][:40] not in hit["answer"]:
                hit["answer_extra"] = r["answer"]
            stats["merged"] += 1
        else:
            kept.append(r)

    fields = ["id", "category", "question", "aliases", "answer", "answer_extra", "question_en", "answer_en", "times_seen", "batches", "needs_voice", "has_price", "source"]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, r in enumerate(sorted(kept, key=lambda x: (x["category"], -x["times_seen"])), 1):
            third = bool(THIRD_RE.search(r["answer"]) or THIRD_RE.search(r.get("answer_extra", "")))
            price = bool(PRICE_RE.search(r["answer"]))
            stats["third_person"] += third; stats["price"] += price
            w.writerow({"id": f"qa-{i:03d}-{slug(r['question_en'] or r['question'])}", "category": r["category"], "question": r["question"],
                        "aliases": " || ".join(dict.fromkeys(r["aliases"])), "answer": r["answer"], "answer_extra": r.get("answer_extra", ""),
                        "question_en": r["question_en"], "answer_en": r["answer_en"], "times_seen": r["times_seen"],
                        "batches": ",".join(sorted(b for b in r["batches"] if b)), "needs_voice": int(third), "has_price": int(price),
                        "source": "customer call transcripts (ACR, Oct 2026)"})
    stats["out"] = len(kept)
    print(stats, "->", a.out)


if __name__ == "__main__":
    main()
