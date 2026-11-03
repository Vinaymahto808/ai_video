"""Conversation memory: every call is stored (transcript, extracted facts, analysis, audio) and a returning
visitor is recognised, so Neha can pick up where the last conversation ended.

Decision 2026-11-02 (reverses the 2026-10-10 "no saved conversations"): the owner wants full recordings and
a structured record per lead for the CRM. Recording is disclosed in the greeting (DPDP Act 2023).

Storage: SQLite (stdlib) at human_bot/data/conversations.sqlite, audio under human_bot/data/audio/<id>/.
One row per conversation, upserted after every turn (a crash loses nothing), plus an FTS5 index over the
turns so earlier conversations of the same visitor can be searched by topic.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent / "data"
DEFAULT_DB = DATA_DIR / "conversations.sqlite"
AUDIO_DIR = DATA_DIR / "audio"

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  visitor_id TEXT, persona_id TEXT, page_url TEXT,
  started REAL NOT NULL, updated REAL NOT NULL, ended REAL,
  turns INTEGER NOT NULL DEFAULT 0,
  consent INTEGER NOT NULL DEFAULT 0,
  phone TEXT, name TEXT, car TEXT, area TEXT, problem TEXT,
  intent TEXT, lead TEXT, next_step TEXT, summary TEXT,
  transcript TEXT NOT NULL, analysis TEXT, audio_dir TEXT, audio_files TEXT
);
CREATE INDEX IF NOT EXISTS conv_visitor ON conversations(visitor_id, started);
CREATE INDEX IF NOT EXISTS conv_phone ON conversations(phone, started);
"""
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(conversation_id UNINDEXED, role UNINDEXED, text, tokenize='unicode61');
"""

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)")
NAME_RE = re.compile(r"\b(?:mera naam|my name is|naam hai|main|i am|this is)\s+([A-Z][a-z]{2,15}(?:\s+[A-Z][a-z]{2,15})?)")
INTENTS = (
    ("insurance_claim", r"\b(insurance|claim|accident|cashless|surveyor)\b"),
    ("ac", r"\b(ac|a\.c\.|cooling|thanda)\b"),
    ("denting_painting", r"\b(dent|denting|paint|scratch|bumper|body ?shop)\b"),
    ("brakes_clutch", r"\b(brake|brakes|clutch)\b"),
    ("suspension", r"\b(suspension|shocker|shock|awaaz|noise)\b"),
    ("engine", r"\b(engine|gearbox|overheat|smoke|dhuan|start nahi)\b"),
    ("battery_tyre", r"\b(battery|tyre|tire|puncture|alignment)\b"),
    ("periodic_service", r"\b(service|servicing|oil|filter|periodic|checkup)\b"),
    ("test_drive", r"\b(test drive|drive)\b"),
    ("price_enquiry", r"\b(price|kitna|kitne|cost|quote|emi|finance|loan)\b"),
)
NEXT_STEPS = (
    ("callback", r"\b(call ?back|call karegi|call karega|call kar|phone kar)\w*"),
    ("pickup", r"\b(pickup|pick up|uthwa|doorstep)\w*"),
    ("workshop_visit", r"\b(workshop|visit|aa jaiye|aa jao|le aaiye)\w*"),
    ("whatsapp", r"\bwhatsapp\b"),
    ("test_drive", r"\btest drive\b"),
)


def _slots(transcript: list[dict]) -> dict[str, str]:
    """Facts the customer gave, pulled from the user turns (the assistant's words are not evidence)."""
    from llm.client import OfflineServiceBrain  # noqa: PLC0415 (same regex tables as the offline script)
    out: dict[str, str] = {}
    user = " ".join(t.get("text", "") for t in transcript if t.get("role") == "user")
    low = " " + user.lower() + " "
    m = PHONE_RE.search(user)
    if m:
        out["phone"] = m.group(1)
    m = NAME_RE.search(user)
    if m and m.group(1).lower() not in ("neha", "auto", "car", "gurgaon", "delhi", "noida"):
        out["name"] = m.group(1)
    for name in OfflineServiceBrain._MODELS:
        if f" {name} " in low or f" {name}," in low:
            out["car"] = name.title()
            break
    else:
        for make in OfflineServiceBrain._MAKES:
            if f" {make} " in low or f" {make}," in low:
                out["car"] = make.title()
                break
    for key, label in OfflineServiceBrain._AREAS.items():
        if key in low:
            out["area"] = label
            break
    for pat, label in OfflineServiceBrain._SERVICES:
        if re.search(pat, low):
            out["problem"] = label
            break
    return out


def _intent(transcript: list[dict]) -> str:
    user = " ".join(t.get("text", "") for t in transcript if t.get("role") == "user").lower()
    for name, pat in INTENTS:
        if re.search(pat, user):
            return name
    return "general"


def _next_step(transcript: list[dict]) -> str:
    agent = " ".join(t.get("text", "") for t in transcript[-4:] if t.get("role") == "assistant").lower()
    for name, pat in NEXT_STEPS:
        if re.search(pat, agent):
            return name
    return ""


def _summary(slots: dict, intent: str, lead: str, next_step: str, turns: int) -> str:
    bits = []
    if slots.get("car"):
        bits.append(slots["car"])
    if slots.get("problem"):
        bits.append(slots["problem"])
    elif intent != "general":
        bits.append(intent.replace("_", " "))
    if slots.get("area"):
        bits.append("in " + slots["area"])
    s = ", ".join(bits) if bits else "general enquiry"
    s += f"; {turns} turns"
    if next_step:
        s += f"; next: {next_step}"
    if lead:
        s += f"; lead {lead}"
    return s


class ConversationStore:
    def __init__(self, path: Path = DEFAULT_DB) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)
        self.fts = True
        try:
            self.db.executescript(FTS_SCHEMA)
        except sqlite3.OperationalError:
            self.fts = False
        self.db.commit()

    # ---------------------------------------------------------------- write
    def record(self, session: Any, ended: bool = False, analysis: dict | None = None) -> dict:
        """Build the structured record for a live Session (server/app.py) without touching the db."""
        transcript = list(session.transcript)
        slots = _slots(transcript)
        intent = _intent(transcript)
        an = analysis or getattr(session, "analysis", None) or {}
        lead = (an.get("lead_status") or an.get("lead_quality") or an.get("lead") or "") if isinstance(an, dict) else ""
        next_step = _next_step(transcript)
        turns = sum(1 for t in transcript if t.get("role") == "user")
        return {
            "id": session.id,
            "visitor_id": getattr(session, "visitor_id", "") or "",
            "persona_id": session.persona.get("id"),
            "page_url": ((session.context or {}).get("page") or {}).get("url", ""),
            "started": session.started,
            "updated": time.time(),
            "ended": time.time() if ended else None,
            "turns": turns,
            "consent": 1 if getattr(session, "consent", False) else 0,
            "phone": slots.get("phone"), "name": slots.get("name"), "car": slots.get("car"),
            "area": slots.get("area"), "problem": slots.get("problem"),
            "intent": intent, "lead": lead or None, "next_step": next_step or None,
            "summary": _summary(slots, intent, lead, next_step, turns),
            "transcript": transcript,
            "analysis": an or None,
            "audio_dir": str(getattr(session, "audio_dir", "") or ""),
            "audio_files": list(getattr(session, "audio_files", []) or []),
        }

    def save(self, rec: dict) -> None:
        cols = ("id", "visitor_id", "persona_id", "page_url", "started", "updated", "ended", "turns", "consent",
                "phone", "name", "car", "area", "problem", "intent", "lead", "next_step", "summary",
                "transcript", "analysis", "audio_dir", "audio_files")
        row = {k: rec.get(k) for k in cols}
        row["transcript"] = json.dumps(rec.get("transcript") or [], ensure_ascii=False)
        row["analysis"] = json.dumps(rec["analysis"], ensure_ascii=False) if rec.get("analysis") else None
        row["audio_files"] = json.dumps(rec.get("audio_files") or [])
        sets = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("id", "started"))
        with self.lock:
            self.db.execute(
                f"INSERT INTO conversations({', '.join(cols)}) VALUES({', '.join(':' + c for c in cols)}) "
                f"ON CONFLICT(id) DO UPDATE SET {sets}", row)
            if self.fts:
                self.db.execute("DELETE FROM turns_fts WHERE conversation_id=?", (rec["id"],))
                self.db.executemany("INSERT INTO turns_fts(conversation_id, role, text) VALUES(?,?,?)",
                                    [(rec["id"], t.get("role", ""), t.get("text", "")) for t in rec.get("transcript") or []])
            self.db.commit()

    # ---------------------------------------------------------------- read
    def _rows(self, sql: str, args: tuple) -> list[dict]:
        with self.lock:
            rows = self.db.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["transcript"] = json.loads(d["transcript"]) if d.get("transcript") else []
            d["analysis"] = json.loads(d["analysis"]) if d.get("analysis") else None
            d["audio_files"] = json.loads(d["audio_files"]) if d.get("audio_files") else []
            out.append(d)
        return out

    def get(self, conv_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM conversations WHERE id=?", (conv_id,))
        return rows[0] if rows else None

    def list(self, limit: int = 50, visitor_id: str | None = None, phone: str | None = None, since: float | None = None) -> list[dict]:
        sql, args = "SELECT * FROM conversations WHERE 1=1", []
        if visitor_id:
            sql += " AND visitor_id=?"; args.append(visitor_id)
        if phone:
            sql += " AND phone=?"; args.append(phone)
        if since:
            sql += " AND started>=?"; args.append(since)
        sql += " ORDER BY started DESC LIMIT ?"; args.append(max(1, min(limit, 500)))
        rows = self._rows(sql, tuple(args))
        for r in rows:  # list view: no transcript bodies
            r["transcript"] = None
            r["analysis"] = None
        return rows

    def recall(self, visitor_id: str | None = None, phone: str | None = None, limit: int = 3, exclude: str | None = None) -> list[dict]:
        """Earlier conversations of this visitor (by visitor id, or by phone once known), newest first."""
        if not visitor_id and not phone:
            return []
        sql = "SELECT * FROM conversations WHERE turns>0 AND ("
        parts, args = [], []
        if visitor_id:
            parts.append("visitor_id=?"); args.append(visitor_id)
        if phone:
            parts.append("phone=?"); args.append(phone)
        sql += " OR ".join(parts) + ")"
        if exclude:
            sql += " AND id<>?"; args.append(exclude)
        sql += " ORDER BY started DESC LIMIT ?"; args.append(limit)
        return self._rows(sql, tuple(args))

    def search(self, query: str, visitor_id: str | None = None, k: int = 5) -> list[dict]:
        """Turns from earlier conversations matching `query` (FTS5), optionally for one visitor."""
        if not self.fts or not query.strip():
            return []
        terms = re.findall(r"[\w]{2,}", query.lower())
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms[:12])
        sql = ("SELECT f.conversation_id, f.role, f.text, c.started FROM turns_fts f JOIN conversations c ON c.id=f.conversation_id "
               "WHERE turns_fts MATCH ?")
        args: list = [match]
        if visitor_id:
            sql += " AND c.visitor_id=?"; args.append(visitor_id)
        sql += " ORDER BY rank LIMIT ?"; args.append(k)
        with self.lock:
            rows = self.db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- prompt
    @staticmethod
    def recall_block(records: list[dict]) -> str:
        """System-prompt section for a returning customer. Facts only; the model decides how to use them."""
        if not records:
            return ""
        lines = ["## Returning customer: earlier conversations with this person (newest first)",
                 "Use this naturally: greet them as someone you have spoken to, do not re-ask what is already known, "
                 "confirm it instead (\"Creta ki AC wali baat thi na?\"). Never read this list out."]
        for r in records:
            when = time.strftime("%d %b", time.localtime(r["started"]))
            facts = [f"{k}: {r[k]}" for k in ("name", "car", "problem", "area", "phone") if r.get(k)]
            facts.append(f"intent: {r.get('intent')}")
            if r.get("next_step"):
                facts.append(f"agreed next step: {r['next_step']}")
            lines.append(f"- {when}: " + "; ".join(facts) + f". Summary: {r.get('summary')}")
            # last two things the customer said: the most useful context for "where were we"
            tail = [t["text"] for t in (r.get("transcript") or []) if t.get("role") == "user"][-2:]
            if tail:
                lines.append("  Customer said: " + " | ".join(x[:120] for x in tail))
        return "\n".join(lines)

    # ---------------------------------------------------------------- audio
    @staticmethod
    def audio_dir_for(conv_id: str) -> Path:
        d = AUDIO_DIR / conv_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def merge_audio(audio_dir: Path, files: list[str], out_name: str = "call.m4a") -> str | None:
        """Best effort: one listenable file of the whole call, in order (needs ffmpeg on PATH)."""
        ff = shutil.which("ffmpeg")
        if not ff or not files:
            return None
        wavs = []
        try:
            for i, f in enumerate(files):
                src = audio_dir / f
                if not src.is_file():
                    continue
                w = audio_dir / f"_{i:03d}.wav"
                subprocess.run([ff, "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(w)], check=True, timeout=60)
                wavs.append(w)
            if not wavs:
                return None
            lst = audio_dir / "_concat.txt"
            lst.write_text("".join(f"file '{w.name}'\n" for w in wavs), encoding="utf-8")
            out = audio_dir / out_name
            subprocess.run([ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "aac", "-b:a", "48k", str(out)],
                           check=True, timeout=120, cwd=str(audio_dir))
            return out.name
        except (subprocess.SubprocessError, OSError):
            return None
        finally:
            for w in wavs:
                try: w.unlink()
                except OSError: pass
            try: (audio_dir / "_concat.txt").unlink()
            except OSError: pass
