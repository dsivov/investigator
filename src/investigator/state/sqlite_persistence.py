"""Durable SQLite-backed investigation-state persistence.

Drop-in replacement for the coffy JSON-file :class:`InvestigationStateRepo`,
implementing the same 4-method interface (``find`` / ``add`` / ``update`` /
``get_field``). Two things it fixes over the coffy backing:

* **Durability (M1).** State lives in a real SQLite file **outside** the code
  tree (``~/.local/share/investigator/state.sqlite3`` by default, overridable
  with ``INVESTIGATOR_STATE_DB``) and — crucially — is **not wiped on startup**.
  A server restart no longer discards every session. Set
  ``INVESTIGATOR_CLEAR_ON_START=1`` to opt back into the old wipe (e.g. tests).
* **Concurrency.** Writes are serialised under a lock and the connection runs in
  WAL mode, so concurrent Flask requests can't corrupt the store the way the
  rewrite-the-whole-JSON-file coffy backing could.

Records are session-id-keyed dicts (nodes, edges, dirty markers, run counts,
…). They are already JSON-serialisable (the coffy store held them as JSON), so
each record is stored as a single JSON blob keyed by ``session_id`` — preserving
the exact ``find``/``add``/``update`` dict semantics of the original repo.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from investigator.logging import get_logger

log = get_logger()


def default_state_db_path() -> str:
    """The durable SQLite path, outside the code tree so it survives restarts.

    Resolution order: ``INVESTIGATOR_STATE_DB`` → ``$XDG_DATA_HOME`` →
    ``~/.local/share`` — the same convention as the cumulative-KG store.
    """
    env = os.environ.get("INVESTIGATOR_STATE_DB")
    if env:
        return str(Path(env).expanduser())
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return str(Path(base) / "investigator" / "state.sqlite3")


class SqliteInvestigationStateRepo:
    """SQLite-backed per-session investigation state. Same interface as the
    coffy :class:`InvestigationStateRepo`, but durable and concurrency-safe."""

    def __init__(
        self,
        path: str | None = None,
        *,
        clear_on_start: bool | None = None,
        migrate_from: str | None = None,
    ) -> None:
        self.path = path or default_state_db_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # No-wipe by default (the M1 fix). Env var can force the old behaviour.
        if clear_on_start is None:
            clear_on_start = os.environ.get("INVESTIGATOR_CLEAR_ON_START", "").lower() in ("1", "true", "yes")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            "session_id TEXT PRIMARY KEY, record TEXT NOT NULL)"
        )
        self._conn.commit()
        if clear_on_start:
            with self._lock:
                self._conn.execute("DELETE FROM sessions")
                self._conn.commit()
            log.info("SqliteInvestigationStateRepo: cleared on start (INVESTIGATOR_CLEAR_ON_START).")
        else:
            self._maybe_migrate(migrate_from)
        log.info(f"SqliteInvestigationStateRepo ready at {self.path} ({self._count()} session(s)).")

    # --- repo interface -----------------------------------------------------
    def find(self, session_id: str) -> dict | None:
        """Return the session's stored record or ``None`` if not present."""
        cur = self._conn.execute("SELECT record FROM sessions WHERE session_id=?", (session_id,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def add(self, record: dict) -> None:
        """Insert (or replace) a session record; must contain ``session_id``."""
        sid = record.get("session_id")
        if not sid:
            raise ValueError("record must contain 'session_id'")
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO sessions(session_id, record) VALUES(?, ?)",
                (sid, json.dumps(record)),
            )
            self._conn.commit()

    def update(self, session_id: str, fields: dict) -> None:
        """Patch the named fields on a session record. No-op if the record is
        absent (matches the coffy ``.where().eq().update()`` semantics)."""
        with self._lock:
            cur = self._conn.execute("SELECT record FROM sessions WHERE session_id=?", (session_id,))
            row = cur.fetchone()
            if row is None:
                return
            record = json.loads(row[0])
            record.update(fields)
            self._conn.execute(
                "UPDATE sessions SET record=? WHERE session_id=?",
                (json.dumps(record), session_id),
            )
            self._conn.commit()

    def get_field(self, session_id: str, field: str, default: Any = None) -> Any:
        """Fetch ``record[field]`` for the session, or ``default`` if missing."""
        record = self.find(session_id)
        if record is None:
            return default
        return record.get(field, default)

    # --- helpers ------------------------------------------------------------
    def _count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    def _maybe_migrate(self, migrate_from: str | None) -> None:
        """Best-effort one-time import of sessions from a legacy coffy JSON file.

        Only runs when this SQLite store is empty and a legacy file with records
        exists. The legacy default store lives under /tmp and is typically
        already empty (it was wiped on every restart), so this is usually a
        no-op — but it makes the coffy→SQLite transition lossless when it isn't.
        """
        if self._count() > 0:
            return
        legacy = migrate_from or "/tmp/tan_server_data/investigation_state_graph_db.json"
        try:
            p = Path(legacy)
            if not p.exists() or p.stat().st_size <= 2:
                return
            raw = json.loads(p.read_text() or "null")
            # coffy stores either a list of records or {collection: [records]}.
            records = raw if isinstance(raw, list) else next(
                (v for v in raw.values() if isinstance(v, list)), []
            ) if isinstance(raw, dict) else []
            migrated = 0
            for rec in records:
                if isinstance(rec, dict) and rec.get("session_id"):
                    self.add(rec)
                    migrated += 1
            if migrated:
                log.info(f"Migrated {migrated} session(s) from legacy coffy store {legacy}.")
        except Exception as e:  # noqa: BLE001 -- migration must never block startup
            log.warning(f"Legacy state migration skipped: {type(e).__name__}: {e}")
