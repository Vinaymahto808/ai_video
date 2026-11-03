"""Service request flow: what Neha has collected, what she still needs, when to submit.

Pure functions (no network), unit-tested. The server (server/app.py) calls:

    slots = collect(transcript)                      # facts from the customer's own words
    block = prompt_block(slots, persona)              # instructions for the model this turn
    if is_confirmation(user_text) and pending: ...    # the server submits, never the model

Rules: ask one missing thing at a time; never invent a field; the request is sent only after the customer
says yes to the read-back; the customer hears the real reference from the backend or an honest "could
not send, the team will call" (never a fabricated confirmation).
"""
from __future__ import annotations

import re

REQUIRED = ("name", "phone", "car", "problem", "area")
OPTIONAL = ("registration", "when", "pickup")
LABEL_HI = {"name": "aapka naam", "phone": "mobile number", "car": "gaadi ka make aur model", "problem": "kya kaam karwana hai",
            "area": "aap kis area mein hain", "registration": "gaadi ka number", "when": "kab convenient rahega", "pickup": "pickup chahiye ya workshop aayenge"}

# spoken service -> ACR website service category (lookups/service-categories, 2026-11-03)
SERVICE_SLUG = {
    "AC repair": "car-ac-service-repair", "battery and tyre service": "car-battery", "brakes and clutch work": "car-brake-wheel-maintenance",
    "detailing": "car-care-detailing", "denting and painting": "car-denting-painting", "insurance claim repair": "car-insurance-claim",
    "lights and glass work": "car-lights-and-glass-work", "suspension check": "car-suspension-work", "engine and gearbox diagnosis": "car-repairs-inspection",
    "periodic service": "car-repairs-inspection",
}

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)")
REG_RE = re.compile(r"\b([A-Z]{2}[\s-]?\d{1,2}[\s-]?[A-Z]{1,3}[\s-]?\d{3,4})\b", re.I)
NAME_RE = re.compile(r"(?:mera naam|my name is|naam hai|name hai|main|i am|i'm|this is|bol raha hoon|bol rahi hoon)\s+([A-Za-z][a-z]{1,15}(?:\s+[A-Z][a-z]{1,15})?)", re.I)
NAME_STOP = {"neha", "auto", "car", "gurgaon", "delhi", "noida", "bol", "baat", "kar", "ek", "the", "ok", "haan", "nahi", "service", "sir", "madam",
             "hai", "hoon", "hu", "ji", "se", "raha", "rahi", "bolta", "bolti", "and", "aur", "mera", "meri"}
YES_RE = re.compile(r"^\W*(haan|ha|han|haan ji|ji haan|yes|yeah|ok|okay|theek hai|thik hai|sahi hai|bilkul|kar do|bhej do|bhej dijiye|send|confirm|done|sure|go ahead)\b", re.I)
NO_RE = re.compile(r"^\W*(nahi|na|no|mat|ruko|wait|cancel|galat|wrong|change)\b", re.I)
WHEN_RE = re.compile(r"\b(aaj|today|abhi|kal|tomorrow|parso|weekend|saturday|sunday|monday|tuesday|wednesday|thursday|friday|subah|morning|dopahar|afternoon|shaam|evening)\b", re.I)


def collect(transcript: list[dict]) -> dict[str, str]:
    """Facts from user turns only. Uses the same car/service/area tables as the offline script."""
    from llm.client import OfflineServiceBrain  # noqa: PLC0415
    out: dict[str, str] = {}
    user_turns = [t.get("text", "") for t in transcript if t.get("role") == "user"]
    user = " ".join(user_turns)
    low = " " + user.lower() + " "
    m = PHONE_RE.search(user)
    if m:
        out["phone"] = m.group(1)
    m = REG_RE.search(user)
    if m:
        out["registration"] = re.sub(r"[\s-]", "", m.group(1)).upper()
    for t in user_turns:
        m = NAME_RE.search(t)
        if not m:
            continue
        words = [w for w in m.group(1).split() if w.lower() not in NAME_STOP]  # "mera naam Rahul hai" -> Rahul
        if words:
            out["name"] = " ".join(words).title()
    for name in OfflineServiceBrain._MODELS:
        if f" {name} " in low or f" {name}," in low:
            out["car"] = name.title(); break
    else:
        for make in OfflineServiceBrain._MAKES:
            if f" {make} " in low or f" {make}," in low:
                out["car"] = make.title(); break
    for pat, label in OfflineServiceBrain._SERVICES:
        if re.search(pat, low):
            out["problem"] = label; break
    for key, label in OfflineServiceBrain._AREAS.items():
        if key in low:
            out["area"] = label; break
    m = WHEN_RE.search(user)
    if m:
        out["when"] = m.group(1).lower()
    if re.search(r"\b(pickup|pick up|uthwa|ghar se|doorstep)\b", low):
        out["pickup"] = "yes"
    elif re.search(r"\b(workshop (aa|visit)|khud aa|main aa jaunga|main aa jaungi|le aaunga|drop kar dunga)", low):
        out["pickup"] = "no"
    return out


def missing(slots: dict) -> list[str]:
    return [k for k in REQUIRED if not slots.get(k)]


def wants_request(transcript: list[dict], slots: dict) -> bool:
    """A service/repair/booking conversation, not a price or timing enquiry only."""
    user = " ".join(t.get("text", "") for t in transcript if t.get("role") == "user").lower()
    if slots.get("problem") or slots.get("car"):
        return True
    return bool(re.search(r"\b(service|servicing|repair|book|booking|appointment|pickup|karwana|karwani|karwa|request|problem|dikkat)\b", user))


def summary_line(slots: dict) -> str:
    bits = [slots.get("name"), slots.get("car"), slots.get("problem"), slots.get("area") and f"{slots['area']} se", slots.get("when"), "free pickup" if slots.get("pickup") == "yes" else None]
    return ", ".join(b for b in bits if b)


def prompt_block(slots: dict, pending_confirm: bool) -> str:
    """System-prompt section: what is known, what to ask next, when to read back and ask for a yes."""
    need = missing(slots)
    known = "; ".join(f"{k}: {v}" for k, v in slots.items() if v) or "nothing yet"
    lines = ["## Service request (you file it yourself; the system sends it to the workshop after the customer says yes)",
             "You CAN take the request on this call. Never send the customer to the website form or ask them to fill anything.",
             f"Known so far: {known}."]
    if need:
        lines.append(f"Still needed, ask ONLY the first one now, in one short question: {', '.join(LABEL_HI[k] for k in need)}.")
        lines.append("Do not invent any of these. A phone number must be read back digit by digit once you have it.")
    elif not pending_confirm:
        lines.append("Everything is known. Read it back in one sentence and ask exactly: \"Main yeh request bhej doon?\" Then wait.")
    else:
        lines.append("You already asked for confirmation. If the customer agrees, say nothing about sending (the system does it); if they correct something, update it and read back again.")
    return "\n".join(lines)


def is_yes(text: str) -> bool:
    return bool(YES_RE.search(text or "")) and not NO_RE.search(text or "")


def is_no(text: str) -> bool:
    return bool(NO_RE.search(text or ""))


def asked_confirmation(assistant_text: str) -> bool:
    return bool(re.search(r"bhej\s*d(oo|u)n|request (bhej|send)|confirm kar", assistant_text or "", re.I))
