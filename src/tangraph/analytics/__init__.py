"""Optional analytics stack: LightRAG server, reranker, RAGClient, worker.

These modules are conditionally launched (``--analytic_engine_enabled``).
Phase 1 only relocated them from repo root; their internals retain
research-grade style. Cleanup deferred to Phase 2 (see per-file ruff
ignores in pyproject.toml).
"""

from tangraph.analytics.client import RAGClient
from tangraph.analytics.worker import AnalyticsWorker

__all__ = ["AnalyticsWorker", "RAGClient"]
