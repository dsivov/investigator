"""Prototype: merge investigation graphs into one cumulative LightRAG KG via the
in-code merge path (NOT insert_custom_kg, which overwrites by name).

Validates that LightRAG's `merge_nodes_and_edges` accumulates across separate
investigations: a shared entity ends up with its `source_id` unioned across the
investigations that attest it (proof of merge, not clobber), without invoking
LightRAG's internal LLM graph extraction.

Embeddings use WordLlama (local, free); the LLM func is a stub (small inputs
don't trip description summarisation). Storage defaults to file-based
(NetworkX + nano-vdb + JSON-KV) under the working dir.

NEXT STEP (not here): a global cross-investigation canonicalization layer --
LightRAG merges by EXACT entity name, so "IRAN" vs "ISLAMIC REPUBLIC OF IRAN"
across investigations would NOT merge. That alias layer is the real follow-up.

Usage:
    PYTHONPATH=.:src python research/kg_merge_prototype.py <artifactA.json> <artifactB.json>
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from lightrag import LightRAG
from lightrag.operate import merge_nodes_and_edges
from lightrag.utils import EmbeddingFunc
from lightrag.kg.shared_storage import (
    initialize_pipeline_status,
    get_namespace_data,
    get_namespace_lock,
)

from investigator.graph.dedup import _wl  # reuse the loaded WordLlama model

_EMPTY = {"", "not found", "unknown", "n/a"}


async def _stub_llm(prompt, system_prompt=None, history_messages=None, **kwargs) -> str:
    # Description summarisation won't fire for the small per-entity fragment
    # counts in this prototype; this stub just satisfies the required arg.
    return "summary not generated (prototype)"


def _embedding_func() -> EmbeddingFunc:
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


def _artifact_to_chunk_results(path: Path, source_id: str) -> list:
    """One investigation graph -> a single (maybe_nodes, maybe_edges) chunk result."""
    f = json.loads(path.read_text())["final_merged_graph"]
    file_path = path.name
    ts = int(time.time())
    maybe_nodes: dict = {}
    for n in f["nodes"]:
        if (n.get("node_type") or n.get("type")) == "event":
            continue
        name = n["identifier"]
        maybe_nodes.setdefault(name, []).append({
            "entity_name": name,
            "entity_type": _coerce_type((n.get("data") or {}).get("type")),
            "description": _entity_description(n),
            "source_id": source_id,
            "file_path": file_path,
            "timestamp": ts,
        })
    maybe_edges: dict = {}
    for e in f["edges"]:
        if e.get("type") == "evidence":
            continue
        s, t = e.get("src_identifier"), e.get("dst_identifier")
        if not (s and t) or s == t:
            continue
        rtype, ctx = _edge_relation(e)
        maybe_edges.setdefault((s, t), []).append({
            "src_id": s, "tgt_id": t,
            "weight": float(e.get("weight") or 1.0),
            "description": ctx or rtype,
            "keywords": rtype,
            "source_id": source_id,
            "file_path": file_path,
            "timestamp": ts,
        })
    return [(maybe_nodes, maybe_edges)]


async def _merge_one(rag: LightRAG, path: Path, source_id: str,
                     pipeline_status, pipeline_status_lock) -> None:
    chunk_results = _artifact_to_chunk_results(path, source_id)
    n_nodes = len(chunk_results[0][0])
    n_edges = len(chunk_results[0][1])
    print(f"merging {path.name}  (source_id={source_id})  nodes={n_nodes} edges={n_edges}")
    await merge_nodes_and_edges(
        chunk_results=chunk_results,
        knowledge_graph_inst=rag.chunk_entity_relation_graph,
        entity_vdb=rag.entities_vdb,
        relationships_vdb=rag.relationships_vdb,
        global_config=asdict(rag),
        full_entities_storage=rag.full_entities,
        full_relations_storage=rag.full_relations,
        doc_id=source_id,
        pipeline_status=pipeline_status,
        pipeline_status_lock=pipeline_status_lock,
        llm_response_cache=rag.llm_response_cache,
        entity_chunks_storage=rag.entity_chunks,
        relation_chunks_storage=rag.relation_chunks,
        file_path=path.name,
    )
    # Persist the merged state to disk.
    await rag.chunk_entity_relation_graph.index_done_callback()
    await rag.entities_vdb.index_done_callback()
    await rag.relationships_vdb.index_done_callback()


async def main(a: Path, b: Path) -> int:
    work = Path("news_investigations/kg_prototype")
    work.mkdir(parents=True, exist_ok=True)
    rag = LightRAG(
        working_dir=str(work),
        llm_model_func=_stub_llm,
        embedding_func=_embedding_func(),
    )
    await rag.initialize_storages()
    await initialize_pipeline_status()
    pipeline_status = await get_namespace_data("pipeline_status", workspace=rag.workspace)
    pipeline_status_lock = get_namespace_lock("pipeline_status", workspace=rag.workspace)

    src_a, src_b = f"inv::{a.stem}", f"inv::{b.stem}"
    ents_a = {n["identifier"] for n in json.loads(a.read_text())["final_merged_graph"]["nodes"]
              if (n.get("node_type") or n.get("type")) != "event"}
    ents_b = {n["identifier"] for n in json.loads(b.read_text())["final_merged_graph"]["nodes"]
              if (n.get("node_type") or n.get("type")) != "event"}
    shared = sorted(ents_a & ents_b)
    print(f"entities: A={len(ents_a)} B={len(ents_b)} shared={len(shared)}")

    await _merge_one(rag, a, src_a, pipeline_status, pipeline_status_lock)
    await _merge_one(rag, b, src_b, pipeline_status, pipeline_status_lock)

    # --- Validate: shared entities should carry BOTH investigations' source_ids
    print("\n=== merge validation (shared entities should union both source_ids) ===")
    checked = ok = 0
    for name in shared[:8]:
        node = await rag.chunk_entity_relation_graph.get_node(name)
        if not node:
            print(f"  {name:32} MISSING from KG"); continue
        sids = set((node.get("source_id") or "").split("<SEP>")) | set((node.get("source_id") or "").split("|"))
        # LightRAG joins with GRAPH_FIELD_SEP; check both tokens are present as substrings
        s = node.get("source_id") or ""
        both = (src_a in s) and (src_b in s)
        checked += 1; ok += int(both)
        print(f"  {name:32} both_sources={both}  source_id={s[:70]}")
    print(f"\n{ok}/{checked} shared entities merged (source_id unions both). "
          f"KG persisted under {work}/")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: kg_merge_prototype.py <artifactA.json> <artifactB.json>", file=sys.stderr)
        sys.exit(1)
    sys.exit(asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2]))))
