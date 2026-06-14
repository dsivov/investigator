"""Single per-investigation state container.

Today the pipeline juggles three overlapping representations of the same
fields (see DATA_MODEL.md): a request-scoped ``working_state``
dict mutated by side-effect, a read-only ``current_investigation_state``
snapshot, and the persisted record reached through ``InvestigationStateRepo``.
Same logical data, copied between them by hand.

``InvestigationState`` collapses those into one object that:
  * owns the fields (nodes, edges, chunks, dirty markers, representatives,
    run count),
  * knows how to ``load`` / ``save`` itself through the repo, and
  * offers an O(1) ``canonical_id -> EntityRecord`` index so pipeline steps stop
    linear-scanning the node list.

``nodes`` holds :class:`EntityRecord` objects and ``edges`` holds
:class:`EdgeRecord` objects in memory; persisted records are plain dicts, so
``load`` converts dict→record (``from_dict``) and ``save`` converts record→dict
(``to_dict``). Both round-trip losslessly, so the persisted JSON is unchanged.

This module is intentionally free of heavy deps (no dspy / embeddings) so it
can be imported and unit-tested in isolation.

Persistence note: only the cross-request fields are written back
(``nodes``, ``edges``, ``representative_identifiers``, ``dirty_node_names``,
``runs_number``, plus the ``investigation_query`` / ``investigation_subject``
metadata the original record carried). ``chunks`` and ``junction_*`` are
per-request working data and were never persisted by the original code — that
is preserved here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from investigator.state.records import EdgeRecord, EntityRecord


@dataclass
class InvestigationState:
    session_id: str
    nodes: list[EntityRecord] = field(default_factory=list)
    edges: list[EdgeRecord] = field(default_factory=list)
    chunks: list[dict] = field(default_factory=list)
    representative_identifiers: list[dict] = field(default_factory=list)
    dirty_node_names: list[list[str]] = field(default_factory=list)
    runs_number: int = 0
    investigation_query: str | None = None
    investigation_subject: str | None = None
    _index: dict[str, EntityRecord] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.reindex()

    # --- index -----------------------------------------------------------

    def reindex(self) -> None:
        """Rebuild the ``canonical_id -> EntityRecord`` index from ``nodes``.

        On duplicate ids the last node wins, matching how a dict index behaves
        and how the pipeline's later-arriving records supersede earlier ones.
        """
        self._index = {}
        for node in self.nodes:
            cid = node.canonical_id
            if cid:
                self._index[cid] = node

    def node(self, cid: str | None) -> EntityRecord | None:
        """O(1) lookup of a node by canonical id (case-insensitive)."""
        if not cid:
            return None
        return self._index.get(str(cid).upper())

    def add_nodes(self, nodes: list[EntityRecord]) -> None:
        """Append nodes and keep the index in sync."""
        for n in nodes:
            self.nodes.append(n)
            cid = n.canonical_id
            if cid:
                self._index[cid] = n

    # --- persistence -----------------------------------------------------

    @classmethod
    def load(cls, repo, session_id: str) -> InvestigationState:
        """Build state from the persisted record, or a fresh one if absent."""
        record = repo.find(session_id)
        if not record:
            return cls(session_id=session_id)
        runs = record.get("runs_number", 0)
        if not isinstance(runs, int):
            runs = 0
        return cls(
            session_id=session_id,
            nodes=[EntityRecord.from_dict(n) for n in (record.get("nodes") or [])],
            edges=[EdgeRecord.from_dict(e) for e in (record.get("edges") or [])],
            representative_identifiers=record.get("representative_identifiers") or [],
            dirty_node_names=record.get("dirty_node_names") or [],
            runs_number=runs,
            investigation_query=record.get("investigation_query"),
            investigation_subject=record.get("investigation_subject"),
        )

    def save(self, repo) -> None:
        """Upsert the cross-request fields to the repo (insert or patch)."""
        fields = {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "representative_identifiers": self.representative_identifiers,
            "dirty_node_names": self.dirty_node_names,
            "runs_number": self.runs_number,
            "investigation_query": self.investigation_query,
            "investigation_subject": self.investigation_subject,
        }
        if repo.find(self.session_id) is None:
            repo.add({"session_id": self.session_id, **fields})
        else:
            repo.update(self.session_id, fields)
