"""LLM transport. Zero third-party deps (urllib only).

Provider order:
  1. OPENAI_API_KEY / OPENAI_BASE_URL  (any OpenAI-compatible: OpenAI, Groq, Together, vLLM, Ollama)
  2. GROQ_API_KEY
  3. GEMINI_API_KEY
  4. OfflineBrain  -- deterministic sales state machine, so the demo always runs

Keys are read from the environment first, then from ../../voice_clone/.env.
"""

from __future__ import annotations

import json
import os
import uuid
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterator


def _load_env() -> None:
    env = Path(__file__).resolve().parents[2] / "voice_clone" / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env()

OPENAI_BASE = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_KEY = os.getenv("openai_api_key") or os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("HUMAN_BOT_MODEL", "gpt-4o-mini")
GROQ_KEY = os.getenv("groq_api_key") or os.getenv("GROQ_API_KEY")
# qwen/qwen3-4b-instant streams the first token ~2x faster than 27b with
# near-equal Hinglish quality for short service-advisor replies. Override with
# HUMAN_BOT_GROQ_MODEL=qwen/qwen3.8-27b if you need the larger model.
GROQ_MODEL = os.getenv("HUMAN_BOT_GROQ_MODEL", "qwen/qwen3-4b-instant")
GEMINI_KEY = os.getenv("gemini_api_key") or os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("HUMAN_BOT_GEMINI_MODEL", "gemini-3.8-flash")

# Groq sits behind Cloudflare: the default python-urllib UA gets error 1010 instantly
UA = "human-bot/1.0 (python-urllib)"


class LLMError(RuntimeError):
    pass


# Circuit breaker. Voice turns cannot afford long dead windows: a failed provider is
# skipped briefly so the next customer turn falls back fast, then the provider is
# retried. Config failures (401/403/404) held 5 min; 429 quota 30 s; transient
# network blips 5 s (first two failures) then 30 s only if it keeps failing.
_DEAD: dict[str, tuple[float, str, float]] = {}
_FAILS: dict[str, int] = {}  # consecutive failures per provider


def _is_dead(name: str) -> bool:
    rec = _DEAD.get(name)
    return bool(rec) and (time.time() - rec[0]) < rec[2]


def _mark_dead(name: str, why: str) -> None:
    n = _FAILS[name] = _FAILS.get(name, 0) + 1
    if "429" in why:
        ttl = 30.0
    elif any(c in why for c in ("401", "403", "404")):
        ttl = 300.0
    else:
        # transient blip: back off fast, but not so fast that a dead endpoint
        # hammers every turn; 5 s after first two failures, 30 s after that.
        ttl = 5.0 if n < 3 else 30.0
    if name not in _DEAD:
        print(f"[llm] '{name}' disabled for {int(ttl)} s: {why}", file=sys.stderr, flush=True)
    _DEAD[name] = (time.time(), why, ttl)


def _mark_alive(name: str) -> None:
    _FAILS.pop(name, None)
    _DEAD.pop(name, None)


# Socket timeout per LLM request. Voice turns need a first token in under ~2 s:
# Groq streams in ~0.2 s; anything slower than 3 s means the provider is stalled
# and we fall back immediately instead of making the customer wait.
LLM_TIMEOUT_S = int(os.getenv("HUMAN_BOT_LLM_TIMEOUT_S", "3"))

# --------------------------------------------------------------------------- response cache
# Exact-match cache for short, high-frequency customer questions. Avoids a network
# round-trip entirely for greetings, price questions and status checks; the first
# time a phrase is heard it hits the LLM, every time after it is served from RAM.
_RESPONSE_CACHE: dict[str, tuple[float, str]] = {}
_RESPONSE_CACHE_TTL = 3600.0  # 1 hour


def _cache_get(text: str) -> str | None:
    rec = _RESPONSE_CACHE.get(text.strip().lower())
    if not rec:
        return None
    ts, reply = rec
    if time.time() - ts > _RESPONSE_CACHE_TTL:
        _RESPONSE_CACHE.pop(text.strip().lower(), None)
        return None
    return reply


def _cache_put(text: str, reply: str) -> None:
    key = text.strip().lower()
    if len(key) > 200:  # only cache short questions
        return
    _RESPONSE_CACHE[key] = (time.time(), reply)


def cache_reply(text: str, reply: str) -> None:
    """Cache an LLM reply so the same customer question is answered from RAM next time."""
    _cache_put(text, reply)


def _post(url: str, payload: dict, key: str, timeout: int = LLM_TIMEOUT_S):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": UA},
    )
    return urllib.request.urlopen(req, timeout=timeout)


def stream_openai(messages: list[dict], base: str, key: str, model: str) -> Iterator[str]:
    payload = {"model": model, "messages": messages, "stream": True, "temperature": 0.5, "max_tokens": 160}
    with _post(f"{base}/chat/completions", payload, key) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
            except (KeyError, IndexError, json.JSONDecodeError):
                continue
            if delta:
                yield delta


def stream_gemini(messages: list[dict]) -> Iterator[str]:
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
        for m in messages
        if m["role"] != "system"
    ]
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.5, "maxOutputTokens": 160},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:streamGenerateContent?alt=sse"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),  # key in a header, never in the URL (logs)
                                 headers={"Content-Type": "application/json", "User-Agent": UA, "x-goog-api-key": GEMINI_KEY})
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_S) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            try:
                chunk = json.loads(line[5:].strip())
                for cand in chunk.get("candidates", []):
                    for part in cand.get("content", {}).get("parts", []):
                        if part.get("text"):
                            yield part["text"]
            except json.JSONDecodeError:
                continue


# Provider order. Measured 2026-11-02 on this connection: Groq streams the first token in ~0.2 s and a
# whole 2-sentence Hinglish reply in ~0.3 s; OpenAI gpt-4o-mini took ~2 s and was rate-limited (429);
# Gemini took 40 s. Latency is what the customer feels, so Groq goes first. Override with
# HUMAN_BOT_LLM_ORDER="openai-compatible,groq,gemini".
LLM_ORDER = [p.strip() for p in os.getenv("HUMAN_BOT_LLM_ORDER", "groq,openai-compatible,gemini").lower().split(",") if p.strip()]


def provider_name() -> str:
    labels = {
        "openai-compatible": f"openai-compatible:{OPENAI_MODEL}",
        "groq": f"groq:{GROQ_MODEL}",
        "gemini": f"gemini:{GEMINI_MODEL}",
    }
    for name in LLM_ORDER:
        if name not in labels:
            continue
        configured = {"openai-compatible": OPENAI_KEY, "groq": GROQ_KEY, "gemini": GEMINI_KEY}[name]
        if configured and not _is_dead(name):
            return labels[name]
    return "offline"


def cached_reply(text: str) -> str | None:
    """Instant answer for a repeated short question; None when not cached."""
    return _cache_get(text)


def stream_reply(messages: list[dict], fallback: "OfflineBrain | None" = None) -> Iterator[str]:
    """Try each configured provider; a provider that fails is skipped for a while.

    The first *non-blank* chunk is pulled before committing, so a dead endpoint or a
    reasoning-only/empty completion never emits a half sentence into the TTS pipeline.
    An empty completion is retried once, then the next provider is tried.

    Repeated short questions (greetings, price asks, status checks) are served from
    the response cache with zero network latency.
    """
    user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_text = m.get("content", "")
            break
    if user_text:
        hit = _cache_get(user_text)
        if hit is not None:
            yield hit
            return

    available: dict[str, Callable[[], Iterator[str]]] = {}
    if OPENAI_KEY:
        available["openai-compatible"] = lambda: stream_openai(messages, OPENAI_BASE, OPENAI_KEY, OPENAI_MODEL)
    if GROQ_KEY:
        available["groq"] = lambda: stream_openai(messages, "https://api.groq.com/openai/v1", GROQ_KEY, GROQ_MODEL)
    if GEMINI_KEY:
        available["gemini"] = lambda: stream_gemini(messages)
    candidates = [(n, available[n]) for n in LLM_ORDER if n in available]
    candidates += [(n, f) for n, f in available.items() if n not in LLM_ORDER]  # anything not listed goes last

    for name, make in candidates:
        if _is_dead(name):
            continue
        gen = None
        first = None
        for _attempt in (1, 2):
            try:
                gen = make()
                for tok in gen:
                    if tok and tok.strip():
                        first = tok
                        break
            except Exception as ex:  # noqa: BLE001
                _mark_dead(name, f"{type(ex).__name__}: {str(ex)[:120]}")
                gen, first = None, None
                break
            if first:
                break
        if not first or gen is None:
            continue  # empty twice: leave the provider enabled, just move on
        _mark_alive(name)
        yield first
        yield from gen
        return

    if fallback is None:
        raise LLMError("no live provider and no offline fallback")
    yield from fallback.stream(messages)


DEFAULT_PRICES = {
    "glanza": "16.46 lakh se 19.64 lakh, ex-showroom Delhi",
    "rumion": "10.44 lakh se 13.73 lakh, ex-showroom Delhi",
    "taisor": "7.74 lakh se 13.05 lakh, ex-showroom Delhi",
    "hyryder": "11.34 lakh se 20.19 lakh, ex-showroom Delhi",
    "innova": "19.99 lakh se 26.30 lakh, ex-showroom Delhi",
}


class OfflineBrain:
    """Deterministic, rule-driven sales persona. No network, no model, no hallucinations.

    Brand-aware: pass the persona dict and it uses that brand's name, showroom,
    catalogue and price list, so the fallback stays correct for every dealership.
    """

    def __init__(self, persona: dict | None = None) -> None:
        p = persona or {}
        self.name = p.get("display_name") or "Neha"
        self.company = p.get("company") or "Galaxy Toyota"
        self.location = p.get("location") or "Moti Nagar, New Delhi"
        models = (p.get("offline") or {}).get("models") or {}
        self._PRICE = {k.lower(): v for k, v in models.items()} or dict(DEFAULT_PRICES)
        catalogue = (p.get("sales") or {}).get("catalogue") or []
        self.recommend = catalogue[:2] or [m.title() for m in list(self._PRICE)[:2]]
        self.slots: dict[str, str] = {}

    def _remember(self, text: str) -> None:
        t = text.lower()
        m = re.search(r"(\d{1,2})\s*(?:lakh|lac)", t)
        if m:
            self.slots["budget"] = f"{m.group(1)} lakh"
        m = re.search(r"(\d+)\s*(?:log|people|seats|member|family)", t)
        if m:
            self.slots["seats"] = m.group(1)
        for f in ("petrol", "diesel", "cng", "hybrid", "electric"):
            if f in t:
                self.slots["fuel"] = f
        for model in self._PRICE:
            if model in t:
                self.slots["model"] = model

    def stream(self, messages: list[dict]) -> Iterator[str]:
        # Earlier user turns were answered by the live model while this script was idle: read their facts
        # (car, area, phone, name...) before replying, so a fallback mid-conversation does not re-ask them.
        users = [m["content"] for m in messages if m["role"] == "user"]
        seen = getattr(self, "_seen_turns", 0)
        for text in users[seen:-1]:
            self._remember(text)
        self._seen_turns = len(users)
        yield self.reply(users[-1] if users else "")

    def reply(self, user: str) -> str:
        self._remember(user)
        t = user.lower().strip()

        if not t or re.search(r"\b(hi|hello|namaste|hey)\b", t):
            return f"Namaste! Main {self.name}, {self.company} se. Aap kaunsi car dekh rahe hain?"

        if any(w in t for w in ("human", "real person", "agent", "insaan", "baat karni")):
            return f"Bilkul. Main aapko abhi {self.company} ke showroom consultant se connect kar deti hoon."

        if re.search(r"\b(price|kitne|kitni|cost|rate)\b", t):
            model = self.slots.get("model")
            if model and model in self._PRICE:
                return f"{model.title()} ka price {self._PRICE[model]} hai. Aap test drive lena chahenge?"
            return "Kaunsa model dekh rahe hain? Uska exact price main abhi check kar deti hoon."

        if any(w in t for w in ("test drive", "td", "drive")):
            return f"Zaroor. {self.location} showroom par slot available hai. Kaunsi date theek rahegi?"

        if re.search(r"\b(emi|finance|loan|instal?ment)\b", t):
            return "EMI ho jayegi, down payment pe depend karegi. Kitna down payment soch rahe hain?"

        if any(w in t for w in ("thank", "thanks", "dhanyavaad", "bye")):
            return "Aapka time diya, thank you. Main details WhatsApp par bhej deti hoon."

        if self.slots.get("budget") and "recommend" not in t:
            a = self.recommend[0]
            b = self.recommend[1] if len(self.recommend) > 1 else a
            return (
                f"Aapke budget {self.slots['budget']} mein {a} aur {b} dono fit hote hain. "
                f"Family ke liye {b} zyada comfortable hai. Test drive dekh lein?"
            )

        if "family" in t or self.slots.get("seats"):
            return "Family ke liye sahi choice hai. Aapka budget around kitna hai?"

        return "Samajh gayi. Aap city mein zyada chalate hain ya highway par?"


class OfflineServiceBrain(OfflineBrain):
    """Rule-driven *service advisor* script (workshops such as Auto Car Repair).

    Same no-network guarantee as OfflineBrain, but the flow follows the persona's discovery
    order (car -> problem/service -> area -> time) and the next step is a free pickup or a
    call-back, never a test drive. Prices, timings and addresses are quoted only from the
    website passages the server retrieved for this turn (``context``); otherwise the script
    says it cannot verify and offers a call-back, which keeps the strict-KB rule even offline.
    """

    _MAKES = (
        "maruti", "suzuki", "hyundai", "honda", "toyota", "tata", "mahindra", "kia", "mg", "skoda",
        "volkswagen", "vw", "renault", "nissan", "ford", "mercedes", "bmw", "audi", "jeep", "volvo",
        "citroen", "fiat", "chevrolet", "datsun", "isuzu", "lexus", "mini", "porsche",
    )
    _MODELS = (
        "swift", "dzire", "baleno", "brezza", "ertiga", "wagonr", "wagon r", "alto", "celerio", "ciaz",
        "creta", "venue", "i20", "i10", "verna", "exter", "alcazar", "city", "amaze", "jazz", "wr-v",
        "innova", "fortuner", "glanza", "hyryder", "nexon", "punch", "harrier", "safari", "altroz",
        "tiago", "xuv700", "xuv300", "scorpio", "thar", "bolero", "seltos", "sonet", "carens",
        "hector", "astor", "kushaq", "slavia", "taigun", "polo", "kwid", "triber", "magnite", "ecosport",
    )
    _AREAS = {
        "gurgaon": "Gurgaon", "gurugram": "Gurgaon", "delhi": "Delhi", "noida": "Noida",
        "faridabad": "Faridabad", "ghaziabad": "Ghaziabad", "greater noida": "Greater Noida",
    }
    _SERVICES = (
        (r"\b(ac|a\.c\.|aircon|cooling|thanda)\b", "AC repair"),
        (r"\b(dent|denting|paint|painting|scratch|body ?shop|bumper)\b", "denting and painting"),
        (r"\b(brake|brakes|clutch)\b", "brakes and clutch work"),
        (r"\b(suspension|shocker|shock|jhatka|awaaz|noise|rattl)\w*", "suspension check"),
        (r"\b(engine|gearbox|gear|overheat|smoke|dhuan|start nahi|starting)\b", "engine and gearbox diagnosis"),
        (r"\b(battery|tyre|tire|puncture|wheel|alignment|balancing)\b", "battery and tyre service"),
        (r"\b(light|headlight|glass|windshield|wiper|mirror)\b", "lights and glass work"),
        (r"\b(detail|detailing|polish|wash|ceramic|coating|cleaning)\w*", "detailing"),
        (r"\b(insurance|insurence|insurans|bima|beema|claim|clam|claime|clame|accident|cashless|tuta|toota)\b", "insurance claim repair"),
        (r"\b(service|servicing|oil|filter|periodic|general check|checkup|check-up)\b", "periodic service"),
    )
    _INFO = re.compile(
        r"\b(price|prices|kitna|kitne|kitni|cost|charge|charges|chrg|rate|rates|quote|estimate|offer|discount|"
        r"kharcha|kharch|paisa|paise|rupay|rupaye|rupees|padega|prega|pdega|lagega|lgega|lagenge|"
        r"timing|timings|time|open|close|khula|band|hours|address|kahan|kaha|where|location|branch|"
        r"warranty|guarantee|contact|number|phone)\b"
    )

    def __init__(self, persona: dict | None = None) -> None:
        super().__init__(persona)
        p = persona or {}
        g = p.get("guardrails") or {}
        self.handoff = g.get("handoff_line") or f"Main aapko abhi {self.company} ki team se connect kar deti hoon."
        self.areas = ["Gurgaon", "Delhi", "Noida"]
        self.context: list[dict] = []  # KB hits for the current turn, set by the server
        self.offered = False  # a pickup/call-back was offered last turn
        self.asked_reg = False  # the registration number is asked once, never insisted on
        self.last_reply = ""  # what the brain just said: the question the customer is answering

    # -- slot filling ---------------------------------------------------------------
    def _remember(self, text: str) -> None:
        t = " " + text.lower() + " "
        for name in self._MODELS:
            if f" {name} " in t or f" {name}," in t:
                self.slots["car"] = name.title()
                break
        else:
            for make in self._MAKES:
                if f" {make} " in t or f" {make}," in t:
                    self.slots.setdefault("car", make.title())
                    break
        for pat, label in self._SERVICES:
            if re.search(pat, t):
                self.slots["service"] = label
                break
        for key, label in self._AREAS.items():
            if key in t:
                self.slots["area"] = label
                break
        m = re.search(r"\b(aaj|today|abhi|kal|tomorrow|weekend|saturday|sunday|monday|tuesday|wednesday|thursday|friday|parso)\b", t)
        if m:
            self.slots["when"] = m.group(1)
        # name and phone: the same patterns the server's service-request collector uses
        from crm.service_request import NAME_RE, NAME_STOP, PHONE_RE, REG_RE, _bare_name  # noqa: PLC0415
        m = PHONE_RE.search(text)
        if m:
            self.slots["phone"] = m.group(1)
        elif not self.slots.get("phone"):
            # spoken digits: STT writes "nine eight one one ..." as words far more often than as
            # numerals; join the word-digits and read the last 10 (the +91 prefix may be spoken too)
            word_digits = re.findall(r"\b(zero|one|two|three|four|five|six|seven|eight|nine|dasa?|ek|do|teen|char|chara?|paanch|panch|chhah|cheh|saat|aath|nau)\b", text)
            table = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                     "six": "6", "seven": "7", "eight": "8", "nine": "9", "ek": "1", "do": "2",
                     "teen": "3", "char": "4", "chara": "4", "paanch": "5", "panch": "5",
                     "chhah": "6", "cheh": "6", "saat": "7", "aath": "8", "nau": "9", "das": "0"}
            digits = "".join(table.get(w) for w in word_digits)
            if len(digits) >= 10 and digits[-10] in "6789":
                self.slots["phone"] = digits[-10:]
        m = REG_RE.search(text)
        if m:  # "DL 01 AB 1234": the registration number, asked together with the model
            self.slots["registration"] = re.sub(r"[\s-]", "", m.group(1)).upper()
        m = NAME_RE.search(text)
        if m:
            words = [w for w in m.group(1).split() if w.lower() not in NAME_STOP]
            if words:
                self.slots["name"] = " ".join(words).title()
        if not self.slots.get("name") and getattr(self, "last_reply", ""):
            # the bare-name answer: the bot just asked "Aapka naam kya hai?" and the customer said
            # the name alone ("vinay" — STT also returns "VInay"/"VINAY"). _bare_name only fires when
            # the assistant turn asked for the name (it checks the "naam" hint itself).
            name = _bare_name([{"role": "assistant", "text": self.last_reply}, {"role": "user", "text": text}])
            if name:
                self.slots["name"] = name

    def _next_question(self) -> str:
        s = self.slots
        if not s.get("car"):
            return "Aapki car ka model aur number kya hai?"
        if not s.get("registration") and not self.asked_reg:
            self.asked_reg = True  # asked once; a customer who does not have it handy is not asked again
            return "Gaadi ka registration number bhi bata dijiye, record ke liye."
        if not s.get("service"):
            return "Gaadi mein kya problem hai, ya kaunsi service karwani hai?"
        if not s.get("area"):
            return f"Aap kis area mein hain, {', '.join(self.areas[:-1])} ya {self.areas[-1]}?"
        if not s.get("name"):
            return "Aapka naam kya hai?"
        if not s.get("phone"):
            return "Aapka mobile number bata dijiye, team usi par call karegi."
        if not s.get("when"):
            self.offered = True
            return "Kab convenient rahega, aaj ya kal? Free pickup arrange kar deti hoon."
        # everything known: read it back and ask for the go-ahead (the server files the request on "haan")
        self.offered = True
        digits = " ".join(s["phone"])
        car = f"{s['car']} {' '.join(s['registration'])}" if s.get("registration") else s["car"]  # spelt out for speech
        return (f"{s['name']} ji, {car} ki {s['service']}, {s['area']} se {s['when']} pickup, number {digits}. "
                "Main yeh request bhej doon?")

    _STOP = {"kitna", "kitne", "kitni", "price", "kharcha", "padega", "lagega", "karne", "karna", "hoga", "hota", "hai", "mein", "kya",
             "paisa", "paise", "rupees", "cost", "charge", "rate", "aapke", "yahan", "mere", "meri", "gaadi", "gadi", "car"}

    def _from_website(self, user: str = "") -> str | None:
        """First retrieved passage that is about the thing asked, trimmed to a spoken length; None otherwise.
        A price question must not be answered with another service's price: the hit has to share a content
        word (battery, claim, painting...) with the question, or it is skipped."""
        words = {w.strip(".,!?;:()\"'") for w in (user or "").lower().split()}
        keys = {w for w in words if len(w) >= 4 and w not in self._STOP}
        claim_q = bool(keys & {"claim", "insurance", "cashless"})
        for h in self.context or []:
            if keys:
                head = ((h.get("heading") or "") + " " + (h.get("title") or "")).lower()
                blob = head + " " + (h.get("text") or "").lower()
                if not any(k in blob for k in keys):
                    continue
                if claim_q and "claim" not in head:   # a claim question is answered by a claim row, not a towing/RSA aside
                    continue
            words = " ".join((h.get("text") or "").split()).split(" ")
            if len(words) < 4:
                continue
            first_line = (h.get("text") or "").split("\n", 1)[0]
            # a call note that records "nothing was said" or still speaks of the agent is not an answer to give
            if re.search(r"\b(agent|advisor) (ne|customer|workshop)|nahi bataya|not (given|provided|stated)|call mein nahi", first_line, re.I):
                continue
            if (h.get("page_type") or "") == "qa":
                # customer-call knowledge (kb/src/ingest_qa.js): the first line is already Neha's own answer
                first = (h.get("text") or "").split("\n", 1)[0].strip()
                return first if first.endswith((".", "!", "?")) else first + "."
            snippet = " ".join(words[:38]).rstrip(",;:-")
            if not snippet.endswith((".", "!", "?")):
                snippet += "."
            return f"Website ke hisaab se: {snippet} Detail ke liye main team se call karwa deti hoon?"
        return None

    # -- turn -----------------------------------------------------------------------
    # Typed Hinglish is full of misspellings ("clam", "prega", "servic"): map the common ones onto the
    # vocabulary the rules know, and fuzzy-match longer words against the keyword list (one wrong letter).
    _TYPO = {"clam": "claim", "claime": "claim", "clame": "claim", "insurence": "insurance", "insurans": "insurance",
             "prega": "padega", "pdega": "padega", "lgega": "lagega", "kharch": "kharcha", "servic": "service", "servce": "service",
             "sarvice": "service", "batry": "battery", "batery": "battery", "tyer": "tyre", "tire": "tyre", "brek": "brake",
             "cluch": "clutch", "pikup": "pickup", "pick-up": "pickup", "warrenty": "warranty", "warantee": "warranty",
             "garanty": "guarantee", "acident": "accident", "paintng": "painting", "estimat": "estimate"}
    _VOCAB = ("insurance", "claim", "cashless", "service", "servicing", "pickup", "price", "warranty", "guarantee", "battery",
              "tyre", "brake", "clutch", "denting", "painting", "engine", "gearbox", "suspension", "accident", "estimate",
              "discount", "offer", "timing", "address", "location", "booking", "appointment", "repair", "problem")

    @classmethod
    def normalise(cls, text: str) -> str:
        import difflib  # noqa: PLC0415
        out = []
        for w in text.lower().split():
            core = w.strip(".,!?;:()\"'")
            if not core:
                out.append(w)
                continue
            fixed = cls._TYPO.get(core)
            if fixed is None and len(core) >= 5 and core not in cls._VOCAB and core.isalpha():
                m = difflib.get_close_matches(core, cls._VOCAB, n=1, cutoff=0.84)
                fixed = m[0] if m else None
            out.append(w.replace(core, fixed) if fixed else w)
        return " ".join(out)

    def reply(self, user: str) -> str:
        user = self.normalise(user)
        before = dict(self.slots)
        self._remember(user)
        new = [k for k in self.slots if before.get(k) != self.slots.get(k)]  # facts this turn added
        t = " " + user.lower().strip() + " "
        out = self._reply(user, t, new)
        self.last_reply = out  # the next turn is an answer to THIS question (bare-name capture)
        return out

    def _reply(self, user: str, t: str, new: list[str]) -> str:
        if not t.strip() or re.search(r"\b(hi|hello|namaste|namaskar|hey)\b", t):
            return f"Namaste! Main {self.name}, {self.company} se. Aapki gaadi mein kya problem hai, ya kaunsi service chahiye?"

        if re.search(r"\b(human|real person|agent|insaan|manager|complaint|shikayat|dispute|legal|emergency)\b", t) or "baat karni" in t:
            return self.handoff

        if re.search(r"\b(thank|thanks|thanku|bye|ok bye|theek hai bye)\b", t):
            self.offered = False
            return "Thank you ji. Pickup confirm hote hi main details WhatsApp par bhej deti hoon."

        if self.offered and re.fullmatch(r"\s*(haan|haan ji|ha|yes|ok|okay|theek hai|theek|sure|done|kar do|karo|book karo)\s*[.!]*\s*", t):
            self.offered = False
            return "Done ji. Team aapko thodi der mein call karke pickup time confirm karegi. Koi aur help?"

        if re.search(r"\b(pickup|pick up|drop|ghar se|doorstep)\b", t):
            if self.slots.get("area"):
                return f"Haan ji, {self.slots['area']} mein free pickup and drop hai. " + self._next_question()
            return "Haan ji, free pickup and drop hai. " + self._next_question()

        if self._INFO.search(t):
            web = self._from_website(t)
            if web:
                return web
            if re.search(r"\b(insurance|claim|cashless|accident)\b", t):
                return ("Claim ka kaam cashless hota hai: insurance company ka surveyor hum coordinate karte hain, aap sirf policy ke "
                        "hisaab se deductible aur depreciation (agar zero-dep nahi hai) dete hain. Exact amount survey ke baad pata chalta "
                        "hai. Policy aur damage ki photo WhatsApp kar dijiye. " + self._next_question())
            if re.search(r"\b(address|kahan|kaha|where|location|branch)\b", t):
                return (f"Hamare workshops {', '.join(self.areas[:-1])} aur {self.areas[-1]} mein hain. "
                        "Aap kis area mein hain? Main nearest workshop ka address bhijwa deti hoon.")
            return ("Exact figure gaadi aur kaam par depend karta hai, main abhi verify nahi kar paa rahi. "
                    "Team se quote dilwa deti hoon. " + self._next_question())

        if self.slots.get("service") and "service" in self.slots and re.search(self._SERVICES_UNION, t):
            label = self.slots["service"]
            lead = f"{label[0].upper() + label[1:]} ho jayegi, genuine parts ke saath."
            return lead + " " + self._next_question()

        if self.slots.get("car") and re.search(self._MAKES_UNION, t):
            return "Theek hai ji. " + self._next_question()

        if self.slots.get("area") and re.search("|".join(self._AREAS), t):
            return f"{self.slots['area']} mein free pickup and drop available hai. " + self._next_question()

        if self.slots.get("when") and re.search(r"\b(aaj|today|abhi|kal|tomorrow|weekend|parso)\b", t):
            return self._next_question()

        if new:  # the customer answered the question (name, number, time...): acknowledge, move on
            ack = "Number note kar liya. " if "phone" in new else "Theek hai ji. "
            return ack + self._next_question()
        # Nothing recognised (noise, a mis-heard word): say so, then ask the next discovery question.
        return "Maaf kijiye, main theek se samajh nahi paayi. " + self._next_question()

    _SERVICES_UNION = "|".join(p for p, _ in _SERVICES)
    _MAKES_UNION = r"\b(" + "|".join(re.escape(m) for m in _MAKES + _MODELS) + r")\b"


def offline_brain(persona: dict | None = None) -> OfflineBrain:
    """Pick the rule-driven fallback for a persona: service advisor for workshops, sales otherwise."""
    if (persona or {}).get("business_type", "").lower() == "service":
        return OfflineServiceBrain(persona)
    return OfflineBrain(persona)


# --------------------------------------------------------------------------- voice
# Female Indian (Delhi NCR) voice, one voice for Hindi, Hinglish and English.
#
# Gemini TTS (Kore) is the default: google's voices are not locale-locked, so Hindi/Hinglish text comes back
# with a natural Indian accent and English words keep an Indian delivery. Groq's Canopy Orpheus is English-only
# (a Hinglish line gets an American accent), so it stays as an option for English-only use. The browser Web
# Speech voice is the last resort — web/index.html and web/embed.html now pick a female en-IN/hi-IN voice.
#
# Free-tier Gemini allows ~10 speech clips/day; server/app.py caches /tts by text, so repeats are free. When
# the quota is gone the client falls back to the browser voice instead of failing the turn.

TTS_PROVIDER = (os.getenv("HUMAN_BOT_TTS_PROVIDER") or ("gemini" if GEMINI_KEY else "groq")).lower()
GEMINI_TTS_MODEL = os.getenv("HUMAN_BOT_GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts")
GEMINI_TTS_VOICE = os.getenv("HUMAN_BOT_GEMINI_TTS_VOICE", "Kore")
TTS_MODEL = os.getenv("HUMAN_BOT_TTS_MODEL", "canopylabs/orpheus-v1-english")  # groq only
TTS_VOICE = os.getenv("HUMAN_BOT_TTS_VOICE", "hannah")  # groq only


class TTSUnavailable(RuntimeError):
    pass


def _wav(pcm: bytes, rate: int = 24000) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _synthesize_gemini(text: str) -> bytes:
    import base64

    if not GEMINI_KEY:
        raise TTSUnavailable("no gemini key")
    # No style note: the 3.x speech models read a note aloud instead of following it (measured 2026-10-29).
    body = {
        "contents": [{"parts": [{"text": text[:2000]}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": GEMINI_TTS_VOICE}}},
        },
    }
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TTS_MODEL}:generateContent",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY, "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "ignore").replace("\n", " ")
        raise TTSUnavailable(f"gemini HTTP {e.code}: {detail}") from e
    except Exception as e:  # noqa: BLE001
        raise TTSUnavailable(f"gemini {type(e).__name__}: {e}") from e
    try:
        part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        raw = base64.b64decode(part["data"])
        mime = str(part.get("mimeType", ""))
    except (KeyError, IndexError, ValueError) as e:
        raise TTSUnavailable(f"gemini: no audio in response: {json.dumps(data)[:200]}") from e
    if raw[:4] == b"RIFF":  # 3.x models return a complete WAV
        return raw
    rate = 24000
    for item in mime.split(";"):
        if item.strip().startswith("rate="):
            rate = int(item.split("=", 1)[1])
    return _wav(raw, rate)


def _synthesize_groq(text: str, voice: str | None, fmt: str) -> bytes:
    if not GROQ_KEY:
        raise TTSUnavailable("no groq key")
    payload = {
        "model": TTS_MODEL,
        "voice": voice or TTS_VOICE,
        "input": text[:1200],
        "response_format": fmt,
    }
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/audio/speech",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {GROQ_KEY}", "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "ignore").replace("\n", " ")
        raise TTSUnavailable(f"HTTP {e.code}: {detail}") from e
    except Exception as e:  # noqa: BLE001
        raise TTSUnavailable(f"{type(e).__name__}: {e}") from e


# Microsoft neural voices through the `edge-tts` package: Indian female, Delhi-NCR-neutral delivery, no quota.
# ONE voice for every answer (decision 2026-11-01): en-IN Neerja reads Roman-script Hinglish with Indian English
# prosody, so "gaadi ki service" sounds like Gurgaon, not a textbook. hi-IN Swara is used only when a reply is
# mostly Devanagari (customer typed Hindi script); a stray Hindi word never flips the voice any more.
# Unofficial endpoint: fine for the demo; for production use the same voices on Azure Speech.
EDGE_VOICE_EN = os.getenv("HUMAN_BOT_EDGE_VOICE_EN", "en-IN-NeerjaNeural")
EDGE_VOICE_HI = os.getenv("HUMAN_BOT_EDGE_VOICE_HI", "hi-IN-SwaraNeural")
EDGE_RATE = os.getenv("HUMAN_BOT_EDGE_RATE", "+2%")
EDGE_PITCH = os.getenv("HUMAN_BOT_EDGE_PITCH", "+0Hz")
# Voice style (2026-11-02, "voice sounds robotic"):
#   hindi      (default) Hindi words are transliterated to Devanagari (llm/hindi_script.py) and hi-IN Swara reads
#              them with real Hindi phonetics; English words stay Latin and get her Indian-English accent.
#   neerja     the 2026-11-01 one-voice setup: en-IN Neerja reads Roman-script Hinglish as Indian English.
#   expressive en-IN NeerjaExpressive on Roman script: livelier, but ~0.8 s slower to first audio.
# Switch with HUMAN_BOT_VOICE_STYLE; sample clips of all three are in web/samples/.
VOICE_STYLE = (os.getenv("HUMAN_BOT_VOICE_STYLE") or "hindi").lower()
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def _mostly_devanagari(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    return bool(letters) and len(_DEVANAGARI.findall(text)) > len(letters) / 2


def edge_voice_and_text(text: str, voice: str | None = None) -> tuple[str, str]:
    """Pick the Edge voice and the exact text it will read for the configured style."""
    if voice:
        return voice, text
    if VOICE_STYLE == "hindi":
        from llm.hindi_script import to_devanagari  # noqa: PLC0415 (lazy: keeps client importable alone)
        return EDGE_VOICE_HI, to_devanagari(text)
    if VOICE_STYLE == "expressive":
        return ("hi-IN-SwaraNeural" if _mostly_devanagari(text) else "en-IN-NeerjaExpressiveNeural"), text
    return (EDGE_VOICE_HI if _mostly_devanagari(text) else EDGE_VOICE_EN), text


# Expressive delivery (2026-11-02, "should feel like a person"): the humanizer tags every sentence with an
# emotion; each one shifts rate and pitch a little around the base, so a reassurance sounds softer and an
# offer sounds brighter instead of every line having the same flat contour. Offsets are on top of
# EDGE_RATE / EDGE_PITCH and kept small so the voice stays the same person.
EMOTION_PROSODY: dict[str, tuple[int, int]] = {  # emotion -> (rate % delta, pitch Hz delta)
    "neutral": (0, 0), "warm": (0, 3), "enthusiastic": (5, 10), "concerned": (-5, -6), "thinking": (-4, 0),
}
EMOTIONS = frozenset(EMOTION_PROSODY)


def _prosody(emotion: str | None) -> tuple[str, str]:
    dr, dp = EMOTION_PROSODY.get((emotion or "neutral").lower(), (0, 0))
    base_r = int(re.sub(r"[^-\d]", "", EDGE_RATE) or 0)
    base_p = int(re.sub(r"[^-\d]", "", EDGE_PITCH) or 0)
    r, p = base_r + dr, base_p + dp
    return (f"{r:+d}%", f"{p:+d}Hz")


def _edge_stream(text: str, voice: str | None = None, emotion: str = "neutral", words: list | None = None) -> Iterator[bytes]:
    """MP3 chunks as Edge produces them (first one ~1 s in, the rest over the next 0.5-3 s).
    `words`, when given, receives (start_s, duration_s, spoken_word) from Edge's WordBoundary events: the
    card turns them into lip shapes that follow the actual words (see server/app.py viseme_cues)."""
    try:
        import asyncio

        import edge_tts
    except ImportError as e:
        raise TTSUnavailable("edge-tts not installed (pip install edge-tts)") from e
    v, spoken = edge_voice_and_text(text, voice)
    rate, pitch = _prosody(emotion)
    loop = asyncio.new_event_loop()
    try:
        try:
            com = edge_tts.Communicate(spoken[:2000], v, rate=rate, pitch=pitch, boundary="WordBoundary")
        except TypeError:  # older edge-tts: boundaries are on by default
            com = edge_tts.Communicate(spoken[:2000], v, rate=rate, pitch=pitch)
        agen = com.stream().__aiter__()
        total = 0
        deadline = time.monotonic() + 30
        while True:
            try:
                chunk = loop.run_until_complete(asyncio.wait_for(agen.__anext__(), timeout=max(1.0, deadline - time.monotonic())))
            except StopAsyncIteration:
                break
            except Exception as e:  # noqa: BLE001
                raise TTSUnavailable(f"edge {type(e).__name__}: {e}") from e
            if chunk["type"] == "audio" and chunk["data"]:
                total += len(chunk["data"])
                yield chunk["data"]
            elif chunk["type"] == "WordBoundary" and words is not None:
                words.append((chunk["offset"] / 1e7, chunk["duration"] / 1e7, chunk.get("text", "")))
        if total < 400:
            raise TTSUnavailable("edge: empty audio")
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:  # noqa: BLE001
            pass
        loop.close()


def _synthesize_edge(text: str, voice: str | None = None, emotion: str = "neutral") -> bytes:
    return b"".join(_edge_stream(text, voice, emotion))  # MP3; server/app.py sniffs the content type


# --------------------------------------------------------------------------- speech in
# Groq Whisper (large-v3-turbo): 400-600 ms for a sentence, and with a Roman-script Hinglish prompt it returns
# "AC thanda nahi kar raha" instead of Devanagari (measured 2026-11-01). Chrome's built-in recogniser drops most
# Hinglish and Safari/Firefox have none, so the card records each utterance and posts it to /stt; the browser
# recogniser is only the fallback.
STT_MODEL = os.getenv("HUMAN_BOT_STT_MODEL", "whisper-large-v3-turbo")
STT_PROMPT = os.getenv("HUMAN_BOT_STT_PROMPT",
                       "Hinglish, Roman script. Haan ji, meri gaadi ki service karwani hai, brake se awaaz aa rahi hai, "
                       "AC thanda nahi kar raha, pickup kab hoga, kitna lagega.")
# Whisper invents these on near-silent clips; a real customer never says only this
_STT_NOISE = re.compile(r"^\W*(thank you|thanks for watching|thank you for watching|subscribe.*|bye|धन्यवाद|शुक्रिया)\W*$", re.I)


class STTUnavailable(RuntimeError):
    pass


def transcribe(audio: bytes, mime: str = "audio/webm") -> str:
    """Audio clip (webm/opus, mp4 or wav) -> Roman-script Hinglish text. Raises STTUnavailable when Groq is out."""
    if not GROQ_KEY:
        raise STTUnavailable("no groq key")
    boundary = uuid.uuid4().hex
    ext = "webm" if "webm" in mime else "mp4" if "mp4" in mime else "wav"
    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())

    field("model", STT_MODEL)
    field("prompt", STT_PROMPT)
    field("response_format", "json")
    field("temperature", "0")
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"clip.{ext}\"\r\n"
                  f"Content-Type: {mime}\r\n\r\n").encode() + audio + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/audio/transcriptions", data=b"".join(parts), method="POST",
        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": f"multipart/form-data; boundary={boundary}",
                 "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode("utf-8", "ignore")
        raise STTUnavailable(f"groq HTTP {e.code}: {detail}") from e
    except Exception as e:  # noqa: BLE001
        raise STTUnavailable(f"groq {type(e).__name__}: {e}") from e
    text = (data.get("text") or "").strip()
    return "" if _STT_NOISE.match(text) else text


def stt_status() -> str:
    return f"groq:{STT_MODEL}" if GROQ_KEY else "browser only (no groq key)"


# Provider chain, tried in order: "gemini,edge" keeps Gemini Kore first and drops to the neural Indian
# female voice when the free-tier quota is gone; the browser voice is the last resort in the page.
# Edge only by default: Gemini Kore sounds good but the free tier allows ~10 clips a day, so a "gemini,edge" chain
# changed the voice mid-conversation whenever the quota ran out. Set HUMAN_BOT_TTS_PROVIDER=gemini,edge on a paid key.
_DEFAULT_CHAIN = "edge"
TTS_CHAIN = [p.strip() for p in (os.getenv("HUMAN_BOT_TTS_PROVIDER") or _DEFAULT_CHAIN).lower().split(",") if p.strip()]
TTS_PROVIDER = TTS_CHAIN[0]
_TTS_LAST_USED: dict[str, str] = {}


def synthesize_stream(text: str, voice: str | None = None, fmt: str = "wav", emotion: str = "neutral",
                      words: list | None = None) -> Iterator[bytes]:
    """Audio chunks for `text`. Edge streams as it synthesizes; the other providers yield one whole clip.

    A provider that fails before producing any audio is skipped; a failure mid-stream propagates, because
    bytes already went out and a different voice must not finish the sentence.
    """
    errors = []
    for p in TTS_CHAIN:
        if _tts_is_dead(p):
            errors.append(f"{p}: skipped ({_TTS_DEAD[p][1]})")
            continue
        try:
            if p == "gemini":
                gen: Iterator[bytes] = iter([_synthesize_gemini(text)])
            elif p == "groq":
                gen = iter([_synthesize_groq(text, voice, fmt)])
            elif p == "edge":
                gen = _edge_stream(text, voice if voice and "Neural" in voice else None, emotion, words)
            else:
                raise TTSUnavailable(f"unknown TTS provider '{p}'")
            first = next(gen)
        except (TTSUnavailable, StopIteration) as e:
            errors.append(f"{p}: {e or 'empty'}")
            if "empty" not in str(e):  # one odd text with no audio is not an outage
                _tts_mark_dead(p, str(e) or "empty audio")
            continue
        _TTS_DEAD.pop(p, None)
        _TTS_LAST_USED["provider"] = p
        yield first
        yield from gen
        return
    raise TTSUnavailable(" | ".join(errors)[:400])


# TTS circuit breaker: when Edge is blocked every sentence used to wait the full 30 s deadline before
# the card fell back to the browser voice. A failed provider is skipped for 60 s instead, so the
# fallback is immediate; the next attempt after that re-opens it when the service is back.
_TTS_DEAD: dict[str, tuple[float, str]] = {}
_TTS_DEAD_S = 60.0


def _tts_is_dead(p: str) -> bool:
    rec = _TTS_DEAD.get(p)
    return bool(rec) and time.time() - rec[0] < _TTS_DEAD_S


def _tts_mark_dead(p: str, why: str) -> None:
    if p not in _TTS_DEAD:
        print(f"[tts] '{p}' skipped for {int(_TTS_DEAD_S)} s: {why[:120]}", file=sys.stderr, flush=True)
    _TTS_DEAD[p] = (time.time(), why[:160])


def synthesize(text: str, voice: str | None = None, fmt: str = "wav", emotion: str = "neutral") -> bytes:
    return b"".join(synthesize_stream(text, voice, fmt, emotion))


def tts_status() -> str:
    def one(p: str) -> str:
        if p == "gemini":
            return f"gemini:{GEMINI_TTS_MODEL}/{GEMINI_TTS_VOICE}" if GEMINI_KEY else "gemini(no key)"
        if p == "groq":
            return f"groq:{TTS_MODEL}/{TTS_VOICE}" if GROQ_KEY else "groq(no key)"
        if p == "edge":
            return f"edge:{EDGE_VOICE_EN}|{EDGE_VOICE_HI}"
        return f"unknown({p})"
    used = _TTS_LAST_USED.get("provider")
    return " -> ".join(one(p) for p in TTS_CHAIN) + (f"  [last used: {used}]" if used else "")  # ASCII: cp1252 consoles
