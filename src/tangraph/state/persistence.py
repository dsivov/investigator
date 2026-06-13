"""Investigation-state persistence behind a small repository interface.

Phase 1 keeps the existing ``coffy.nosql`` JSON-file backing as-is. The
abstraction matters more than the implementation: by routing every read
and write through ``InvestigationStateRepo`` the call sites no longer
encode the specific query DSL (``.where(...).eq(...).first()``), so a
Phase 3 swap to PostgreSQL / SQLite / Redis only touches this file.

Known behaviour preserved deliberately (FIXME for Phase 2):
  * ``clear_on_start=True`` wipes the DB at construction. The original
    code did this at module import (``graph_db_state.clear()``), which
    discards every prior investigation on every server restart.
"""

from __future__ import annotations

import os
from typing import Any

from coffy.nosql import db


class InvestigationStateRepo:
    """Thin wrapper around the coffy NoSQL DB used to store per-session
    investigation state (nodes, edges, dirty markers, run counts)."""

    def __init__(
        self,
        path: str = "/tmp/tan_server_data/investigation_state_graph_db.json",
        *,
        collection_name: str = "invetigation_state_graph_db",
        clear_on_start: bool = True,
    ) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._db = db(collection_name, path=path)
        if clear_on_start:
            # FIXME(Phase 2): wipes all sessions on every server restart.
            self._db.clear()

    def find(self, session_id: str) -> dict | None:
        """Return the session's stored record or ``None`` if not present."""
        return self._db.where("session_id").eq(session_id).first()

    def add(self, record: dict) -> None:
        """Insert a new session record (record must contain ``session_id``)."""
        self._db.add(record)

    def update(self, session_id: str, fields: dict) -> None:
        """Patch the named fields on a session record."""
        self._db.where("session_id").eq(session_id).update(fields)

    def get_field(self, session_id: str, field: str, default: Any = None) -> Any:
        """Fetch ``record[field]`` for the session, or ``default`` if either
        the record or the field is missing.
        """
        record = self.find(session_id)
        if record is None:
            return default
        return record.get(field, default)
