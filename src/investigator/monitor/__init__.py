"""Standing-monitor / CEP layer.

Turns Investigator from request-driven (you ask -> it builds a graph) into a
standing monitor: a scheduled job fetches fresh news, extracts events + actors,
intersects them with the cumulative knowledge graph, and propagates *impact* onto
connected nodes (direct + hidden/brokered) -- surfacing what moved in your graph.

Phase 1 (this package): a scheduled impact digest. See
``docs/cep-monitoring-discussion.html`` for the design.
"""
from investigator.monitor.watchlist import Watchlist, load_watchlist

__all__ = ["Watchlist", "load_watchlist"]
