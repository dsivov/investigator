"""Analytics stack: the in-code cumulative knowledge graph.

Investigation graphs accumulate into one persistent LightRAG KG via
:class:`CumulativeKG`, which merges in-process (no FastAPI server) and runs a
conservative cross-investigation :mod:`canonicalizer` pre-pass. This replaced
the old server stack (LightRAG FastAPI server + HTTP client + ingest worker +
reranker), which duplicated LightRAG's own graph construction.
"""

import os
from pathlib import Path

from investigator.analytics.canonicalizer import CanonicalRegistry
from investigator.analytics.cumulative_kg import CumulativeKG

__all__ = ["CumulativeKG", "CanonicalRegistry", "kg_store_dir"]


def kg_store_dir() -> Path:
    """The single, persistent cumulative-KG store directory, shared by the
    engine (accumulation) and the UI (Knowledge Base queries).

    Resolution order:
      1. ``INVESTIGATOR_KG_STORE`` env var (absolute path recommended), else
      2. ``$XDG_DATA_HOME/investigator/kg``, else
      3. ``~/.local/share/investigator/kg``.

    Deliberately OUTSIDE the code tree so the knowledge base persists
    independently of where the engine/UI is launched from.
    """
    env = os.environ.get("INVESTIGATOR_KG_STORE")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "investigator" / "kg"
