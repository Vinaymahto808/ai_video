"""CRM outbox: every finished conversation becomes a row here, and a worker pushes it to the CRM with retries.

Why an outbox: the customer must never wait for the CRM, the CRM may be down or rate-limited, and a push must
happen exactly once per conversation. The row lives in the same SQLite file as the conversations
(human_bot/data/conversations.sqlite), keyed by conversation id, so a restart continues where it stopped.

    sink = Bitrix24Sink(url)            # or any object with push(record) -> dict (raises on failure)
    outbox = Outbox(db_path, sink)      # worker thread starts when a sink is given
    outbox.enqueue(record)              # idempotent; called from server/app.py after /end

Without a sink (no BITRIX24_WEBHOOK_URL yet) rows still queue, so nothing is lost: they flush the day the
URL is configured.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Protocol

MAX_ATTEMPTS = 20
BACKOFF_S = [30, 60, 120, 300, 600, 1200, 1800, 3600]  # then 3600 until MAX_ATTEMPTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS crm_outbox (
  conversation_id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  created REAL NOT NULL, next_at REAL NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, done_at REAL, result TEXT
);
CREATE INDEX IF NOT EXISTS outbox_due ON crm_outbox(done_at, next_at);
"""


class Sink(Protocol):
    name: str
    def push(self, record: dict) -> dict: ...  # returns {"id": ..., ...}; raises on failure


class Outbox:
    def __init__(self, db_path: Path | str, sink: Sink | None = None, poll_s: float = 5.0, start_worker: bool = True) -> None:
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.lock = threading.Lock()
        self.sink = sink
        self.poll_s = poll_s
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        if sink and start_worker:
            self.thread = threading.Thread(target=self._loop, daemon=True, name="crm-outbox")
            self.thread.start()

    # ---------------------------------------------------------------- producer
    def enqueue(self, record: dict) -> bool:
        """Queue a conversation record once; a second call for the same id refreshes the payload only if
        the row has not been delivered yet (a longer transcript after a late save)."""
        cid = record.get("id")
        if not cid:
            return False
        payload = json.dumps(record, ensure_ascii=False, default=str)
        now = time.time()
        with self.lock:
            cur = self.db.execute("SELECT done_at FROM crm_outbox WHERE conversation_id=?", (cid,)).fetchone()
            if cur is None:
                self.db.execute("INSERT INTO crm_outbox(conversation_id, payload, created, next_at) VALUES(?,?,?,?)", (cid, payload, now, now))
            elif cur["done_at"] is None:
                self.db.execute("UPDATE crm_outbox SET payload=? WHERE conversation_id=?", (payload, cid))
            else:
                self.db.commit()
                return False
            self.db.commit()
        return True

    # ---------------------------------------------------------------- worker
    def due(self, limit: int = 10) -> list[dict]:
        now = time.time()
        with self.lock:
            rows = self.db.execute("SELECT * FROM crm_outbox WHERE done_at IS NULL AND next_at<=? AND attempts<? ORDER BY next_at LIMIT ?",
                                   (now, MAX_ATTEMPTS, limit)).fetchall()
        return [dict(r) for r in rows]

    def process_once(self) -> int:
        """Push every due row once. Returns the number delivered. Safe to call from tests without the thread."""
        if not self.sink:
            return 0
        delivered = 0
        for row in self.due():
            record = json.loads(row["payload"])
            try:
                result = self.sink.push(record)
                with self.lock:
                    self.db.execute("UPDATE crm_outbox SET done_at=?, result=?, attempts=attempts+1, last_error=NULL WHERE conversation_id=?",
                                    (time.time(), json.dumps(result, default=str)[:2000], row["conversation_id"]))
                    self.db.commit()
                delivered += 1
            except Exception as ex:  # noqa: BLE001
                attempts = row["attempts"] + 1
                wait = BACKOFF_S[min(attempts - 1, len(BACKOFF_S) - 1)]
                err = f"{type(ex).__name__}: {str(ex)[:300]}"
                with self.lock:
                    self.db.execute("UPDATE crm_outbox SET attempts=?, next_at=?, last_error=? WHERE conversation_id=?",
                                    (attempts, time.time() + wait, err, row["conversation_id"]))
                    self.db.commit()
                sys.stderr.write(f"[crm] {self.sink.name} push failed for {row['conversation_id']} (attempt {attempts}): {err}\n")
        return delivered

    def _loop(self) -> None:
        while not self.stop.wait(self.poll_s):
            try:
                self.process_once()
            except Exception as ex:  # noqa: BLE001
                sys.stderr.write(f"[crm] outbox worker error: {type(ex).__name__}: {ex}\n")

    def status(self) -> dict[str, Any]:
        with self.lock:
            pending = self.db.execute("SELECT COUNT(*) n FROM crm_outbox WHERE done_at IS NULL AND attempts<?", (MAX_ATTEMPTS,)).fetchone()["n"]
            failed = self.db.execute("SELECT COUNT(*) n FROM crm_outbox WHERE done_at IS NULL AND attempts>=?", (MAX_ATTEMPTS,)).fetchone()["n"]
            done = self.db.execute("SELECT COUNT(*) n FROM crm_outbox WHERE done_at IS NOT NULL").fetchone()["n"]
            last = self.db.execute("SELECT last_error FROM crm_outbox WHERE last_error IS NOT NULL ORDER BY next_at DESC LIMIT 1").fetchone()
        return {"sink": getattr(self.sink, "name", None), "pending": pending, "failed": failed, "delivered": done, "last_error": last["last_error"] if last else None}

    def close(self) -> None:
        self.stop.set()
        self.db.close()
