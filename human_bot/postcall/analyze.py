"""Post-call intelligence.

Implements the same JSON contract AIVA publishes for its call analysis, so a dashboard
built against their shape works against ours. Runs with no model (heuristics) and
upgrades to an LLM when a key is present.

Contract (from AIVA policy docs):
  summary, sentiment, sentiment_score, keywords, sentiment_segments, questions,
  action_items, lead_status, miscellaneous[{question,answer,weight,score}],
  unanswered_questions, audit_details
"""

from __future__ import annotations

import json
import re
from typing import Any

POS = {
    "love", "great", "good", "perfect", "thanks", "thank", "yes", "interested", "book",
    "badhiya", "accha", "pasand", "zaroor", "bilkul", "confirm", "ready", "best",
}
NEG = {
    "no", "not", "expensive", "costly", "bad", "problem", "issue", "delay", "angry",
    "nahi", "mehnga", "problem", "gussa", "cancel", "refund", "worst",
}
INTENT = {
    "price", "buy", "book", "test drive", "emi", "finance", "available", "stock",
    "kitna", "kitne", "lena", "chahiye", "book", "slot", "offer", "discount",
}
STOP = set(
    "the a an and or but if then so to of in on at for with is are was were be been i you he she it we they "
    "my your our their this that hai hain ka ki ke ko mein se par aur bhi kya main aap".split()
)

WEIGHTS = [
    ("purchase_timeline", ["month", "week", "today", "tomorrow", "jaldi", "is hafte", "next"], 20),
    ("budget_confirmed", ["lakh", "budget", "lac", "price"], 20),
    ("product_selected", ["glanza", "rumion", "taisor", "hyryder", "innova", "fortuner", "hilux"], 20),
    ("test_drive_intent", ["test drive", "td", "drive"], 20),
    ("finance_intent", ["emi", "loan", "finance", "down payment"], 20),
]


def _turns(transcript: list[dict]) -> list[dict]:
    return [t for t in transcript if t.get("role") in ("user", "assistant") and t.get("text")]


def _score(text: str) -> float:
    words = re.findall(r"[a-zA-Z\u0900-\u097F]+", text.lower())
    if not words:
        return 0.0
    p = sum(w in POS for w in words)
    n = sum(w in NEG for w in words)
    return round(max(-1.0, min(1.0, (p - n) / max(4, len(words) / 6))), 2)


def _label(s: float) -> str:
    return "positive" if s > 0.15 else "negative" if s < -0.15 else "neutral"


def _keywords(text: str, k: int = 10) -> list[str]:
    words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", text.lower()) if w not in STOP]
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]


def analyze(transcript: list[dict], lead_rules: dict | None = None) -> dict[str, Any]:
    turns = _turns(transcript)
    full = " ".join(t["text"] for t in turns)
    user_turns = [t["text"] for t in turns if t["role"] == "user"]
    agent_turns = [t["text"] for t in turns if t["role"] == "assistant"]

    s = _score(full)
    segments = []
    for i in range(0, len(turns), 2):
        chunk = turns[i : i + 2]
        text = " ".join(t["text"] for t in chunk)
        words = text.split()
        seg_score = _score(text)
        segments.append({
            "text": " ".join(words[:10]),
            "start_word": i * 10,
            "end_word": i * 10 + min(10, len(words)),
            "sentiment": _label(seg_score),
            "sentiment_score": seg_score,
        })
    segments = segments[:10]

    questions = [u for u in user_turns if "?" in u][:10]
    action_items = []
    for a in agent_turns:
        if re.search(r"(test drive|slot|call back|whatsapp|visit|booking|bhej)", a, re.I):
            action_items.append(a.strip()[:120])
    action_items = list(dict.fromkeys(action_items))[:10]

    misc = []
    for name, cues, weight in WEIGHTS:
        hit = any(c in full.lower() for c in cues)
        misc.append({"question": name, "answer": "yes" if hit else "not_discussed",
                     "weight": weight, "score": weight if hit else 0})

    intent_hits = sum(1 for c in INTENT if c in full.lower())
    qualification = sum(m["score"] for m in misc)
    if intent_hits >= 2 and qualification >= 40:
        lead = "HOT"
    elif intent_hits >= 1 or qualification >= 20:
        lead = "WARM"
    else:
        lead = "COLD"

    unanswered = []
    for i, t in enumerate(turns):
        if t["role"] == "user" and "?" in t["text"]:
            nxt = turns[i + 1]["text"] if i + 1 < len(turns) else ""
            if re.search(r"(check karta|confirm|pata nahi|not sure|available nahi)", nxt, re.I) or not nxt:
                unanswered.append(t["text"][:120])

    engagement = min(5, 1 + len(user_turns) // 3)
    clarity = 4 if not re.search(r"(umm|uhh|sorry, i)", " ".join(agent_turns), re.I) else 3
    handling = 5 if lead == "HOT" else 4 if lead == "WARM" else 2
    resolution = 4 if action_items else 2
    compliance = 5
    overall = round((clarity + engagement + handling + resolution + compliance) / 5, 1)
    cx = min(10, round(overall * 2 * 0.9 + engagement * 0.2, 1))

    summary_bits = []
    if user_turns:
        summary_bits.append(user_turns[0][:100])
    if action_items:
        summary_bits.append(f"Next step agreed: {action_items[0][:80]}")
    summary = ". ".join(summary_bits)[:240] or "No customer speech captured."

    return {
        "summary": summary,
        "sentiment": _label(s),
        "sentiment_score": s,
        "keywords": _keywords(full),
        "sentiment_segments": segments,
        "questions": questions,
        "action_items": action_items,
        "lead_status": lead,
        "miscellaneous": misc,
        "unanswered_questions": unanswered[:10],
        "audit_details": {
            "overall_score": overall,
            "customer_experience_score": cx,
            "dimensions": {
                "response_quality": clarity,
                "customer_engagement": engagement,
                "lead_handling": handling,
                "resolution_effectiveness": resolution,
                "compliance": compliance,
            },
            "issues": [] if overall >= 4 else ["Agent turns were short or discovery was incomplete."],
            "highlights": ["Clear next step" ] if action_items else [],
            "action_steps": {
                "immediate": "Send the agreed next step on WhatsApp." if action_items else "Call back the lead.",
                "short_term": "Confirm budget and timeline on the follow-up call.",
                "long_term": "Track which discovery question correlates with HOT leads.",
            },
        },
    }


LLM_PROMPT = """You are an assistant that generates valid JSON analysis of a sales call transcript.
Return exactly this JSON shape, most important information first, valid even if truncated:
{"summary": "", "sentiment": "positive|neutral|negative", "sentiment_score": 0.0,
 "keywords": [], "sentiment_segments": [{"text": "", "start_word": 0, "end_word": 0,
 "sentiment": "positive|neutral|negative", "sentiment_score": 0.0}],
 "questions": [], "action_items": [], "lead_status": "HOT|WARM|COLD",
 "miscellaneous": [{"question": "", "answer": "", "weight": 0, "score": 0}],
 "unanswered_questions": [], "audit_details": ""}
HOT = any real buying intent. WARM = interested but exploratory. COLD = no intent.
When unsure between two grades, choose the hotter one. Max 10 items per list."""


def analyze_with_llm(transcript: list[dict], complete) -> dict[str, Any]:
    """`complete(messages) -> str` is injected so this module stays provider-agnostic."""
    plain = "\n".join(f"{t['role']}: {t['text']}" for t in _turns(transcript))
    raw = complete([
        {"role": "system", "content": LLM_PROMPT},
        {"role": "user", "content": plain[-12000:]},
    ])
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if not m:
        return analyze(transcript)
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return analyze(transcript)
