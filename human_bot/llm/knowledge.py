"""Retrieval client for the kb/ service (website knowledge base).

Stdlib only, fail-soft: if the service is down or slow the call returns [] and the agent
answers from the persona rules with the strict "cannot verify" line instead of inventing.

    KB_URL=http://127.0.0.1:4100   (default)
    KB_TIMEOUT_S=2.5
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

KB_URL = os.environ.get("KB_URL", "http://127.0.0.1:4100").rstrip("/")
KB_TIMEOUT_S = float(os.environ.get("KB_TIMEOUT_S", "2.5"))
_STATUS: dict[str, object] = {"last_ok": None, "last_error": None}


def retrieve(website_id: str, query: str, k: int = 5, min_score: float | None = None,
             page_type: str | None = None) -> list[dict]:
    """Return [{score,url,title,page_type,heading,text}] or [] on any failure."""
    if not website_id or not query.strip():
        return []
    params = {"website": website_id, "q": query.strip()[:1000], "k": str(k)}
    if min_score is not None:
        params["min_score"] = str(min_score)
    if page_type:
        params["page_type"] = page_type
    url = f"{KB_URL}/api/knowledge/search?{urllib.parse.urlencode(params)}"
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=KB_TIMEOUT_S) as r:
            data = json.loads(r.read().decode("utf-8"))
        _STATUS["last_ok"] = time.time()
        _STATUS["last_error"] = None
        return data.get("results") or []
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as ex:
        _STATUS["last_error"] = f"{type(ex).__name__}: {ex}"[:200]
        sys.stderr.write(f"[kb] retrieval failed in {int((time.perf_counter()-t0)*1000)} ms: {_STATUS['last_error']}\n")
        return []


def knowledge_block(hits: list[dict], website_name: str, max_chars: int = 3500) -> str:
    """System-prompt block: verified website facts + the strict-KB rule. Empty hits still
    return the rule so the model knows there is nothing verified for this turn."""
    lines = [
        "## Verified website information (retrieved for this turn)",
        f"These passages come from {website_name}'s own website. For services, prices, locations, "
        "timings, offers and contact details, answer ONLY from them. Say which page it is from when "
        "the customer asks for detail.",
        "If the answer is not in these passages, say in the customer's language that you cannot verify "
        "it right now, and offer to connect them to the team or take a call-back — never guess, never "
        "invent a price, discount, EMI or availability.",
    ]
    used = 0
    for i, h in enumerate(hits, 1):
        text = " ".join((h.get("text") or "").split())
        tag = (" (customer-call notes: what our team actually told callers; 'Call note' is the raw note)"
               if (h.get("page_type") or "") == "qa" else "")
        entry = f"[{i}] {h.get('title') or ''} — {h.get('url') or ''}{tag}\n{text}"
        if used + len(entry) > max_chars:
            entry = entry[: max(0, max_chars - used)]
        if not entry.strip():
            break
        lines.append(entry)
        used += len(entry)
        if used >= max_chars:
            break
    if not hits:
        if _STATUS.get("last_error"):
            lines.append("(nothing could be retrieved this turn: treat every price, timing, location and offer as "
                         "unverified. Say in the customer's language that you will confirm that detail with the team; "
                         "never mention a knowledge base, a system or being unavailable)")
        else:
            lines.append("(nothing relevant was found on the website for this question)")
    return "\n".join(lines)


def status() -> dict:
    return {"url": KB_URL, **_STATUS}
