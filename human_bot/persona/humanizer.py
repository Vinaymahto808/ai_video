"""Post-processing that turns an LLM reply into something a human would actually say.

Everything here is deterministic and cheap (no model call), so it runs in the 20 ms
between token stream end and TTS start. This is where most "robotic" tells die:
markdown, written numbers, 90-word monologues, stacked questions.
"""

from __future__ import annotations

import re

_MARKUP = [
    (re.compile(r"```[\s\S]*?```"), ""),
    (re.compile(r"^\s*#{1,6}\s*", re.M), ""),
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"\*(.+?)\*"), r"\1"),
    (re.compile(r"^\s*[-*•]\s+", re.M), ""),
    (re.compile(r"^\s*\d+[.)]\s+", re.M), ""),
    (re.compile(r"\[(.*?)\]\((.*?)\)"), r"\1"),
    (re.compile(r"[`~>|]"), ""),
    (re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]"), ""),
]

_BANNED_OPENERS = re.compile(
    r"^(certainly|sure thing|of course|absolutely|great question|that'?s a great question|"
    r"as an ai( language model)?|i'?d be happy to|i am happy to help)[!,.\s]*",
    re.I,
)

# 16,46,000 / 1,23,45,678 -> Indian spoken form
_INDIAN_GROUP = re.compile(r"\b(\d{1,2}(?:,\d{2})*,\d{3})\b")
# boundary before "rs" so "filters, brakes" is not money; the amount must start with a digit; the
# optional unit owns its leading space so a following word keeps its own ("rupees lagega")
_CURRENCY = re.compile(r"(?<![A-Za-z])(?:₹|rs\.?|inr)\s*(\d[\d,]*(?:\.\d+)?)(?:\s*(lakh|lac|cr|crore|k|thousand)\b)?", re.I)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")

# Hindi verbs agree with the speaker. Neha is female, but LLMs drift into masculine first person
# ("kar deta hoon", "bataunga", "samajh gaya"). Only first-person-only shapes are rewritten, so a third-person
# "brake pad ghis gaya tha" is left alone.
_M_TA_HOON = re.compile(r"\b(\w+?)ta\s+(hoon|hun|hu)\b", re.I)          # karta hoon -> karti hoon
_M_RAHA_HOON = re.compile(r"\braha\s+(hoon|hun|hu)\b", re.I)             # raha hoon  -> rahi hoon
_M_UNGA = re.compile(r"\b(\w+?)(u|oo)nga\b", re.I)                        # bataunga   -> bataungi
_M_SAMAJH = re.compile(r"\b(samajh|samjh)\s+gaya\b", re.I)               # samajh gaya -> samajh gayi
_M_MAIN_GAYA = re.compile(r"\b(main|mai|mein)\s+(\w+\s+)?gaya\b", re.I)  # main bhool gaya -> gayi

# Spoken Delhi NCR Hinglish uses the English word where textbook Hindi has a Sanskrit one. Nobody in
# Gurgaon says "dhanyavaad" to a customer on a call; they say "thank you".
_DELHI = [
    (re.compile(r"\bdhanyava+d\b", re.I), "thank you"),
    (re.compile(r"\bkripya\b", re.I), "please"),
    (re.compile(r"\bavashya\b", re.I), "zaroor"),
    (re.compile(r"\bsahayata\b", re.I), "help"),
    (re.compile(r"\bsamasya\b", re.I), "problem"),
    (re.compile(r"\bsampark\b", re.I), "contact"),
    (re.compile(r"\bkshama\s+(kijiye|karein|karen|kare)\b", re.I), "sorry"),
    (re.compile(r"\bkshama\b", re.I), "maaf"),
    (re.compile(r"\bprateeksha\b", re.I), "wait"),
    (re.compile(r"\bupalabdh\b", re.I), "available"),
    (re.compile(r"\bnishulk\b", re.I), "free"),
    (re.compile(r"\bshulk\b", re.I), "charge"),
    (re.compile(r"\bmulya\b", re.I), "price"),
    (re.compile(r"\bvaa?han\s+(ki|ka|ke|mein|me)\b", re.I), r"gaadi \1"),
    (re.compile(r"\batirikt\b", re.I), "extra"),
    (re.compile(r"\bsuchna\b", re.I), "information"),
    (re.compile(r"\bnamaskar\b", re.I), "namaste"),
]


def _keep_case(src: str, out: str) -> str:
    return out[:1].upper() + out[1:] if src[:1].isupper() else out


def fix_gender(text: str, female: bool = True) -> str:
    """Rewrite masculine first-person verb endings to feminine (no-op for a male persona)."""
    if not female:
        return text
    t = _M_TA_HOON.sub(lambda m: f"{m.group(1)}ti {m.group(2)}", text)
    t = _M_RAHA_HOON.sub(lambda m: f"rahi {m.group(1)}", t)
    t = _M_UNGA.sub(lambda m: f"{m.group(1)}{m.group(2)}ngi", t)
    t = _M_SAMAJH.sub(lambda m: f"{m.group(1)} gayi", t)
    t = _M_MAIN_GAYA.sub(lambda m: f"{m.group(1)} {m.group(2) or ''}gayi", t)
    return t


def delhi_register(text: str) -> str:
    """Textbook Hindi -> the English loanwords Delhi NCR actually speaks."""
    t = text
    for pat, out in _DELHI:
        t = pat.sub(lambda m, out=out: _keep_case(m.group(0), m.expand(out) if "\\" in out else out), t)
    return t


def strip_markup(text: str) -> str:
    out = text
    for pat, rep in _MARKUP:
        out = pat.sub(rep, out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{2,}", "\n", out)
    return out.strip()


def _indian_words(n: int) -> str:
    """12345678 -> '1 crore 23 lakh 45 thousand 678'."""
    if n == 0:
        return "zero"
    units = [(10_000_000, "crore"), (100_000, "lakh"), (1_000, "thousand")]
    parts: list[str] = []
    rest = n
    for size, name in units:
        if rest >= size:
            q, rest = divmod(rest, size)
            parts.append(f"{q} {name}")
    if rest:
        parts.append(str(rest))
    return " ".join(parts)


def spoken_numbers(text: str) -> str:
    def _curr(m: re.Match) -> str:
        num, unit = m.group(1), (m.group(2) or "").lower()
        num = num.replace(",", "")
        if unit in ("k", "thousand"):
            return f"{num} thousand rupees"
        if unit in ("lakh", "lac"):
            return f"{num} lakh rupees"
        if unit in ("cr", "crore"):
            return f"{num} crore rupees"
        try:
            return f"{_indian_words(int(float(num)))} rupees"
        except ValueError:
            return f"{num} rupees"

    text = _CURRENCY.sub(_curr, text)

    def _grp(m: re.Match) -> str:
        try:
            return _indian_words(int(m.group(1).replace(",", "")))
        except ValueError:
            return m.group(1)

    text = _INDIAN_GROUP.sub(_grp, text)
    text = _PERCENT.sub(r"\1 percent", text)
    text = re.sub(r"\bkmpl\b", "km per litre", text, flags=re.I)
    text = re.sub(r"\bkm/h\b", "km per hour", text, flags=re.I)
    return text


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?।])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


# "…hota hai aur washing…": the joiner starts the next clause, so the head can end before it.
_CLAUSE_JOINERS = ("aur", "to", "lekin", "magar", "kyunki", "isliye", "phir", "jab", "agar", "ya")


def split_first_phrase(text: str, min_words: int = 3, max_words: int = 10) -> tuple[str, str] | None:
    """First speakable clause of the first sentence, for TTS to start before the reply is finished.

    Returns (head, rest) once the buffer holds a natural break: the last comma/semicolon that still
    leaves >= min_words in the head, else the first conjunction inside the window. Returns None while
    there is nothing safe to say yet (too short, no break, or the first sentence is already complete
    and the normal sentence flush will handle it). Never cuts mid-phrase just to hit max_words: a
    wrong pause is worse than a later first clip.
    """
    if not text or len(split_sentences(text)) > 1:
        return None
    # last punctuation break inside the window
    cut = None
    for m in re.finditer(r"[,;:]\s+", text):
        head = text[: m.end()].strip()
        if min_words <= len(head.split()) <= max_words:
            cut = m.end()
    if cut:
        head, rest = text[:cut].strip(), text[cut:].strip()
        return (head, rest) if rest else None
    # no punctuation: a joiner may start the rest ("… hota hai aur washing …")
    cut = None
    for m in re.finditer(r"\b(" + "|".join(_CLAUSE_JOINERS) + r")\b", text, re.I):
        head = text[: m.start()].strip()
        if min_words <= len(head.split()) <= max_words:
            cut = m.start()
    if cut is not None:
        head, rest = text[:cut].strip(), text[cut:].strip()
        if head and rest:
            return head, rest
    return None


def enforce_budget(text: str, max_words: int = 40) -> str:
    """Trim to the word budget without cutting mid-sentence and without losing the ask."""
    sents = split_sentences(text)
    if not sents:
        return text
    kept: list[str] = []
    words = 0
    for s in sents:
        n = len(s.split())
        if kept and words + n > max_words:
            break
        kept.append(s)
        words += n
    if len(kept) < len(sents) and not any("?" in s for s in kept):
        q = next((s for s in sents[len(kept):] if "?" in s), None)
        if q:
            kept.append(q)
    return " ".join(kept)


_DETAIL = re.compile(
    r"\b(in detail|detail mein|detail me|details?|explain|elaborate|step by step|poora batao|pura batao|poori baat|"
    r"sab kuch batao|samjhao|samjha do|samjha dijiye|kaise hota hai|kaise karte ho|process kya hai|vistaar)\b",
    re.I,
)


def wants_detail(user_text: str) -> bool:
    """True when the customer asked for an explanation, so the turn may use the larger word budget
    (persona.conversation.max_words_detail) instead of the normal cap."""
    return bool(user_text) and bool(_DETAIL.search(user_text))


def humanize(reply: str, max_words: int = 40, language: str = "hinglish", female: bool = True) -> str:
    t = strip_markup(reply)
    t = _BANNED_OPENERS.sub("", t).strip()
    t = spoken_numbers(t)
    if language in ("hinglish", "hindi"):
        t = fix_gender(t, female)
    if language == "hinglish":
        t = delhi_register(t)
    t = re.sub(r"\s+([,.!?])", r"\1", t)
    t = enforce_budget(t, max_words)
    # one question max: keep the first, demote the rest
    if t.count("?") > 1:
        first = t.index("?")
        t = t[: first + 1] + t[first + 1 :].replace("?", ".")
    if language == "hinglish":
        t = re.sub(r"\bOkay\b", "Theek hai", t)
        t = re.sub(r"\bYes\b", "Haan", t)
    return t.strip()


_CONCERN_CUES = ("problem", "issue", "expensive", "mehnga", "mehenga", "nahi", "dikkat", "kharab", "awaaz",
                 "noise", "leak", "garam", "overheat", "band ho", "accident", "complaint", "shikayat", "pareshan")
_FEMALE_BACKCHANNELS = ["haan ji", "hmm", "right", "samajh gayi", "acha", "okay"]


def pick_backchannel(user_text: str, index: int = 0, options: list[str] | None = None) -> str | None:
    """Short acknowledgment to play while the customer is still talking.

    `options` comes from persona.conversation.backchannels so the words match the speaker (female Delhi
    NCR by default: "samajh gayi", never "samajh gaya")."""
    t = (user_text or "").lower()
    if len(t.split()) < 4:
        return None
    if any(w in t for w in _CONCERN_CUES):
        return "hmm"
    opts = [o for o in (options or _FEMALE_BACKCHANNELS) if o]
    return opts[index % len(opts)] if opts else None


def pick_gesture(gestures: dict, emotion: str, index: int = 0) -> str:
    """Rotate through the persona's gestures for an emotion so consecutive sentences don't repeat one move."""
    opts = gestures.get(emotion) or gestures.get("neutral") or ["idle"]
    return opts[index % len(opts)]


_E_REASSURE = ("koi dikkat nahi", "koi baat nahi", "tension mat", "no problem", "ho jayega", "fikar mat", "don't worry")
_E_THINK = ("ek second", "ek minute", "check karti", "dekhti hoon", "confirm karke", "let me check")
_E_CONCERN = ("sorry", "problem", "issue", "delay", "nahi ho paya", "dikkat", "kharab", "awaaz", "noise", "leak",
              "overheat", "garam", "accident", "complaint", "shikayat", "arre", "oh no", "maaf", "afsos", "band ho",
              "start nahi", "nahi chal", "atak", "jhatka", "smoke", "dhuan", "warning light")
_E_ENTHUS = ("congrat", "great", "badhiya", "perfect", "best", "zaroor", "bilkul", "done", "book kar d", "mubarak",
             "wah", "mast", "shandaar", "excellent", "sahi")
_E_WARM = ("welcome", "namaste", "dhanyavaad", "thank", "shukriya", "hello", "hi ", "haan ji", "ji haan", "acha",
           "achha", "accha", "theek hai", "good", "nice")


_E_WH = ("kaise", "kya", "kaun", "kab", "kitna", "kitni", "kahan", "kyun", "kaunsi", "how", "why", "which", "when", "where")


_PREFIX_CUES = {"book kar d"}   # stems that continue ("book kar deti / dungi"); everything else is a whole word


def _cue_re(cues: tuple[str, ...]) -> re.Pattern:
    """Whole-word cues, so "namaste" never hits "mast" and "achanak" never hits "acha"."""
    parts = [re.escape(c.strip()) + ("" if c in _PREFIX_CUES else r"\b") for c in cues]
    return re.compile(r"\b(?:" + "|".join(parts) + r")", re.I)


_RE_REASSURE, _RE_THINK, _RE_CONCERN = _cue_re(_E_REASSURE), _cue_re(_E_THINK), _cue_re(_E_CONCERN)
_RE_ENTHUS, _RE_WARM, _RE_WH = _cue_re(_E_ENTHUS), _cue_re(_E_WARM), _cue_re(_E_WH)


def emotion_of(text: str) -> str:
    """Emotion tag per sentence -> persona.avatar.gestures. Hinglish and English cues, reassurance before concern
    so "koi dikkat nahi" reads as warm, not worried."""
    t = text.lower()
    if _RE_REASSURE.search(t):
        return "warm"
    if _RE_THINK.search(t):
        return "thinking"
    if "?" in t and _RE_WH.search(t):
        return "thinking"
    if _RE_CONCERN.search(t):
        return "concerned"
    if _RE_ENTHUS.search(t):
        return "enthusiastic"
    if _RE_WARM.search(t):
        return "warm"
    if "?" in t:
        return "warm"
    return "neutral"


def delivery_chunks(text: str, max_words: int = 14) -> list[str]:
    """Chunks of ~14 words at clause boundaries -> TTS starts sooner, sounds less read."""
    chunks: list[str] = []
    for s in split_sentences(text):
        if len(s.split()) <= max_words:
            chunks.append(s)
            continue
        parts = re.split(r"(?<=[,;:])\s+", s)
        buf = ""
        for p in parts:
            if buf and len((buf + " " + p).split()) > max_words:
                chunks.append(buf.strip())
                buf = p
            else:
                buf = f"{buf} {p}".strip()
        if buf:
            chunks.append(buf)
    return [c for c in chunks if c]
