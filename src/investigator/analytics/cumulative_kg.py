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

import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from lightrag import LightRAG
from lightrag.kg.shared_storage import (
    get_namespace_data,
    get_namespace_lock,
    initialize_pipeline_status,
)
from lightrag.operate import merge_nodes_and_edges
from lightrag.utils import EmbeddingFunc

from investigator.analytics.canonicalizer import CanonicalRegistry, resolve_graph_entities
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


def _entity_description(node: dict) -> str:
    d = node.get("data") or {}
    parts = []
    for key, label in (("position", ""), ("location", "Location: ")):
        v = d.get(key)
        if isinstance(v, str) and v.strip() and v.strip().lower() not in _EMPTY:
            parts.append(f"{label}{v.strip()}")
    return " | ".join(parts) or f"{node['identifier']} ({d.get('type', 'ENTITY')})"


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

    Lifecycle::

        kg = CumulativeKG(working_dir, registry_path)
        await kg.initialize()
        await kg.merge_graph(final_graph_a, source_id="inv::a")
        await kg.merge_graph(final_graph_b, source_id="inv::b")

    Call :meth:`merge_graph` once per finished investigation.
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
        self._llm = llm_model_func or _concat_llm
        self._embed = embedding_func or _default_embedding_func()
        self._rag: LightRAG | None = None
        self._pipeline_status = None
        self._pipeline_status_lock = None

    async def initialize(self) -> None:
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

    def _graph_to_chunk_results(self, final_graph: dict, source_id: str, file_path: str, mapping: dict) -> list:
        """One investigation graph -> a single (maybe_nodes, maybe_edges) chunk
        result, with every entity name rewritten to its global canonical."""
        ts = int(time.time())
        maybe_nodes: dict = {}
        for n in final_graph["nodes"]:
            if (n.get("node_type") or n.get("type")) == "event":
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
            if e.get("type") == "evidence":
                continue
            s = mapping.get(e.get("src_identifier"), e.get("src_identifier"))
            t = mapping.get(e.get("dst_identifier"), e.get("dst_identifier"))
            if not (s and t) or s == t:
                continue
            rtype, ctx = _edge_relation(e)
            maybe_edges.setdefault((s, t), []).append(
                {
                    "src_id": s,
                    "tgt_id": t,
                    "weight": float(e.get("weight") or 1.0),
                    "description": ctx or rtype,
                    "keywords": rtype,
                    "source_id": source_id,
                    "file_path": file_path,
                    "timestamp": ts,
                }
            )
        return [(maybe_nodes, maybe_edges)]

    async def merge_graph(self, final_graph: dict, source_id: str, file_path: str | None = None) -> dict:
        """Canonicalize then merge one investigation graph into the cumulative KG.

        Returns a small summary dict (node/edge counts merged + registry stats).
        """
        if self._rag is None:
            raise RuntimeError("CumulativeKG.initialize() must be called before merge_graph()")
        file_path = file_path or source_id
        mapping = resolve_graph_entities(final_graph, self.registry, source_id)
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

    async def get_node(self, name: str) -> dict | None:
        """Look up a merged entity by its canonical name (mainly for tests/debug)."""
        return await self._rag.chunk_entity_relation_graph.get_node(name)

    async def _persist(self) -> None:
        await self._rag.chunk_entity_relation_graph.index_done_callback()
        await self._rag.entities_vdb.index_done_callback()
        await self._rag.relationships_vdb.index_done_callback()
        self.registry.save()
