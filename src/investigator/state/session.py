"""In-memory store for per-session DSPy Expert objects.

Replaces the module-level ``experts = {}`` dict that lived at the top of
investigator_server.py. The store has no eviction yet — Phase 3 candidate: add
TTL or LRU + a background sweeper so long-running servers don't leak.
"""

from __future__ import annotations

import threading
from typing import Any


class SessionStore:
    """Maps a session/investigation id to a single Expert instance.

    M2 concurrency: guarded by a lock so concurrent Flask requests (each on its
    own event loop/thread under Flask[async]) can't race the shared dict on the
    get-then-set path.
    """

    def __init__(self) -> None:
        self._experts: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Any | None:
        with self._lock:
            return self._experts.get(session_id)

    def set(self, session_id: str, expert: Any) -> None:
        with self._lock:
            self._experts[session_id] = expert

    def __contains__(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._experts

    def clear(self) -> None:
        with self._lock:
            self._experts.clear()
