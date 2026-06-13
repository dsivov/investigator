"""In-memory store for per-session DSPy Expert objects.

Replaces the module-level ``experts = {}`` dict that lived at the top of
tangraph_server.py. The store has no eviction yet — Phase 3 candidate: add
TTL or LRU + a background sweeper so long-running servers don't leak.
"""

from __future__ import annotations

from typing import Any


class SessionStore:
    """Maps a session/investigation id to a single Expert instance."""

    def __init__(self) -> None:
        self._experts: dict[str, Any] = {}

    def get(self, session_id: str) -> Any | None:
        return self._experts.get(session_id)

    def set(self, session_id: str, expert: Any) -> None:
        self._experts[session_id] = expert

    def __contains__(self, session_id: str) -> bool:
        return session_id in self._experts

    def clear(self) -> None:
        self._experts.clear()
