"""AIVA-shaped calling loop, stdlib only (no FastAPI/websockets install needed).

    python human_bot/server/app.py --port 8010

Endpoints
  GET  /health                     -> {"ok":true,"provider":"...","persona":"..."}
  POST /session                    -> {session_id, greeting, chunks[]}
  POST /chat      (SSE)            -> events: backchannel, sentence, final
  POST /analyze                    -> AIVA post-call JSON contract
  GET  /                           -> browser demo (mic, streaming, TTS, avatar panel)

Why SSE and not WebSocket: the transport is the only stdlib-free part of a real
build. Swapping this for LiveKit/Agora is a transport change, not a brain change;
everything below (session context, barge-in, sentence-level TTS, emotion tags,
post-call analysis) stays identical.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from llm.client import (  # noqa: E402
    OfflineBrain,
    offline_brain,
    STTUnavailable,
    TTSUnavailable,
    EDGE_VOICE_EN,
    EDGE_VOICE_HI,
    EMOTIONS,
    stt_status,
    transcribe,
    provider_name,
    stream_reply,
    synthesize,
    synthesize_stream,
    tts_status,
)
from llm.knowledge import KB_TIMEOUT_S, knowledge_block, retrieve  # noqa: E402
from memory.store import ConversationStore  # noqa: E402
from crm.outbox import Outbox  # noqa: E402
from crm.bitrix24 import sink_from_env as bitrix_sink_from_env  # noqa: E402
from crm import acr_leads  # noqa: E402
from crm.service_request import asked_confirmation, collect as sr_collect, is_no as sr_is_no, is_yes as sr_is_yes, missing as sr_missing, prompt_block as sr_prompt_block, summary_line as sr_summary, wants_request as sr_wants  # noqa: E402
from llm.knowledge import status as kb_status  # noqa: E402
from llm import did_avatar, simli_avatar  # noqa: E402


def avatar_status() -> dict:
    return simli_avatar.status() if simli_avatar.enabled() else did_avatar.status()
from persona.humanizer import (  # noqa: E402
    delivery_chunks,
    emotion_of,
    humanize,
    pick_backchannel,
    pick_gesture,
    split_sentences,
    wants_detail,
)
from persona.prompting import build_system_prompt, load_persona  # noqa: E402
from postcall.analyze import analyze  # noqa: E402

PERSONA_DIR = HERE.parent / "persona"
WEB = HERE.parent / "web"
WEB_ROOT = WEB.resolve()                      # static files are confined to this directory
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
                ".css": "text/css", ".jpg": "image/jpeg", ".png": "image/png", ".svg": "image/svg+xml",
                ".wav": "audio/wav", ".mp3": "audio/mpeg", ".mp4": "video/mp4", ".json": "application/json; charset=utf-8",
                ".ico": "image/x-icon", ".webp": "image/webp"}
# The only voices a client may request: female Indian (project rule). Anything else is ignored.
ALLOWED_VOICES = {EDGE_VOICE_EN, EDGE_VOICE_HI, "en-IN-NeerjaNeural", "en-IN-NeerjaExpressiveNeural", "hi-IN-SwaraNeural"}


class BodyTooLarge(Exception):
    pass


def puppet_version() -> str:
    """Changes whenever the portrait or any puppet layer is rebuilt (newest mtime across the asset folders)."""
    base = HERE.parents[1] / "tran_moment"
    newest = 0
    for d in (base / "prepared", base / "path_b" / "mouths", base / "path_b" / "expressions", base / "path_b" / "reactions"):
        if d.is_dir():
            for f in d.iterdir():
                if f.is_file():
                    newest = max(newest, f.stat().st_mtime_ns)
    return "%x" % (newest // 1_000_000)


from llm.client import OfflineServiceBrain as _OSB  # noqa: E402
KNOWN_CARS = frozenset(w for n in _OSB._MAKES + _OSB._MODELS for w in n.split()) | frozenset(
    ("glanza", "rumion", "taisor", "hyryder", "innova", "crysta", "fortuner", "hilux", "camry", "vellfire", "urban", "cruiser",
     "creta", "venue", "verna", "exter", "alcazar", "tucson", "ioniq", "aura", "grand", "i10", "i20", "nios"))
MAX_TURNS_KEPT = 16


def load_personas() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in sorted(PERSONA_DIR.glob("persona_*.json")):
        p = load_persona(f)
        out[p.get("id") or f.stem.replace("persona_", "")] = p
    return out


# every persona_*.json in persona/ is servable; --persona only picks the default
PERSONAS = load_personas()
PERSONA = next(iter(PERSONAS.values()))


def is_female(persona: dict) -> bool:
    return (persona.get("gender") or "female").lower().startswith("f")


class Session:
    def __init__(self, persona: dict) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.persona = persona
        self.transcript: list[dict] = []
        self.context: dict = {}
        self.brain: OfflineBrain = offline_brain(persona)  # service-advisor script for workshops, sales script for showrooms
        self.generation = 0
        self.lock = threading.Lock()
        self.language = persona["languages"]["primary"]
        self.started = time.time()
        self.last_seen = self.started
        # conversation memory (memory/store.py): who this is, what was said before, what is being recorded
        self.visitor_id = ""
        self.recall_block = ""
        self.recalled: list[dict] = []
        self.consent = False
        self.audio_dir: Path | None = None
        self.audio_files: list[str] = []
        self.pending_audio: str | None = None
        self.analysis: dict | None = None
        self.save_lock = threading.Lock()
        # service request (crm/service_request.py): has Neha asked "bhej doon?", and what was filed
        self.sr_pending = False
        # knowledge prefetch: /stt starts retrieval for the transcript it just produced, so /chat for the same
        # text finds the passages ready instead of spending 30-300 ms on the kb call in the critical path
        self.kb_prefetch: dict | None = None   # {"text", "done": Event, "result": (block, hits)}
        self.service_request: dict | None = None

    def messages(self, extra: str = "") -> list[dict]:
        ctx = json.dumps(self.context, ensure_ascii=False) if self.context else "{}"
        if self.recall_block:
            extra = self.recall_block + ("\n\n" + extra if extra else "")
        system = build_system_prompt(self.persona, self.language, extra=extra).replace("{SESSION_CONTEXT}", ctx)
        history = [{"role": t["role"], "content": t["text"]} for t in self.transcript[-MAX_TURNS_KEPT:]]
        return [{"role": "system", "content": system}] + history

    def knowledge(self, query: str) -> tuple[str, list[dict]]:
        """Retrieve website facts for this turn (persona.knowledge decides if/where). Fail-soft.
        Uses the /stt prefetch when it was started for exactly this text."""
        kcfg = self.persona.get("knowledge") or {}
        wid = kcfg.get("website_id")
        if not wid:
            return "", []
        pre = self.kb_prefetch
        if pre and pre["text"] == query.strip().lower():
            pre["done"].wait(KB_TIMEOUT_S)
            self.kb_prefetch = None
            if pre.get("result") is not None:
                return pre["result"]
        hits = retrieve(wid, query, k=int(kcfg.get("top_k", 5)), min_score=kcfg.get("min_score"))
        return knowledge_block(hits, self.persona.get("company", "the company")), hits

    def prefetch_knowledge(self, text: str) -> None:
        """Start retrieval for `text` in the background (called by /stt as soon as the transcript exists)."""
        if not (self.persona.get("knowledge") or {}).get("website_id") or not text.strip():
            return
        job = {"text": text.strip().lower(), "done": threading.Event(), "result": None}
        self.kb_prefetch = job

        def run() -> None:
            try:
                kcfg = self.persona.get("knowledge") or {}
                hits = retrieve(kcfg["website_id"], text, k=int(kcfg.get("top_k", 5)), min_score=kcfg.get("min_score"))
                job["result"] = (knowledge_block(hits, self.persona.get("company", "the company")), hits)
            except Exception:  # noqa: BLE001  (fail-soft: /chat retrieves itself)
                job["result"] = None
            finally:
                job["done"].set()

        threading.Thread(target=run, daemon=True, name="kb-prefetch").start()


SESSIONS: dict[str, Session] = {}
SESSIONS_LOCK = threading.Lock()

TTS_CACHE: dict[str, bytes] = {}
TTS_LOCK = threading.Lock()
TTS_LAST: dict[str, str] = {}
TTS_INFLIGHT: dict[str, "TtsJob"] = {}   # key -> synthesis in progress (chunks appear as Edge sends them)
TTS_CACHE_MAX = 400


def tts_key(text: str, voice: str | None = None, emotion: str = "neutral") -> str:
    return f"{voice or 'default'}|{emotion or 'neutral'}|{text}"


TTS_CUES: dict[str, list[dict]] = {}   # tts_key -> [{"s": start_s, "e": end_s, "v": Rhubarb letter}] for the card's lips
# letter -> Rhubarb mouth letter (A closed, B teeth, C open, D wide, F pucker, G f/v, H l, X rest); same map as
# tran_moment/synth_talk_clips.py so the live card and the rendered clips move their lips the same way
_LETTER_VISEME = {"a": "C", "e": "B", "i": "D", "o": "F", "u": "G", "m": "A", "b": "A", "p": "A", "f": "G", "v": "G",
                  "l": "H", "w": "F", "y": "D", "s": "B", "z": "B", "t": "B", "d": "B", "n": "B", "r": "B", "k": "C",
                  "g": "C", "h": "C", "j": "B", "c": "B"}


def viseme_cues(text: str, words: list[tuple[float, float, str]]) -> list[dict]:
    """Lip-shape timeline for a clip from Edge's word boundaries: the letters of each (Roman) word are spread over
    that word's time, rests (X) fill the pauses, micro-cues and repeats merge so the mouth does not flicker.
    Returns [] when the provider gave no word timing (the card then falls back to the loudness analyser)."""
    if not words:
        return []
    roman = re.findall(r"[A-Za-z]+", text)
    cues: list[dict] = []
    t_prev = 0.0
    for i, (start, dur, _spoken) in enumerate(words):
        if start - t_prev > 0.12:
            cues.append({"s": round(t_prev, 3), "e": round(start, 3), "v": "X"})
        letters = [c for c in (roman[i] if i < len(roman) else "a").lower() if c.isalpha()] or ["a"]
        keep: list[str] = []
        for c in letters:                               # consonant clusters collapse to their first consonant
            if c in "aeiou" or not keep or keep[-1] in "aeiou":
                keep.append(c)
        step = max(dur, 0.08) / len(keep)
        for k, c in enumerate(keep):
            cues.append({"s": round(start + k * step, 3), "e": round(start + (k + 1) * step, 3), "v": _LETTER_VISEME.get(c, "B")})
        t_prev = start + max(dur, 0.08)
    cues.append({"s": round(t_prev, 3), "e": round(t_prev + 0.3, 3), "v": "X"})
    merged: list[dict] = []
    for c in cues:
        if merged and ((c["e"] - c["s"]) < 0.09 or merged[-1]["v"] == c["v"]):
            merged[-1]["e"] = c["e"]
        else:
            merged.append(dict(c))
    return merged


class TtsJob:
    """One synthesis. Readers can stream the chunks while the producer thread is still receiving them,
    so /tts starts sending audio ~1 s after the request instead of after the whole clip (1.5-4.6 s)."""

    def __init__(self, text: str, voice: str | None, emotion: str = "neutral") -> None:
        self.text, self.voice, self.emotion = text, voice, emotion
        self.chunks: list[bytes] = []
        self.words: list[tuple[float, float, str]] = []   # Edge WordBoundary events (start_s, dur_s, word)
        self.done = False
        self.error: str | None = None
        self.cv = threading.Condition()

    def run(self) -> None:
        key = tts_key(self.text, self.voice, self.emotion)
        try:
            for chunk in synthesize_stream(self.text, self.voice, emotion=self.emotion, words=self.words):
                with self.cv:
                    self.chunks.append(chunk)
                    self.cv.notify_all()
            audio = b"".join(self.chunks)
            with TTS_LOCK:
                while len(TTS_CACHE) >= TTS_CACHE_MAX:
                    TTS_CACHE.pop(next(iter(TTS_CACHE)))  # oldest first
                TTS_CACHE[key] = audio
                while len(TTS_CUES) >= TTS_CACHE_MAX:
                    TTS_CUES.pop(next(iter(TTS_CUES)))
                TTS_CUES[key] = viseme_cues(self.text, self.words)
            TTS_LAST.pop("error", None)
        except Exception as ex:  # noqa: BLE001
            self.error = str(ex)[:220]
            TTS_LAST["error"] = self.error
        finally:
            with TTS_LOCK:
                if TTS_INFLIGHT.get(key) is self:
                    TTS_INFLIGHT.pop(key, None)
            with self.cv:
                self.done = True
                self.cv.notify_all()

    def stream(self, timeout_s: float = 30.0) -> Iterator[bytes]:
        """Yield chunks in order, waiting for new ones; raises TTSUnavailable on failure or timeout."""
        i = 0
        deadline = time.monotonic() + timeout_s
        while True:
            with self.cv:
                while i >= len(self.chunks) and not self.done:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise TTSUnavailable("tts timeout")
                    self.cv.wait(left)
                if i < len(self.chunks):
                    chunk = self.chunks[i]
                    i += 1
                elif self.error:
                    raise TTSUnavailable(self.error)
                else:
                    return
            yield chunk


def tts_job(text: str, voice: str | None = None, emotion: str = "neutral") -> TtsJob:
    """The job for this text: finished (from cache), already running (joined), or started now.

    The server starts a job the moment a sentence is produced (see prefetch_tts), so the card's later
    /tts request usually finds the clip ready or half done. Two callers never synthesize the same text twice.
    """
    key = tts_key(text, voice, emotion)
    with TTS_LOCK:
        hit = TTS_CACHE.get(key)
        if hit:
            job = TtsJob(text, voice, emotion)
            job.chunks, job.done = [hit], True
            return job
        job = TTS_INFLIGHT.get(key)
        if job is None:
            job = TTS_INFLIGHT[key] = TtsJob(text, voice, emotion)
            threading.Thread(target=job.run, daemon=True, name="tts-job").start()
        return job


def tts_get_or_make(text: str, voice: str | None = None, wait_s: float = 30.0, emotion: str = "neutral") -> bytes:
    """Whole clip (cache, joined in-flight job, or a fresh synthesis)."""
    remember_tts_text(text)
    try:
        return b"".join(tts_job(text, voice, emotion).stream(wait_s))
    except TTSUnavailable:
        with TTS_LOCK:
            hit = TTS_CACHE.get(tts_key(text, voice, emotion))
        if hit:
            return hit
        raise


# An opening interjection followed by a comma and more text: "Acha, ", "Haan ji, ", "Bilkul, ". Only matched once
# per turn, only when text follows (so a bare "Acha" sentence is left to the normal splitter).
_OPENER = re.compile(r"^\s*((?:haan ji|ji haan|haan|acha|achha|accha|bilkul|theek hai|dekhiye|arre|right|okay|samajh gayi)[,!])\s+\S", re.I)


def cached_opener(opener: str, persona: dict) -> str:
    """The interjection as the warm backchannel clip when one exists (exact text = cache key), else as spoken."""
    bare = opener.rstrip(",!").strip()
    for cand in (bare.lower(), bare.capitalize(), bare):
        if cached_clip(cand):
            return cand
    for bc in (persona.get("conversation") or {}).get("backchannels") or []:
        if bc.lower() == bare.lower():
            return bc
    return bare.capitalize() + "."


def prefetch_tts(texts: list[str], voice: str | None = None, emotion: str = "neutral") -> None:
    """Start synthesis now so playback does not wait. Failures are recorded; /tts will retry.
    Every text that passes through here is one the server produced, which is what /tts may speak."""
    for t in texts:
        t = (t or "").strip()
        if t:
            remember_tts_text(t)
            tts_job(t, voice, emotion)


def cached_clip(text: str, emotion: str = "neutral") -> bytes | None:
    with TTS_LOCK:
        return TTS_CACHE.get(tts_key(text, None, emotion)) or TTS_CACHE.get(tts_key(text, None, "neutral"))


def warm_tts(personas: dict) -> None:
    """At start-up: backchannels and stock lines for every persona, so the first turn never pays the
    Edge cold start (7.6 s measured) and 'haan ji' / 'acha' play the instant the customer stops talking."""
    phrases: list[str] = []
    for p in personas.values():
        phrases += list((p.get("conversation") or {}).get("backchannels") or [])
    phrases += ["Ek second, main check karti hoon.", "Haan ji, boliye.", "Samajh gayi."]
    seen: list[str] = []
    for t in phrases:
        if t and t not in seen:
            seen.append(t)
    t0 = time.perf_counter()
    failed = 0
    for t in seen:
        remember_tts_text(t)
        try:
            tts_get_or_make(t)
        except Exception as ex:  # noqa: BLE001
            failed += 1
            print(f"[tts] warm-up failed for {t!r}: {str(ex)[:120]}", file=sys.stderr)
            if failed >= 3:
                print("[tts] warm-up stopped after 3 failures (provider down?); clips synthesize on demand", file=sys.stderr)
                return
    print(f"[tts] warmed {len(seen) - failed}/{len(seen)} clips in {time.perf_counter() - t0:.1f}s", file=sys.stderr, flush=True)


def get_session(sid: str) -> Session | None:
    with SESSIONS_LOCK:
        s = SESSIONS.get(sid)
    if s:
        s.last_seen = time.time()
    return s


def new_session(context: dict | None, persona: dict) -> Session:
    s = Session(persona)
    s.context = context or {}
    s.last_seen = time.time()
    with SESSIONS_LOCK:
        SESSIONS[s.id] = s
        while len(SESSIONS) > SESSION_MAX:
            SESSIONS.pop(next(iter(SESSIONS)), None)
    return s


def clean_context(ctx) -> dict:
    """Only the page facts the prompt needs, each capped: the context comes from the host page / URL."""
    if not isinstance(ctx, dict):
        return {}
    page = ctx.get("page") if isinstance(ctx.get("page"), dict) else {}
    keep = {}
    for k in ("url", "title", "model", "lang"):
        v = page.get(k)
        if isinstance(v, str) and v.strip():
            keep[k] = v.strip()[:200]
    out = {"page": keep} if keep else {}
    for k in ("lang", "language", "source"):
        v = ctx.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:40]
    v = ctx.get("visitor")  # long-lived id the card keeps in localStorage: how a returning customer is recognised
    if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,64}", v):
        out["visitor"] = v
    return out


# ------------------------------------------------------------------ conversation memory
RECORD = os.getenv("HUMAN_BOT_RECORD", "1").lower() in ("1", "true", "yes")   # store transcripts + audio
ADMIN_KEY = os.getenv("HUMAN_BOT_ADMIN_KEY", "")                               # /conversations off loopback
DISCLOSURE = {"service": "Yeh baat-cheet quality ke liye record hoti hai.",
              "sales": "Yeh baat-cheet quality ke liye record hoti hai."}
STORE: ConversationStore | None = ConversationStore() if RECORD else None
# CRM: every ended conversation is queued in the outbox (same SQLite file) and pushed to Bitrix24 by a worker
# with retries. Without BITRIX24_WEBHOOK_URL the rows wait; they flush once it is configured.
CRM_SINK = bitrix_sink_from_env()
OUTBOX: Outbox | None = Outbox(STORE.path, CRM_SINK) if STORE else None
if STORE and not CRM_SINK:
    print("[crm] BITRIX24_WEBHOOK_URL not set: conversations queue in the outbox, nothing is pushed yet", file=sys.stderr, flush=True)


def persist(s: Session, ended: bool = False) -> None:
    """Write the structured record (upsert). Called after every turn and at the end; never raises."""
    if not STORE:
        return
    with s.save_lock:  # the per-turn save thread and /end must not interleave (duplicate clip files)
        _persist(s, ended)


def _persist(s: Session, ended: bool) -> None:
    try:
        if ended and s.analysis is None:
            try:
                s.analysis = analyze(s.transcript)
            except Exception:  # noqa: BLE001
                s.analysis = None
        if s.audio_dir and RECORD:  # keep Neha's side too: clips are in the TTS cache by text
            for i, t in enumerate(s.transcript):
                if t.get("role") != "assistant" or t.get("audio"):
                    continue
                names = []
                for k, piece in enumerate(delivery_chunks(t["text"])):
                    clip = cached_clip(piece, emotion_of(piece))
                    if not clip and ended:
                        # the recording must be complete: wait for the clip still being made (or make it)
                        try:
                            clip = tts_get_or_make(piece, emotion=emotion_of(piece), wait_s=20)
                        except Exception:  # noqa: BLE001
                            clip = None
                    if clip:
                        name = f"a{i:03d}_{k}.mp3"
                        (s.audio_dir / name).write_bytes(clip)
                        names.append(name)
                if names:
                    t["audio"] = names
                    s.audio_files += [n for n in names if n not in s.audio_files]
        rec = STORE.record(s, ended=ended)
        if s.service_request:  # what was filed with the workshop (dry/queued or the live lead id)
            rec["analysis"] = {**(rec.get("analysis") or {}), "service_request": s.service_request}
            rec["next_step"] = "service_request:" + (s.service_request.get("lead_id") or s.service_request.get("status", ""))
        STORE.save(rec)
        if ended and OUTBOX and rec.get("turns"):
            OUTBOX.enqueue(rec)  # CRM push happens in the worker, never on the customer's request path
        if ended and s.audio_dir and s.audio_files:
            ordered = [f for t in s.transcript for f in ([t["audio"]] if isinstance(t.get("audio"), str) else (t.get("audio") or []))]
            def _merge():
                name = STORE.merge_audio(s.audio_dir, ordered)
                if name:
                    rec["audio_files"] = list(rec["audio_files"]) + [name]
                    STORE.save(rec)
            threading.Thread(target=_merge, daemon=True, name="audio-merge").start()
    except Exception as ex:  # noqa: BLE001
        sys.stderr.write(f"[memory] save failed for {s.id}: {type(ex).__name__}: {ex}\n")


def persona_brief(p: dict) -> dict:
    return {
        "id": p.get("id"),
        "name": p.get("display_name"),
        "company": p.get("company"),
        "location": p.get("location"),
        "business_type": p.get("business_type") or "sales",
    }


# ------------------------------------------------------------------ production guards (2026-11-02 audit)
# Origins allowed to call the API from a browser (dealer sites). "*" keeps the dev behaviour.
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in os.getenv("HUMAN_BOT_ALLOWED_ORIGINS", "*").split(",") if o.strip()]
BEHIND_PROXY = os.getenv("HUMAN_BOT_BEHIND_PROXY", "").lower() in ("1", "true", "yes")
MAX_BODY = 64 * 1024                      # JSON bodies; audio goes through /stt with its own 8 MB cap
SESSION_IDLE_S = 30 * 60                  # a session unused this long is dropped
SESSION_MAX = 5000                        # hard cap, oldest first
RATE_PER_MIN = int(os.getenv("HUMAN_BOT_RATE_PER_MIN", "240"))      # API calls per IP per minute
SESSION_PER_MIN = int(os.getenv("HUMAN_BOT_SESSIONS_PER_MIN", "20"))  # /session per IP per minute
TTS_OPEN = os.getenv("HUMAN_BOT_TTS_OPEN", "").lower() in ("1", "true", "yes")  # dev: /tts speaks any text
RATE: dict[str, list] = {}                # ip -> [window_start, api_calls, session_calls]
RATE_LOCK = threading.Lock()
KNOWN_TTS: "OrderedDict[str, None]" = OrderedDict()  # texts this server produced: the only ones /tts will speak
KNOWN_TTS_MAX = 20000
KNOWN_LOCK = threading.Lock()
LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def remember_tts_text(text: str) -> None:
    with KNOWN_LOCK:
        KNOWN_TTS[text] = None
        KNOWN_TTS.move_to_end(text)
        while len(KNOWN_TTS) > KNOWN_TTS_MAX:
            KNOWN_TTS.popitem(last=False)


def tts_text_allowed(text: str, voice: str | None) -> bool:
    if TTS_OPEN:
        return True
    with KNOWN_LOCK:
        return text in KNOWN_TTS


def rate_check(ip: str, session_call: bool = False) -> bool:
    """Fixed one-minute window per client IP. Loopback (the dev page, tests) is exempt."""
    if ip in LOOPBACK and not BEHIND_PROXY:
        return True
    now = time.time()
    with RATE_LOCK:
        rec = RATE.get(ip)
        if not rec or now - rec[0] > 60:
            rec = RATE[ip] = [now, 0, 0]
        rec[1] += 1
        if session_call:
            rec[2] += 1
        if len(RATE) > 10000:  # forget old windows
            for k in [k for k, v in RATE.items() if now - v[0] > 60][:5000]:
                RATE.pop(k, None)
        return rec[1] <= RATE_PER_MIN and rec[2] <= SESSION_PER_MIN


def reap_sessions() -> None:
    """Drop idle sessions so memory is bounded (transcripts + offline brains live per session).
    An idle session is closed properly first: its record is finished and saved."""
    while True:
        time.sleep(60)
        now = time.time()
        with SESSIONS_LOCK:
            idle = [SESSIONS.pop(k) for k in [k for k, s in SESSIONS.items() if now - getattr(s, "last_seen", now) > SESSION_IDLE_S]]
            while len(SESSIONS) > SESSION_MAX:
                idle.append(SESSIONS.pop(next(iter(SESSIONS))))
        for s in idle:
            if s.transcript and any(t.get("role") == "user" for t in s.transcript):
                persist(s, ended=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "HumanBot/1.0"
    protocol_version = "HTTP/1.1"  # keep-alive + chunked /tts, so a proxy can stream and a cut clip is detectable

    def log_message(self, fmt, *args):  # quieter logs: date, client, message
        sys.stderr.write("[%s] %s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), self.client_ip(), fmt % args))

    def send_response(self, code, message=None):
        self._responded = True
        super().send_response(code, message)

    def end_headers(self):
        # embeddable from the dealer sites (all sites when the allow-list is "*") and from this origin
        # itself: the site mirror at "/" frames /embed same-origin, which a bare allow-list would block
        ancestors = "*" if "*" in ALLOWED_ORIGINS else " ".join(["'self'"] + ALLOWED_ORIGINS)
        self.send_header("Content-Security-Policy", f"frame-ancestors {ancestors}")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    # ------------------------------------------------------------------ helpers
    def client_ip(self) -> str:
        if BEHIND_PROXY:
            fwd = self.headers.get("X-Forwarded-For") if hasattr(self, "headers") else None
            if fwd:
                return fwd.split(",")[0].strip()
        return self.client_address[0] if self.client_address else "-"

    def _cors(self) -> None:
        """Reflect the caller's origin when it is allowed; no header otherwise (the browser then blocks)."""
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if "*" in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", "*")
        elif origin and origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self.close_connection = True  # the unread body would poison the keep-alive connection
            raise BodyTooLarge()
        if not n:
            return {}
        try:
            obj = json.loads((self.rfile.read(n) or b"{}").decode("utf-8-sig") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return obj if isinstance(obj, dict) else {}

    def _sse_start(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")  # no length: the close marks the end of the stream
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        self.close_connection = True

    def _sse(self, event: str, data) -> None:
        try:
            self.wfile.write(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise

    # ------------------------------------------------------------ site preview
    # /site shows the persona's real website with the launcher injected, before the client adds
    # the one-line embed. The site's own paths (/assets/..., SPA routes) are proxied through this
    # origin because its module bundles are served without CORS headers and would not load
    # cross-origin. Their analytics tags are stripped so previews never hit the real GA property.
    def _site_origin(self) -> str:
        p = getattr(self.server, "persona", PERSONA)
        return ((p.get("knowledge") or {}).get("website") or p.get("website") or "").rstrip("/")

    def _public_base(self) -> str:
        """This server as the browser reaches it. X-Forwarded-* is trusted only behind a configured proxy."""
        host = (self.headers.get("X-Forwarded-Host") if BEHIND_PROXY else None) or self.headers.get("Host") or "127.0.0.1"
        proto = (self.headers.get("X-Forwarded-Proto") if BEHIND_PROXY else None) or "http"
        return f"{proto}://{host}"

    def _inject_widget(self, html: str) -> str:
        import re
        p = getattr(self.server, "persona", PERSONA)
        base = self._public_base()
        html = re.sub(r'<script[^>]+googletagmanager\.com[^>]*>\s*</script>', "", html, flags=re.I)
        html = re.sub(r'<script>(?:(?!</script>).)*?\b(?:gtag|fbq)\((?:(?!</script>).)*?</script>', "", html, flags=re.S)
        html = re.sub(r'<noscript>(?:(?!</noscript>).)*?facebook\.com/tr(?:(?!</noscript>).)*?</noscript>', "", html, flags=re.S)
        # preview only: hide the site's existing chat/WhatsApp launcher so ours owns the corner
        tag = ('<style data-hhbot-preview>.fixed.right-4.bottom-24.z-40{display:none!important}</style>'
               f'<script src="{base}/widget.js" data-persona="{p.get("id")}" '
               f'data-accent="#0b2f6b" defer></script>')
        return html.replace("</body>", tag + "</body>", 1) if "</body>" in html else html + tag

    def _rewrite_origin(self, text: str, origin: str) -> str:
        """The SPA bundle hardcodes its own absolute origin for API calls; point those at this
        server so they are same-origin and proxied instead of failing CORS."""
        mine = self._public_base()
        bare = origin.split("://", 1)[-1].removeprefix("www.")
        for o in (f"https://www.{bare}", f"https://{bare}", f"http://www.{bare}", f"http://{bare}"):
            text = text.replace(o, mine)
        return text

    def _proxy_site(self, path: str, query: str) -> bool:
        """GET fallback: fetch `path` from the persona website and relay it. Returns False when
        no website is configured so the caller can 404 as before."""
        import urllib.error
        import urllib.request
        origin = self._site_origin()
        if not origin:
            return False
        url = origin + path + (f"?{query}" if query else "")
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (human_bot preview)",
                "Accept": self.headers.get("Accept") or "*/*",
            })
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read()
                ctype = r.headers.get("Content-Type") or "application/octet-stream"
        except urllib.error.HTTPError as ex:
            self._json(ex.code, {"error": f"upstream {ex.code} for {path}"})
            return True
        except (urllib.error.URLError, TimeoutError, OSError) as ex:
            self._json(502, {"error": f"could not fetch {url}: {ex}"})
            return True
        kind = ctype.split(";")[0].strip()
        if kind == "text/html":
            html = self._rewrite_origin(body.decode("utf-8", "replace"), origin)
            body = self._inject_widget(html).encode("utf-8")
            ctype = "text/html; charset=utf-8"
        elif kind in ("application/x-javascript", "application/javascript", "text/javascript"):
            body = self._rewrite_origin(body.decode("utf-8", "replace"), origin).encode("utf-8")
            ctype = "text/javascript; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        return True

    def _site_mirror(self) -> None:
        if not self._proxy_site("/", ""):
            self._json(404, {"error": "persona has no website url (knowledge.website)"})

    def _mirror_on(self) -> bool:
        """The site preview/proxy is a dev tool: on by default only on loopback, never behind a proxy
        unless HUMAN_BOT_SITE_MIRROR=1 (it relays the dealer site through this origin)."""
        return bool(getattr(self.server, "mirror", False))

    # ------------------------------------------------------------------ routes
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")
        # a public https site embedding a bot on 127.0.0.1 is a "local network" request in Chrome;
        # the preflight must opt in (the user still sees a one-time Allow prompt)
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._responded = False
        try:
            return self._get()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as ex:  # noqa: BLE001
            sys.stderr.write(f"[error] GET {self.path}: {type(ex).__name__}: {ex}\n")
            traceback.print_exc()
            self.close_connection = True
            if not self._responded:
                self._json(500, {"error": "internal error"})

    def do_POST(self):
        self._responded = False
        try:
            return self._post()
        except BodyTooLarge:
            self._json(413, {"error": f"body too large (max {MAX_BODY // 1024} KB)"})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as ex:  # noqa: BLE001
            sys.stderr.write(f"[error] POST {self.path}: {type(ex).__name__}: {ex}\n")
            traceback.print_exc()
            self.close_connection = True
            if not self._responded:
                self._json(500, {"error": "internal error"})

    def _get(self):
        path = urlparse(self.path).path
        if path == "/health":
            p = getattr(self.server, "persona", PERSONA)
            provider = provider_name()
            degraded = []
            if provider == "offline":
                degraded.append("no live LLM provider (offline script)")
            if TTS_LAST.get("error"):
                degraded.append("tts error")
            body = {"ok": not degraded, "degraded": degraded, "persona_id": p.get("id"), "brand": p.get("company")}
            if self.client_ip() in LOOPBACK:  # operational detail stays on the box
                body.update({
                    "provider": provider, "persona": p["display_name"], "sessions": len(SESSIONS),
                    "tts": tts_status(), "tts_error": TTS_LAST.get("error"), "stt": stt_status(),
                    "kb": kb_status(), "avatar": avatar_status(),
                    "crm": OUTBOX.status() if OUTBOX else {"sink": None, "note": "recording off"},
                    "leads": {"mode": acr_leads.mode(), "url": acr_leads.LEADS_URL, "dry_runs": len(acr_leads.LOG)},
                })
            return self._json(200 if body["ok"] else 503, body)
        if path == "/conversations" or path.startswith("/conversations/"):
            return self._conversations(path)
        if path == "/personas":
            default = getattr(self.server, "persona", PERSONA)
            return self._json(200, {
                "default": default.get("id"),
                "personas": [persona_brief(p) for p in PERSONAS.values()],
            })
        if path == "/tts":
            return self._tts()
        if path == "/tts_cues":
            # Lip timeline for a clip the server produced (same text/emo/voice as /tts). Waits briefly for a
            # synthesis in flight; [] means "no word timing, use the analyser".
            q = parse_qs(urlparse(self.path).query)
            text = (q.get("text") or [""])[0].strip()
            voice = (q.get("voice") or [""])[0].strip() or None
            emotion = (q.get("emo") or ["neutral"])[0].strip().lower()
            if emotion not in EMOTIONS:
                emotion = "neutral"
            if voice and voice not in ALLOWED_VOICES:
                voice = None
            if not text or len(text) > 600 or not tts_text_allowed(text, voice):
                return self._json(404, {"cues": []})
            key = tts_key(text, voice, emotion)
            deadline = time.monotonic() + 4.0
            while True:
                with TTS_LOCK:
                    cues = TTS_CUES.get(key)
                    inflight = TTS_INFLIGHT.get(key)
                if cues is not None or inflight is None or inflight.done or time.monotonic() > deadline:
                    break
                time.sleep(0.05)
            return self._json(200, {"cues": cues or [], "map": {"A": "closed.png", "B": "E.png", "C": "A.png", "D": "I.png",
                                                                 "E": "E.png", "F": "U.png", "G": "U.png", "H": "L.png", "X": "X.png"}})
        if path == "/site":
            # the SPA router must see "/" or it renders its own 404; the mirror lives at "/"
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return None
        if path == "/" and self._site_origin() and self._mirror_on():
            # persona has a website: "/" previews the real site with the launcher injected;
            # the legacy full-page UI stays reachable at /index.html
            return self._site_mirror()
        if path in ("/widget.js",):
            f = WEB / "widget.js"
        elif path in ("/embed", "/embed.html"):
            f = WEB / "embed.html"
        elif path in ("/demo", "/demo.html"):
            f = WEB / "demo.html"
        elif path in ("/clips", "/clips.html"):
            f = WEB / "clips.html"
        elif path in ("/", "/index.html"):
            f = WEB / "index.html"
        elif path == "/avatar.jpg":
            f = HERE.parents[1] / "tran_moment" / "prepared" / "face.jpg"
        elif path.startswith("/avatar_puppet/"):
            # 2D puppet assets (tran_moment/path_b): aligned mouth-shape layers + mouths.json, and the
            # landmark file for eyelids. Whitelisted names only.
            name = path[len("/avatar_puppet/"):]
            # <name>.png | <name>.json | <expression>/fNN.webp (morph frames)
            if not re.fullmatch(r"[A-Za-z0-9_]+\.(png|json)|[A-Za-z0-9_]+/f\d{2}\.webp", name):
                return self._json(404, {"error": "no such asset"})
            base = HERE.parents[1] / "tran_moment"
            if name == "face.json":
                f = base / "prepared" / "face.json"
            elif name in ("mouths.json", "reactions.json", "expressions.json"):
                f = base / "path_b" / name
            else:  # mouth shapes first, then expression layers (from the grid), then the warp reaction layers
                for sub in ("mouths", "expressions", "reactions"):
                    f = base / "path_b" / sub / name
                    if f.is_file():
                        break
            if not f.is_file():
                return self._json(404, {"error": "no such asset"})
            body = f.read_bytes()
            if name.endswith(".json"):
                # The manifests carry the asset version: the card appends ?v=<version> to every image URL, so
                # a rebuilt portrait is never mixed with images a browser cached from the previous one
                # (seen 2026-11-03: old base.png under new expression layers).
                try:
                    obj = json.loads(body.decode("utf-8"))
                    obj["version"] = puppet_version()
                    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                except ValueError:
                    pass
            self.send_response(200)
            self.send_header("Content-Type", "image/png" if name.endswith(".png") else "image/webp" if name.endswith(".webp") else "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if name.endswith(".json") or not parse_qs(urlparse(self.path).query).get("v"):
                self.send_header("Cache-Control", "no-cache, must-revalidate")   # manifests and unversioned images: revalidate
                self.send_header("ETag", '"%x-%x"' % (f.stat().st_mtime_ns, len(body)))
            else:
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")  # versioned image URL
            self._cors()  # the voice widget on another origin reads face.json for its blink/mouth placement
            self.end_headers()
            return self.wfile.write(body)
        elif path.startswith("/avatar_clips/"):
            # reaction clips + manifest (tran_moment/prepared/clips); names are whitelisted, no traversal
            name = path[len("/avatar_clips/"):]
            if not re.fullmatch(r"[a-z0-9_]+\.(mp4|json)", name):
                return self._json(404, {"error": "no such clip"})
            # ?set=neha (default: synthesized from the CGI portrait) | ?set=sample (presenter footage, test only)
            clip_set = parse_qs(urlparse(self.path).query).get("set", ["neha"])[0]
            folder = {"neha": "clips_neha", "neha_video": "clips_neha", "neha_render": "clips_neha_render", "sample": "clips"}.get(clip_set)
            if not folder:
                return self._json(404, {"error": "unknown clip set"})
            f = HERE.parents[1] / "tran_moment" / "prepared" / folder / name
            if not f.is_file():
                return self._json(404, {"error": "no such clip"})
            body = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4" if name.endswith(".mp4") else "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            return self.wfile.write(body)
        elif path == "/avatar_idle.mp4":
            # optional pre-rendered idle loop (breathing, blinks, sway) of the same portrait;
            # the card falls back to CSS motion on the still when this 404s
            f = HERE.parents[1] / "tran_moment" / "prepared" / "idle.mp4"
            if not f.is_file():
                return self._json(404, {"error": "no idle loop rendered yet"})  # never fall through to the site proxy
        else:
            # Anything else is a file inside human_bot/web only. The path is resolved and confined:
            # "/web/../../voice_clone/.env" used to be served (2026-11-02 audit).
            rel = path.lstrip("/").removeprefix("human_bot/").removeprefix("web/")
            f = (WEB_ROOT / rel).resolve()
            if not f.is_relative_to(WEB_ROOT) or f.suffix not in STATIC_TYPES or f.name.startswith("."):
                f = None
        if f is not None and f.is_file():
            body = f.read_bytes()
            ctype = STATIC_TYPES.get(f.suffix, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            # Widget code and pages change often during development: make browsers revalidate on
            # every load so a reload always shows the current card. Media stays cacheable, except the
            # portrait itself, which must follow a rebuild immediately.
            if f.suffix in (".html", ".js", ".css") or path == "/avatar.jpg":
                self.send_header("Cache-Control", "no-cache, must-revalidate")
                self.send_header("ETag", '"%x-%x"' % (f.stat().st_mtime_ns, len(body)))
            else:
                self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            return self.wfile.write(body)
        if self._mirror_on() and self._proxy_site(path, urlparse(self.path).query):
            return None
        return self._json(404, {"error": "not found"})

    def _avatar(self, path: str, body: dict) -> None:
        """Browser ↔ avatar-vendor relay for the live talking-head stream (keys stay here)."""
        if simli_avatar.enabled():
            # Simli: server mints a session token; the browser SDK does WebRTC and we stream PCM audio.
            # The face is the server's: a client-chosen face_id would bill minutes on any face.
            try:
                if path == "/avatar/start":
                    return self._json(200, simli_avatar.start_stream(None))
                if path == "/avatar/faces":
                    return self._json(200, {"faces": simli_avatar.faces(), "default": simli_avatar.FACE_ID})
                if path in ("/avatar/stop", "/avatar/say", "/avatar/sdp", "/avatar/ice"):
                    return self._json(200, {"ok": True, "note": "handled in the browser SDK for simli"})
            except simli_avatar.AvatarUnavailable as ex:
                return self._json(503, {"error": str(ex)[:300], "fallback": "puppet"})
            return self._json(404, {"error": "not found"})
        if not did_avatar.enabled():
            return self._json(503, {"error": "avatar stream disabled", "fallback": "puppet"})
        sid, sess = str(body.get("id") or ""), str(body.get("session_id") or "")
        try:
            if path == "/avatar/start":
                return self._json(200, did_avatar.start_stream())
            if not sid:
                return self._json(400, {"error": "id required"})
            if path == "/avatar/sdp":
                return self._json(200, did_avatar.send_answer(sid, sess, body.get("answer") or {}))
            if path == "/avatar/ice":
                return self._json(200, did_avatar.send_ice(sid, sess, body.get("candidate") or {}))
            if path == "/avatar/say":
                text = str(body.get("text") or "").strip()
                if not text:
                    return self._json(400, {"error": "text required"})
                if not tts_text_allowed(text, None):
                    return self._json(403, {"error": "only text produced by this server can be spoken"})
                return self._json(200, did_avatar.say(sid, sess, text, None))  # server-side voice only (female)
            if path == "/avatar/stop":
                return self._json(200, did_avatar.stop_stream(sid, sess))
        except did_avatar.AvatarUnavailable as ex:
            return self._json(503, {"error": str(ex)[:300], "fallback": "puppet"})
        return self._json(404, {"error": "not found"})

    def _stt(self) -> None:
        """Raw audio body (webm/opus, mp4 or wav) -> {"text": ...}; 503 tells the card to use the browser recogniser."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        mime = (self.headers.get("Content-Type") or "audio/webm").split(";")[0].strip() or "audio/webm"
        if not n or n > 8_000_000:
            self.close_connection = True  # body not consumed
            return self._json(400, {"error": "audio body required (max 8 MB)"})
        audio = self.rfile.read(n)
        t0 = time.perf_counter()
        try:
            text = transcribe(audio, mime)
        except STTUnavailable as ex:
            return self._json(503, {"error": str(ex)[:200]})
        # Recording: the raw utterance is kept with the conversation when the card tells us which one it is.
        sid = (parse_qs(urlparse(self.path).query).get("session_id") or [""])[0]
        s = get_session(sid) if sid else None
        if s and text.strip():
            s.prefetch_knowledge(text)  # the kb lookup runs while the card is still deciding the turn has ended
        if s and s.audio_dir and text.strip():
            ext = "webm" if "webm" in mime else "mp4" if "mp4" in mime else "wav"
            name = f"u{len(s.audio_files):03d}.{ext}"
            try:
                (s.audio_dir / name).write_bytes(audio)
                s.audio_files.append(name)
                s.pending_audio = name
            except OSError:
                pass
        return self._json(200, {"text": text, "ms": int((time.perf_counter() - t0) * 1000)})

    def _admin_ok(self) -> bool:
        return self.client_ip() in LOOPBACK or (bool(ADMIN_KEY) and self.headers.get("X-Admin-Key") == ADMIN_KEY)

    def _conversations(self, path: str) -> None:
        """Structured records for the CRM / dashboard. Loopback or X-Admin-Key only."""
        if not STORE:
            return self._json(404, {"error": "recording is off (HUMAN_BOT_RECORD=0)"})
        if not self._admin_ok():
            return self._json(403, {"error": "admin only"})
        q = parse_qs(urlparse(self.path).query)
        rest = path[len("/conversations"):].strip("/")
        if not rest:
            rows = STORE.list(limit=int((q.get("limit") or ["50"])[0] or 50), visitor_id=(q.get("visitor") or [None])[0],
                              phone=(q.get("phone") or [None])[0])
            return self._json(200, {"conversations": rows, "count": len(rows)})
        parts = rest.split("/")
        conv = STORE.get(parts[0])
        if not conv:
            return self._json(404, {"error": "no such conversation"})
        if len(parts) == 1:
            return self._json(200, conv)
        if len(parts) == 3 and parts[1] == "audio" and re.fullmatch(r"[A-Za-z0-9_.-]+", parts[2]) and conv.get("audio_dir"):
            f = Path(conv["audio_dir"]) / parts[2]
            if f.is_file():
                body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", {"mp3": "audio/mpeg", "webm": "audio/webm", "mp4": "audio/mp4", "m4a": "audio/mp4", "wav": "audio/wav"}.get(f.suffix[1:], "application/octet-stream"))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "private, no-store")
                self.end_headers()
                return self.wfile.write(body)
        return self._json(404, {"error": "not found"})

    def _post(self):
        path = urlparse(self.path).path
        ip = self.client_ip()
        if not rate_check(ip, session_call=(path == "/session")):
            self.close_connection = True
            return self._json(429, {"error": "too many requests"})
        if path == "/stt":
            return self._stt()
        body = self._body()

        if path.startswith("/avatar/"):
            return self._avatar(path, body)
        if path == "/session":
            default = getattr(self.server, "persona", PERSONA)
            pid = body.get("persona")
            persona = (PERSONAS.get(pid) if isinstance(pid, str) else None) or default
            context = clean_context(body.get("sessionContext") or body.get("context") or {})
            s = new_session(context, persona)
            model = ((context.get("page") or {}).get("model") or "").strip()
            model = model.replace("Hyundai ", "").replace("hyundai ", "")
            # the loader derives "model" from the page slug; only a real car name may enter the greeting
            # ("Aapki Demo.Html mein kya kaam karwana hai?" was heard on the demo page)
            if model and not any(w in KNOWN_CARS for w in model.lower().split()):
                model = ""
            service = (persona.get("business_type") or "sales") == "service"
            # returning customer: earlier conversations by visitor id feed the prompt and the greeting
            s.visitor_id = context.get("visitor", "")
            if STORE and s.visitor_id:
                s.recalled = STORE.recall(visitor_id=s.visitor_id, limit=3)
                s.recall_block = STORE.recall_block(s.recalled)
            last = s.recalled[0] if s.recalled else None
            if last and (last.get("car") or last.get("problem")):
                car = last.get("car") or "gaadi"
                if service and last.get("problem"):
                    opener = (f"Namaste, wapas swagat hai! Main {persona['display_name']}. Pichli baar aapki {car} ki "
                              f"{last['problem']} ki baat hui thi. Kya woh theek ho gayi?")
                elif service:
                    opener = (f"Namaste, wapas swagat hai! Main {persona['display_name']}. Pichli baar aapki {car} ki baat hui thi. "
                              "Aaj kya karwana hai?")
                else:
                    opener = (f"Namaste, wapas swagat hai! Main {persona['display_name']}. Pichli baar aap {car} dekh rahe the. "
                              "Wahin se aage baat karein?")
            elif model and service:
                opener = (f"Namaste! Main {persona['display_name']}, {persona['company']} se. "
                          f"Aapki {model} mein kya kaam karwana hai?")
            elif model:
                opener = (f"Namaste! Main {persona['display_name']}, {persona['company']} se. "
                          f"{model} ke baare mein hi baat karni hai?")
            elif service:
                opener = (f"Namaste! Main {persona['display_name']}, {persona['company']} se. "
                          "Aapki car mein kya problem hai?")
            else:
                opener = (f"Namaste! Main {persona['display_name']}, {persona['company']} se. "
                          "Aap kaunsi car dekh rahe hain?")
            greeting = humanize(
                opener,
                # a returning-customer opener carries the recap sentence; the 14-word cap would cut it
                max_words=32 if last else persona["conversation"]["max_words_first_turn"],
                language=s.language,
                female=is_female(persona),
            )
            chunks = [{"text": c, "emotion": emotion_of(c),
                       "gesture": pick_gesture(persona["avatar"]["gestures"], emotion_of(c), i)}
                      for i, c in enumerate(delivery_chunks(greeting))]
            if STORE:
                # Recording is disclosed in the first breath (DPDP): a separate short chunk after the greeting.
                line = DISCLOSURE["service" if service else "sales"]
                chunks.append({"text": line, "emotion": "neutral", "gesture": "small_nod"})
                greeting = greeting + " " + line
                s.consent = True  # disclosed; continuing the call is the customer's consent
                s.audio_dir = STORE.audio_dir_for(s.id)
            s.transcript.append({"role": "assistant", "text": greeting, "t": time.time()})
            # start the greeting's clips now; the card asks for them a few hundred ms from here
            for c in chunks:
                prefetch_tts([c["text"]], emotion=c["emotion"])
            return self._json(200, {
                "session_id": s.id,
                "greeting": greeting,
                "chunks": chunks,
                "provider": provider_name(),
                "persona": persona_brief(persona),
                "returning": bool(s.recalled),
                "recording": bool(STORE),
            })

        if path == "/end":
            sid = body.get("session_id")
            s = get_session(sid) if isinstance(sid, str) else None
            if not s:
                return self._json(404, {"error": "unknown session"})
            with SESSIONS_LOCK:
                SESSIONS.pop(s.id, None)
            if any(t.get("role") == "user" for t in s.transcript):
                try:
                    s.analysis = analyze(s.transcript)
                except Exception:  # noqa: BLE001
                    s.analysis = None
                # the record (clips, merge) completes in the background; the card is not kept waiting
                threading.Thread(target=persist, args=(s, True), daemon=True, name="memory-end").start()
                return self._json(200, {"ok": True, "conversation_id": s.id, "turns": sum(1 for t in s.transcript if t.get("role") == "user"),
                                        "lead": (s.analysis or {}).get("lead_status")})
            return self._json(200, {"ok": True, "conversation_id": None, "turns": 0})

        if path == "/chat":
            return self._chat(body)

        if path == "/analyze":
            sid = body.get("session_id")
            s = get_session(sid) if isinstance(sid, str) else None
            if not s:
                return self._json(404, {"error": "unknown session"})
            return self._json(200, analyze(s.transcript))

        return self._json(404, {"error": "not found"})

    # ------------------------------------------------------------------ voice
    def _tts(self) -> None:
        q = parse_qs(urlparse(self.path).query)
        text = (q.get("text") or [""])[0].strip()
        voice = (q.get("voice") or [""])[0].strip() or None
        if not text or len(text) > 600:
            return self._json(400, {"error": "text required (max 600 chars)"})
        if not rate_check(self.client_ip()):
            return self._json(429, {"error": "too many requests"})
        if voice and voice not in ALLOWED_VOICES:
            voice = None  # female Indian voices only; an unknown voice falls back to the server's
        emotion = (q.get("emo") or ["neutral"])[0].strip().lower()
        if emotion not in EMOTIONS:
            emotion = "neutral"
        if not tts_text_allowed(text, voice):
            # Only sentences this server produced are synthesized: /tts is not an open text-to-speech API.
            return self._json(403, {"error": "only text produced by this server can be spoken"})
        job = tts_job(text, voice, emotion)
        if (q.get("prefetch") or [""])[0]:
            return self._json(202, {"started": True})  # the card warms a sentence; it will play it from /tts later
        with TTS_LOCK:
            cached = TTS_CACHE.get(tts_key(text, voice, emotion))
        if cached:
            return self._audio(cached)  # whole clip: Content-Length + byte ranges (iOS probes with Range)
        # Stream: the first chunk arrives ~1 s in; the browser's <audio> starts playing it while the rest
        # is still being synthesized. Chunked framing lets a proxy pass it through and the browser tell a
        # finished clip from a cut one.
        chunks = job.stream()
        try:
            first = next(chunks)
        except (TTSUnavailable, StopIteration) as ex:
            return self._json(503, {"error": str(ex)[:220] or "empty audio", "fallback": "browser"})
        self.send_response(200)
        self.send_header("Content-Type", self._audio_type(first))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        try:
            self._chunk(first)
            for chunk in chunks:
                self._chunk(chunk)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TTSUnavailable):
            self.close_connection = True  # the card cut in (barge-in) or Edge died mid-clip: the stream ends unterminated

    def _chunk(self, data: bytes) -> None:
        self.wfile.write(b"%x\r\n" % len(data))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    @staticmethod
    def _audio_type(audio: bytes) -> str:
        return "audio/wav" if audio[:4] == b"RIFF" else "audio/mpeg" if audio[:3] == b"ID3" or audio[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") else "application/octet-stream"

    def _audio(self, audio: bytes) -> None:
        """A complete clip, with single-range support (iOS Safari probes media with Range: bytes=0-1)."""
        ctype = self._audio_type(audio)
        start, end = 0, len(audio) - 1
        rng = self.headers.get("Range") or ""
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
        partial = False
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), len(audio) - 1)
            else:  # suffix range: last N bytes
                start = max(0, len(audio) - int(m.group(2)))
            if start > end or start >= len(audio):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(audio)}")
                self.send_header("Content-Length", "0")
                self._cors()
                return self.end_headers()
            partial = True
        body = audio[start:end + 1]
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(audio)}")
        self.send_header("Cache-Control", "private, max-age=86400")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------ chat
    def _file_service_request(self, s: Session, slots: dict) -> str:
        """Submit the collected request (dry or live) and return exactly what Neha says about it.
        Never a made-up reference: a number is spoken only when the backend returned one."""
        page = ((s.context or {}).get("page") or {}).get("url", "")
        try:
            res = acr_leads.submit(slots, s.id, page)
        except acr_leads.LeadError as ex:
            sys.stderr.write(f"[leads] submit failed for {s.id}: {ex}\n")
            s.service_request = {"status": "failed", "error": str(ex)[:200], "slots": slots, "t": time.time()}
            s.sr_pending = False
            return "Maaf kijiye, abhi request system mein nahi ja paayi. Main team se aapko call karwa deti hoon, aapka number note hai."
        s.service_request = {"status": "sent" if res["sent"] else "queued", "lead_id": res.get("lead_id"), "mode": res["mode"], "slots": slots, "t": time.time()}
        s.sr_pending = False
        phone = slots.get("phone", "")
        digits = " ".join(phone) if phone else ""
        if res.get("lead_id"):
            return (f"Done ji. Aapki request number {res['lead_id']} hai, note kar lijiye. "
                    f"{sr_summary(slots)}. Team aapko {digits} par jaldi call karegi.")
        return (f"Done ji. Aapki request note ho gayi: {sr_summary(slots)}. "
                f"Team aapko {digits} par jaldi call karegi.")

    def _chat(self, body: dict) -> None:
        sid = body.get("session_id")
        s = get_session(sid) if isinstance(sid, str) else None
        if not s:
            return self._json(404, {"error": "unknown session"})
        text_in = body.get("text")
        user_text = (text_in if isinstance(text_in, str) else "").strip()[:2000]
        if not user_text:
            return self._json(400, {"error": "text required"})
        s.last_seen = time.time()

        with s.lock:
            s.generation += 1
            my_gen = s.generation
        turn: dict = {"role": "user", "text": user_text, "t": time.time()}
        if s.pending_audio:  # the recording /stt just transcribed belongs to this utterance
            turn["audio"] = s.pending_audio
            s.pending_audio = None
        s.transcript.append(turn)

        self._sse_start()
        t0 = time.perf_counter()
        self._sse("meta", {"generation": my_gen, "provider": provider_name()})

        persona = s.persona
        female = is_female(persona)
        bc = pick_backchannel(user_text, len(s.transcript), options=persona["conversation"].get("backchannels"))
        if bc:
            self._sse("backchannel", {"text": bc})
            prefetch_tts([bc])  # normally already warm; this covers a persona added after start-up

        buf, spoken, idx = "", [], 0
        # "explain in detail" lifts the per-sentence cap for this one turn; the prompt tells the model the same
        max_words = (persona["conversation"].get("max_words_detail", 70) if wants_detail(user_text)
                     else persona["conversation"]["max_words_turn"])
        try:
            # website knowledge for this turn (only personas with a `knowledge` block; empty block otherwise)
            kb_extra, hits = s.knowledge(user_text)
            if hits:
                self._sse("sources", {"results": [
                    {"url": h.get("url"), "title": h.get("title"), "score": h.get("score"), "page_type": h.get("page_type")}
                    for h in hits
                ]})
            s.brain.context = hits  # offline script quotes the website passages instead of guessing
            # Service request: the server collects the facts from the customer's words, tells the model what
            # is still missing, and files the request itself once the customer has said yes to the read-back.
            source = None
            service = (persona.get("business_type") or "sales") == "service"
            if service and not s.service_request:
                slots = sr_collect(s.transcript)
                if s.sr_pending and sr_is_no(user_text):
                    s.sr_pending = False
                if s.sr_pending and not sr_missing(slots) and sr_is_yes(user_text):
                    source = iter([self._file_service_request(s, slots)])
                elif sr_wants(s.transcript, slots):
                    kb_extra = (kb_extra + "\n\n" if kb_extra else "") + sr_prompt_block(slots, s.sr_pending)
            for token in (source if source is not None else stream_reply(s.messages(extra=kb_extra), fallback=s.brain)):
                if s.generation != my_gen:  # barge-in: a newer turn superseded us
                    self._sse("interrupted", {"generation": my_gen})
                    return
                buf += token
                # First words first: an opening interjection ("Acha, ...", "Haan ji, ...") goes out on its own the
                # moment the comma arrives. Those clips are warm from start-up, so the customer hears her react
                # ~1 s before the first full sentence can be synthesized (Edge needs ~1 s per fresh clip).
                if idx == 0 and not spoken:
                    m_int = _OPENER.match(buf)
                    if m_int:
                        opener, buf = m_int.group(1), buf[m_int.end(1):].lstrip(" ,!")
                        piece = cached_opener(opener, persona)
                        spoken.append(piece)
                        emo = emotion_of(piece)
                        self._sse("sentence", {"index": idx, "text": piece, "emotion": emo,
                                               "gesture": pick_gesture(persona["avatar"]["gestures"], emo, idx)})
                        prefetch_tts([piece], emotion=emo)
                        idx += 1
                # flush at a sentence boundary so TTS can start before the reply ends
                while True:
                    m = split_sentences(buf)
                    if len(m) <= 1:
                        break
                    head, buf = m[0], " ".join(m[1:])
                    piece = humanize(head, max_words=max_words, language=s.language, female=female)
                    if not piece:
                        continue
                    spoken.append(piece)
                    emo = emotion_of(piece)
                    self._sse("sentence", {
                        "index": idx,
                        "text": piece,
                        "emotion": emo,
                        "gesture": pick_gesture(persona["avatar"]["gestures"], emo, idx),
                    })
                    prefetch_tts([piece], emotion=emo)  # synthesis runs while the model is still writing the next sentence
                    idx += 1

            tail = humanize(buf, max_words=max_words, language=s.language, female=female)
            if tail:
                spoken.append(tail)
                emo = emotion_of(tail)
                self._sse("sentence", {
                    "index": idx, "text": tail, "emotion": emo,
                    "gesture": pick_gesture(persona["avatar"]["gestures"], emo, idx),
                })
                prefetch_tts([tail], emotion=emo)

            full = " ".join(spoken).strip()
            if source is None and not s.service_request:
                s.sr_pending = asked_confirmation(full)  # the model just read the request back and asked "bhej doon?"
            if not full:  # model produced nothing usable: answer from the persona rules
                full = humanize(s.brain.reply(user_text), max_words=max_words, language=s.language, female=female)
            if not full:
                full = "Ek second, main check karti hoon." if female else "Ek second, main check karta hoon."
            s.transcript.append({"role": "assistant", "text": full, "t": time.time()})
            chunks = [{"text": c, "emotion": emotion_of(c),
                       "gesture": pick_gesture(persona["avatar"]["gestures"], emotion_of(c), i)}
                      for i, c in enumerate(delivery_chunks(full))]
            self._sse("final", {
                "text": full,
                "chunks": chunks,
                "latency_ms": max(1, int((time.perf_counter() - t0) * 1000)),
                "words": len(full.split()),
            })
            if STORE:  # the record is current after every turn, so nothing is lost if the tab is closed
                threading.Thread(target=persist, args=(s,), daemon=True, name="memory-save").start()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as ex:  # noqa: BLE001
            try:
                self._sse("error", {"message": f"{type(ex).__name__}: {ex}"[:300]})
            except Exception:  # noqa: BLE001
                pass
        finally:
            self.close_connection = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--persona", default=None,
                    help="default persona id, e.g. galaxy-toyota or hans-hyundai")
    a = ap.parse_args()

    persona = PERSONA
    if a.persona:
        pid = a.persona.strip().lower()
        match = PERSONAS.get(pid) or next(
            (p for k, p in PERSONAS.items() if pid in k or pid in (p.get("company") or "").lower().replace(" ", "-")),
            None,
        )
        if not match:
            print(f"unknown persona '{a.persona}'. available: {', '.join(PERSONAS)}", file=sys.stderr)
            return 2
        persona = match

    # One bot per port. ThreadingHTTPServer sets allow_reuse_address, and on Windows SO_REUSEADDR lets a second
    # process bind the same port, so two bots silently split the traffic (seen 2026-11-03: an old and a new
    # process both answering :4001). Probe first and refuse loudly; on Windows also drop the reuse flag.
    import socket
    probe_host = "127.0.0.1" if a.host in ("", "0.0.0.0") else a.host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex((probe_host, a.port)) == 0:
            print(f"port {a.port} is already serving (another human_bot?): refusing to start a second one", file=sys.stderr)
            return 2
    if sys.platform == "win32":
        ThreadingHTTPServer.allow_reuse_address = False
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    srv.persona = persona
    mirror_env = os.getenv("HUMAN_BOT_SITE_MIRROR", "").lower()
    srv.mirror = mirror_env in ("1", "true", "yes") if mirror_env else (a.host in LOOPBACK and not BEHIND_PROXY)
    if "*" in ALLOWED_ORIGINS and a.host not in LOOPBACK:
        print("[warn] HUMAN_BOT_ALLOWED_ORIGINS is '*' on a non-loopback host: any site can call this API", file=sys.stderr, flush=True)
    print(f"human_bot on http://{a.host}:{a.port}  provider={provider_name()}  "
          f"persona={persona['display_name']} ({persona.get('company')})  mirror={'on' if srv.mirror else 'off'}", flush=True)
    threading.Thread(target=warm_tts, args=(PERSONAS,), daemon=True, name="tts-warm").start()
    threading.Thread(target=reap_sessions, daemon=True, name="session-reaper").start()

    def _term(*_):  # SIGTERM from systemd/Docker: leave serve_forever the same way Ctrl+C does
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, _term)
    except (ValueError, OSError):
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        print("human_bot stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
