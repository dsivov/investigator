"""Analytics stack: the in-code cumulative knowledge graph.

Investigation graphs accumulate into one persistent LightRAG KG via
:class:`CumulativeKG`, which merges in-process (no FastAPI server) and runs a
conservative cross-investigation :mod:`canonicalizer` pre-pass. This replaced
the old server stack (LightRAG FastAPI server + HTTP client + ingest worker +
reranker), which duplicated LightRAG's own graph construction.
"""

from investigator.analytics.canonicalizer import CanonicalRegistry
from investigator.analytics.cumulative_kg import CumulativeKG

__all__ = ["CumulativeKG", "CanonicalRegistry"]
