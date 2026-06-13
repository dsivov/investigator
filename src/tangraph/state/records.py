"""Typed storage records: `EntityRecord` (and later `EdgeRecord`).

These are the in-memory form of the entity dicts that flow through the
pipeline. They are *faithful projections* of the dspy signature output models
(`tangraph.llm.models.Entity` etc.) — see DATA_MODEL.md "Signatures are the
data contract": the storage record is named `EntityRecord` precisely so it does
**not** shadow the signature `Entity`.

Design constraints:
  * **Round-trip fidelity** — `EntityRecord.from_dict(d).to_dict() == d` for the
    entity dicts the pipeline actually produces, so wiring this in (step 4) is
    behaviour-preserving across the dspy / semhash / coffy / HTTP boundaries.
  * **Open `data` blob** — `data` mirrors `models.Entity.model_dump()` (the
    extraction contract) and then accretes dedup-added keys; it stays a dict.
  * **Accreting optional fields** — `representative_identifier`, `leaf`,
    `evidence`, ... are absent on a freshly-extracted entity and added by later
    steps. They default to ``None`` (the "absent" sentinel, distinct from a
    falsy ``False`` / ``0.0`` / ``""``) and `to_dict` omits them when absent, so
    a minimal record round-trips to the same minimal dict.
  * **`extra`** — any key we don't model is preserved verbatim (forward-compat
    for fields a record may carry that the envelope doesn't name).

No pipeline call sites use this yet (step 3 is additive, mirroring step 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields

# Core fields every entity dict carries from extraction onward — always emitted.
_CORE = ("identifier", "unique_identifier", "type", "data")
# Fields added by later pipeline stages — emitted only when set (not None).
_OPTIONAL = (
    "source",
    "chunk_uuid",
    "representative_identifier",
    "labels",
    "most_significant_labels",
    "triangulated",
    "hypothesis",
    "leaf",
    "prob",
    "evidence",
    "evidence_count",
    "self_evidence",
    "runs",
)


@dataclass
class EntityRecord:
    identifier: str = ""               # working name (UPPER) from extraction
    unique_identifier: str = ""        # stable per-record UUID (provenance / edge endpoints)
    type: str = "entity"
    data: dict = field(default_factory=dict)   # == models.Entity.model_dump() + dedup-added keys
    # --- accreting optionals (None = absent; see module docstring) ---
    source: str | None = None
    chunk_uuid: str | None = None
    representative_identifier: str | None = None   # canonical name after dedup
    labels: list | None = None
    most_significant_labels: list | None = None
    triangulated: bool | None = None
    hypothesis: bool | None = None     # entity-level rollup (distinct from each evidence sub-record's own `hypothesis` bool)
    leaf: bool | None = None
    prob: float | None = None
    evidence: list | None = None       # evidence sub-records (signature Evidence + derived fields)
    evidence_count: int | None = None
    self_evidence: dict | None = None
    # Per-session run labels that surfaced or re-attested this entity. None =
    # legacy single-run session (the response shape stays unchanged). When a
    # POST carries a `run` field the orchestrator stamps it here at extraction
    # and the cross-stage merge unions across alias matches -- so this list
    # is the per-entity provenance backbone for cross-run analytics. Named
    # `runs` (not `events`) to avoid collision with first-class graph nodes
    # of type="event" that the Event NER may introduce.
    runs: list | None = None
    # --- anything we don't model, preserved verbatim ---
    extra: dict = field(default_factory=dict)

    @property
    def canonical_id(self) -> str:
        """representative_identifier or identifier, UPPER — the single graph/index/edge key.

        Matches ``tangraph.state.ids.canonical_id`` (the dict-level helper).
        """
        return (self.representative_identifier or self.identifier or "").upper()

    @classmethod
    def from_dict(cls, d: dict) -> EntityRecord:
        known = {f.name for f in fields(cls)} - {"extra"}
        kwargs = {k: v for k, v in d.items() if k in known}
        extra = {k: v for k, v in d.items() if k not in known}
        return cls(**kwargs, extra=extra)

    @classmethod
    def from_extraction(cls, model, chunk_id: str) -> EntityRecord:
        """Build from a dspy ``models.Entity`` (the extraction signature output).

        Duck-typed on ``model`` (``.name``, ``.search_url``, ``.search_source``,
        ``.model_dump()``) to keep this module free of the llm/dspy import.
        The chunk-context bits (``relevant_entities``) are populated by the
        caller, which holds the per-chunk entity list.
        """
        data = model.model_dump()
        if model.search_url:
            source = model.search_url
        elif model.search_source:
            source = model.search_source
        elif data.get("search_url"):
            source = data.get("search_url")
        else:
            source = data.get("search_source", "unknown")
        import uuid

        return cls(
            identifier=model.name.upper(),
            unique_identifier=str(uuid.uuid4()),
            type="entity",
            data=data,
            source=source,
            chunk_uuid=chunk_id,
        )

    def to_dict(self) -> dict:
        out: dict = {name: getattr(self, name) for name in _CORE}
        for name in _OPTIONAL:
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        out.update(self.extra)
        return out


# Edge fields in emit order. All optional (None = absent) for round-trip
# fidelity — the same None-sentinel + extra-bag scheme as EntityRecord — since
# a registered edge and a merged saved edge carry slightly different key sets.
_EDGE_FIELDS = (
    "unique_identifier",        # the edge's own UUID
    "src_identifier",           # canonical_id of source entity (== graph node key)
    "dst_identifier",           # canonical_id of dest entity
    "src_unique_identifier",    # source EntityRecord.unique_identifier (provenance join)
    "dst_unique_identifier",    # dest EntityRecord.unique_identifier
    "type",
    "relations",                # JSON string (resolve_edge_endpoints json.dumps)
    "attributes",
    "metadata",
    "source",
    "search_url",               # PR4-a: URL provenance for the investigation report
    "runs",                     # per-run provenance; same semantics as EntityRecord.runs
)


@dataclass
class EdgeRecord:
    """Typed in-memory form of an enriched edge dict (post resolve_edge_endpoints).

    Same projection/round-trip contract as :class:`EntityRecord` — a faithful
    view of the `GraphEdgesEnrichment` `Edge` output after endpoint resolution.
    `source_node`/`target_node` are transient (resolved to src/dst in
    resolve_edge_endpoints) and so are not modelled; if present they land in
    `extra` and round-trip verbatim.
    """

    unique_identifier: str | None = None
    src_identifier: str | None = None
    dst_identifier: str | None = None
    src_unique_identifier: str | None = None
    dst_unique_identifier: str | None = None
    type: str | None = None
    relations: str | None = None
    attributes: dict | None = None
    metadata: dict | None = None
    source: str | None = None
    search_url: str | None = None       # PR4-a: URL provenance for the investigation report
    runs: list | None = None            # per-run provenance; None = legacy single-run flow
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> EdgeRecord:
        known = {f.name for f in fields(cls)} - {"extra"}
        kwargs = {k: v for k, v in d.items() if k in known}
        extra = {k: v for k, v in d.items() if k not in known}
        return cls(**kwargs, extra=extra)

    def to_dict(self) -> dict:
        out: dict = {}
        for name in _EDGE_FIELDS:
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        out.update(self.extra)
        return out
