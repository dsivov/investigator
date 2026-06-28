"""In-code cumulative knowledge graph: accumulate every investigation's graph
into one persistent LightRAG KG, in-process (no FastAPI server).

This replaces the old server path (``RAGClient`` -> FastAPI -> LightRAG), which
(a) was hard to manage as a background process and (b) re-ran LightRAG's own LLM
graph extraction on text and overwrote overlapping entities by name. Here we
feed our already-built investigation graph straight to LightRAG's
``merge_nodes_and_edges`` (the proper merge path, NOT ``insert_custom_kg`` which
overwrites): it merges by EXACT entity name -- unioning ``source_id`` /
``file_path``, voting ``entity_type``, concatenating + LLM-summarizing
descriptions, and merging edges.

Because LightRAG merges by exact name, divergent canonicals across
investigations would still duplicate. So every graph first goes through the
conservative cross-investigation :class:`CanonicalRegistry` pre-pass (see
:mod:`investigator.analytics.canonicalizer`), which rewrites entity names to
their stable global canonical before the merge.

The LLM and embedding functions are injectable. Defaults are offline-friendly
(local WordLlama embeddings + a concatenation stub LLM) so the module is usable
and testable without network/credentials; production callers should inject a
real ``llm_model_func`` for description summarization.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import Future
from dataclasses import asdict
from pathlib import Path

import numpy as np

from lightrag import LightRAG, QueryParam
from lightrag.kg.shared_storage import (
    get_namespace_data,
    get_namespace_lock,
    initialize_pipeline_status,
)
from lightrag.operate import merge_nodes_and_edges
from lightrag.utils import EmbeddingFunc

from investigator.analytics.canonicalizer import CanonicalRegistry, resolve_graph_entities
from investigator.analytics.structured_store import StructuredStore, _dates
from investigator.graph.dedup import _wl
from investigator.logging import get_logger

_log = get_logger()
_EMPTY = {"", "not found", "unknown", "n/a"}


async def _concat_llm(prompt, system_prompt=None, history_messages=None, **kwargs) -> str:
    """Offline default LLM: no real summarization. Production should inject a
    real ``llm_model_func`` so merged descriptions get summarized."""
    return "summary not generated (no llm_model_func injected)"


def _default_embedding_func() -> EmbeddingFunc:
    dim = int(_wl.embed(["x"]).shape[1])

    async def embed(texts: list[str]):
        return np.asarray(_wl.embed(list(texts)), dtype=np.float32)

    return EmbeddingFunc(embedding_dim=dim, max_token_size=8192, func=embed)


def _coerce_type(t) -> str:
    # Our within-investigation merge can yield a list-typed data.type
    # (e.g. ['GPE','ORG']); LightRAG needs a single hashable string.
    if isinstance(t, list):
        return str(t[0]) if t else "UNKNOWN"
    return str(t) if t else "UNKNOWN"


def _clean(v) -> str:
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    s = str(v or "").strip()
    return "" if s.lower() in _EMPTY else s


def _entity_description(node: dict) -> str:
    """Substantive description text stored in the KG for this entity, so the
    knowledge base can actually answer "who/what is X". Pulls the role/position,
    location, notable aliases, and the entity's own evidence sentences (the
    claims made about it) -- not just the name + type, which left nodes empty."""
    d = node.get("data") or {}
    ident = node["identifier"]
    etype = _coerce_type(d.get("type"))
    parts = [f"{ident} is a {etype}." if etype and etype != "UNKNOWN" else ident]
    role = _clean(d.get("position"))
    if role:
        parts.append(f"Role/position: {role}.")
    loc = _clean(d.get("location"))
    if loc:
        parts.append(f"Location: {loc}.")
    # High-signal identifiers (no chunks => fold into the description or lose them).
    for key, label in (("address", "Address"), ("email", "Email"),
                       ("phone_number", "Phone"),
                       ("financial_restrictions", "Financial restrictions")):
        v = _clean(d.get(key))
        if v:
            parts.append(f"{label}: {v}.")
    # Notable aliases / labels (skip ones equal to the identifier).
    labels = node.get("most_significant_labels") or node.get("labels") or []
    aliases = []
    for lab in labels:
        lab = _clean(lab)
        if lab and lab.upper() != ident.upper() and lab not in aliases:
            aliases.append(lab)
    if aliases:
        parts.append("Also referred to as: " + ", ".join(aliases[:5]) + ".")
    # Dated timeline (so temporal / "what happened" queries retrieve it, and the
    # synthesised answer can cite dates -- this signal was previously embed-blind).
    tl = []
    for te in (d.get("timeline_events") or []):
        if not isinstance(te, dict):
            continue
        ev = _clean(te.get("event"))
        date = _clean(te.get("date"))
        if ev:
            tl.append(f"{date}: {ev}" if date else ev)
        if len(tl) >= 6:
            break
    if tl:
        parts.append("Timeline: " + "; ".join(tl) + ".")
    # Active period (so "who was active in 2024" style queries retrieve it).
    span = sorted(d for te in (d.get("timeline_events") or []) if isinstance(te, dict)
                  for d in _dates(te.get("date")))
    if span:
        parts.append(f"Active period: {span[0]} to {span[-1]}."
                     if span[0] != span[-1] else f"Active: {span[0]}.")
    # The entity's own evidence -- the actual claims about it (the substance).
    seen = set()
    claims = []
    for ev in (node.get("evidence") or []):
        if not isinstance(ev, dict):
            continue
        txt = _clean(ev.get("reasoning"))
        # strip the consolidator routing prefix if present
        if txt.startswith("Evidence through affiliations"):
            nl = txt.find("\n")
            txt = txt[nl + 1:].strip() if nl != -1 else ""
        if txt and txt not in seen:
            seen.add(txt)
            claims.append(txt[:300])
        if len(claims) >= 5:
            break
    if claims:
        parts.append("Reported: " + " ".join(claims))
    return " ".join(parts)


def _event_dates(event_node: dict) -> list[str]:
    """ISO dates carried by an event node (data.date may be str or list)."""
    return _dates((event_node.get("data") or {}).get("date"))


def _edge_urls(edge: dict) -> list[str]:
    """Every source URL an edge cites (single + causal-edge list), http only."""
    attrs = edge.get("attributes") or {}
    cands = [edge.get("search_url"), edge.get("source"), attrs.get("source_url")]
    cands += list(attrs.get("source_urls") or [])
    return [u for u in cands if isinstance(u, str) and u.startswith("http")]


def _edge_relation(edge: dict) -> tuple[str, str]:
    r = edge.get("relations")
    if isinstance(r, str):
        try:
            r = json.loads(r)
        except ValueError:
            r = {}
    if not isinstance(r, dict):
        r = {}
    return (r.get("type") or "related").strip(), (r.get("context") or "").strip()


class CumulativeKG:
    """In-process LightRAG knowledge graph that accumulates investigation graphs.

    All LightRAG work runs on ONE dedicated asyncio loop in a daemon thread.
    This is required under Flask, which runs each async request handler in its
    own event loop: LightRAG's ``shared_storage`` caches ``asyncio.Lock`` objects
    in process globals, and those locks bind to the loop that created them, so a
    long-lived instance touched from many request loops would raise "got Future
    attached to a different loop". Pinning everything to one loop also serializes
    writes to the file store (single writer).

    Usage from an async request handler::

        kg = CumulativeKG(working_dir)                  # construct once (app start)
        await kg.merge_graph(final_graph, source_id="inv::a")   # per investigation

    :meth:`merge_graph` / :meth:`get_node` dispatch onto the background loop and
    return an awaitable bound to the caller's loop, so they never block it.
    Initialization is lazy and happens once on the background loop.
    """

    def __init__(
        self,
        working_dir,
        registry_path=None,
        llm_model_func=None,
        embedding_func=None,
        review_path=None,
    ):
        self.working_dir = Path(working_dir)
        self.registry_path = (
            Path(registry_path) if registry_path else self.working_dir / "canonical_registry.json"
        )
        self.registry = CanonicalRegistry(self.registry_path, review_path=review_path)
        self.structured = StructuredStore(self.working_dir / "structured_store.json")
        self._llm = llm_model_func or _concat_llm
        self._embed = embedding_func or _default_embedding_func()
        self._rag: LightRAG | None = None
        self._pipeline_status = None
        self._pipeline_status_lock = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._initialized = False
        self._start_lock = threading.Lock()

    # --- background loop plumbing -----------------------------------------

    def _ensure_loop(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, name="cumulative-kg", daemon=True
            )
            self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro) -> Future:
        """Schedule a coroutine on the background loop from any thread/loop."""
        self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _initialize(self) -> None:
        """Build + open the LightRAG store. Runs once, on the background loop."""
        if self._initialized:
            return
        self.working_dir.mkdir(parents=True, exist_ok=True)
        self._rag = LightRAG(
            working_dir=str(self.working_dir),
            llm_model_func=self._llm,
            embedding_func=self._embed,
        )
        await self._rag.initialize_storages()
        await initialize_pipeline_status()
        self._pipeline_status = await get_namespace_data(
            "pipeline_status", workspace=self._rag.workspace
        )
        self._pipeline_status_lock = get_namespace_lock(
            "pipeline_status", workspace=self._rag.workspace
        )
        self._initialized = True

    def _graph_to_chunk_results(self, final_graph: dict, source_id: str, file_path: str, mapping: dict) -> list:
        """One investigation graph -> a single (maybe_nodes, maybe_edges) chunk
        result, with every entity name rewritten to its global canonical.

        Events are per-investigation and excluded from the cumulative KG. We must
        also drop any edge that touches an event: LightRAG's merge auto-creates a
        node for every edge endpoint, so keeping an entity<->event edge would
        re-introduce the (headline-shaped) event as a phantom entity.
        """
        ts = int(time.time())
        event_ids = {
            n["identifier"]
            for n in final_graph["nodes"]
            if (n.get("node_type") or n.get("type")) == "event"
        }
        maybe_nodes: dict = {}
        for n in final_graph["nodes"]:
            if n["identifier"] in event_ids:
                continue
            name = mapping.get(n["identifier"], n["identifier"])
            maybe_nodes.setdefault(name, []).append(
                {
                    "entity_name": name,
                    "entity_type": _coerce_type((n.get("data") or {}).get("type")),
                    "description": _entity_description(n),
                    "source_id": source_id,
                    "file_path": file_path,
                    "timestamp": ts,
                }
            )
        maybe_edges: dict = {}
        for e in final_graph["edges"]:
            src, dst = e.get("src_identifier"), e.get("dst_identifier")
            if e.get("type") == "evidence" or src in event_ids or dst in event_ids:
                continue
            s = mapping.get(src, src)
            t = mapping.get(dst, dst)
            if not (s and t) or s == t:
                continue
            rtype, ctx = _edge_relation(e)
            role = _clean((e.get("attributes") or {}).get("role"))
            # Embed the role too (the nature of the link), so relationship
            # retrieval/synthesis sees it -- not just the bare relation type.
            desc = " — ".join(p for p in (role, ctx or rtype) if p) or rtype
            maybe_edges.setdefault((s, t), []).append(
                {
                    "src_id": s,
                    "tgt_id": t,
                    "weight": float(e.get("weight") or 1.0),
                    "description": desc,
                    "keywords": ", ".join(p for p in (rtype, role) if p) or rtype,
                    "source_id": source_id,
                    "file_path": file_path,
                    "timestamp": ts,
                }
            )
        return [(maybe_nodes, maybe_edges)]

    async def merge_graph(self, final_graph: dict, source_id: str, file_path: str | None = None,
                          source_dates: dict | None = None) -> dict:
        """Canonicalize then merge one investigation graph into the cumulative KG.

        Dispatches onto the background loop; awaitable on the caller's loop.
        ``source_dates`` (url -> ISO pub date) lets edges carry observed time.
        Returns a small summary dict (node/edge counts merged + registry stats).
        """
        return await asyncio.wrap_future(
            self._submit(self._merge_graph(final_graph, source_id, file_path, source_dates))
        )

    async def get_node(self, name: str) -> dict | None:
        """Look up a merged entity by its canonical name (mainly for tests/debug)."""
        return await asyncio.wrap_future(self._submit(self._get_node(name)))

    async def query(self, text: str, *, mode: str = "hybrid", **kwargs):
        """LLM-synthesized answer over the cumulative KG (LightRAG ``aquery``).

        ``mode`` is one of local / global / hybrid / mix / naive. Note: ``naive``
        and the chunk side of ``mix`` need text chunks, which the in-code merge
        does not populate, so they retrieve only what the graph holds.
        """
        return await asyncio.wrap_future(self._submit(self._query(text, mode, kwargs)))

    async def retrieve(self, text: str, *, mode: str = "hybrid", **kwargs) -> dict:
        """Structured retrieval WITHOUT LLM synthesis (LightRAG ``aquery_data``):
        returns ``{status, data: {entities, relationships, chunks}}``. Keyword
        extraction still uses the LLM."""
        return await asyncio.wrap_future(self._submit(self._retrieve(text, mode, kwargs)))

    async def stats(self) -> dict:
        """Entity/edge counts in the cumulative graph + registry canonical count."""
        return await asyncio.wrap_future(self._submit(self._stats()))

    def structured_entity(self, name: str):
        """Full structured record (all preserved props) for a canonical entity,
        or None. Synchronous -- the sidecar store is plain in-memory data."""
        return self.structured.get_entity(name)

    def entity_timeline(self, name: str) -> list[dict]:
        """An actor's merged chronology (timeline_events + participated events)."""
        return self.structured.entity_timeline(name)

    def structured_edge(self, src: str, dst: str):
        """Full structured record for a canonical edge (incl. observed_dates /
        active_window), or None. Synchronous -- plain in-memory sidecar data."""
        return self.structured.get_edge(src, dst)

    # --- coroutines that run ON the background loop -----------------------

    async def _merge_graph(self, final_graph: dict, source_id: str, file_path: str | None,
                           source_dates: dict | None = None) -> dict:
        await self._initialize()
        file_path = file_path or source_id
        mapping = resolve_graph_entities(final_graph, self.registry, source_id)
        # Preserve ALL structured node/edge properties (lost by LightRAG's fixed
        # schema) in the sidecar store, keyed by the SAME canonical names.
        self._merge_structured(final_graph, source_id, mapping, source_dates)
        chunk_results = self._graph_to_chunk_results(final_graph, source_id, file_path, mapping)
        n_nodes = len(chunk_results[0][0])
        n_edges = len(chunk_results[0][1])
        _log.info(f"cumulative-KG merge {source_id}: nodes={n_nodes} edges={n_edges}")
        await merge_nodes_and_edges(
            chunk_results=chunk_results,
            knowledge_graph_inst=self._rag.chunk_entity_relation_graph,
            entity_vdb=self._rag.entities_vdb,
            relationships_vdb=self._rag.relationships_vdb,
            global_config=asdict(self._rag),
            full_entities_storage=self._rag.full_entities,
            full_relations_storage=self._rag.full_relations,
            doc_id=source_id,
            pipeline_status=self._pipeline_status,
            pipeline_status_lock=self._pipeline_status_lock,
            llm_response_cache=self._rag.llm_response_cache,
            entity_chunks_storage=self._rag.entity_chunks,
            relation_chunks_storage=self._rag.relation_chunks,
            file_path=file_path,
        )
        await self._persist()
        return {
            "source_id": source_id,
            "nodes_merged": n_nodes,
            "edges_merged": n_edges,
            "registry": dict(self.registry.stats),
        }

    async def _get_node(self, name: str) -> dict | None:
        await self._initialize()
        return await self._rag.chunk_entity_relation_graph.get_node(name)

    async def _query(self, text: str, mode: str, kwargs: dict):
        await self._initialize()
        return await self._rag.aquery(text, param=QueryParam(mode=mode, **kwargs))

    async def _retrieve(self, text: str, mode: str, kwargs: dict) -> dict:
        await self._initialize()
        return await self._rag.aquery_data(text, param=QueryParam(mode=mode, **kwargs))

    async def _stats(self) -> dict:
        await self._initialize()
        graph = self._rag.chunk_entity_relation_graph
        labels = await graph.get_all_labels()
        n_edges = graph._graph.number_of_edges() if getattr(graph, "_graph", None) else None
        return {
            "entities": len(labels),
            "edges": n_edges,
            "canonicals": len(self.registry.canonicals),
        }

    def _merge_structured(self, final_graph: dict, source_id: str, mapping: dict,
                          source_dates: dict | None = None) -> None:
        """Feed every non-event node + relationship edge into the sidecar store,
        canonicalised to match the KG's entity names. Also preserves the temporal
        layer LightRAG drops: per-actor timelines, dated events with their
        (canonical) participants, event->event ordering edges, and per-edge time
        intervals (observed time from article pub dates, valid time from shared
        dated events)."""
        nodes, edges = final_graph["nodes"], final_graph["edges"]
        source_dates = source_dates or {}
        event_nodes = {n["identifier"]: n for n in nodes
                       if (n.get("node_type") or n.get("type")) == "event"}
        event_ids = set(event_nodes)
        for n in nodes:
            if n["identifier"] in event_ids:
                continue
            canon = mapping.get(n["identifier"], n["identifier"])
            self.structured.merge_entity(canon, n, source_id)
        # Participants per event (event_participation: event -> actor), canonicalised.
        participants: dict[str, list[str]] = {}
        for e in edges:
            if e.get("type") == "event_participation":
                ev, actor = e.get("src_identifier"), e.get("dst_identifier")
                if ev in event_ids and actor:
                    participants.setdefault(ev, []).append(mapping.get(actor, actor))
        for ev_id, en in event_nodes.items():
            self.structured.merge_event(ev_id, en, participants.get(ev_id, []), source_id)
        # Valid-time index: for each canonical actor, the dates of events it was in,
        # so a relationship's active window = the dates of events BOTH endpoints share.
        event_dates = {ev_id: _event_dates(en) for ev_id, en in event_nodes.items()}
        events_by_actor: dict[str, set] = {}
        for ev_id, acts in participants.items():
            for a in acts:
                events_by_actor.setdefault(a, set()).add(ev_id)
        for e in edges:
            src, dst = e.get("src_identifier"), e.get("dst_identifier")
            etype = e.get("type")
            if etype in ("event_followed_by", "event_coincident") and src and dst:
                self.structured.merge_temporal_edge(etype, src, dst)
                continue
            if etype == "evidence" or src in event_ids or dst in event_ids:
                continue
            cs, cd = mapping.get(src, src), mapping.get(dst, dst)
            if cs and cd and cs != cd:
                observed = sorted({d for u in _edge_urls(e) if (d := source_dates.get(u))})
                shared = events_by_actor.get(cs, set()) & events_by_actor.get(cd, set())
                win_dates = sorted(d for ev in shared for d in event_dates.get(ev, []))
                active = [win_dates[0], win_dates[-1]] if win_dates else None
                self.structured.merge_edge(cs, cd, e, source_id,
                                           observed_dates=observed, active_window=active)

    async def _persist(self) -> None:
        await self._rag.chunk_entity_relation_graph.index_done_callback()
        await self._rag.entities_vdb.index_done_callback()
        await self._rag.relationships_vdb.index_done_callback()
        self.registry.save()
        self.structured.save()
