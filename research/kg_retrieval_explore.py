"""Explore LightRAG retrieval over the cumulative KG, to decide how to use it
in investigations.

Pipeline:
  1. Load N distinct investigation graphs into a CumulativeKG (real OpenAI LLM
     for keyword-extraction/summarisation + local WordLlama embeddings -- the
     embeddings MUST match what merge wrote, so retrieval vectors line up).
  2. Validate the accumulated store (entity / edge / canonical counts).
  3. For a few queries, compare retrieval modes (local / global / hybrid / mix)
     across BOTH endpoints:
        - retrieve()  -> aquery_data : structured entities/relationships/chunks,
                         NO LLM synthesis  ("raw data" endpoint)
        - query()     -> aquery      : LLM-synthesised answer  ("summary" endpoint)

Notes:
  * `naive` mode + the chunk side of `mix` need text chunks; the in-code merge
    does not populate chunks, so those retrieve only graph-derived context.
  * Even the "raw data" endpoint calls the LLM for keyword extraction in KG modes.

Usage:
    PYTHONPATH=.:src python research/kg_retrieval_explore.py [--n 8] [--model gpt-4.1-mini]
        [--store news_investigations/kg_explore_store] [--reset] [-q "a question" -q "another"]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# investigator.config parses sys.argv at import time (it wraps LightRAG's global
# argparse), so hide our CLI flags from it: parse ours first, then blank argv.
_AP = argparse.ArgumentParser(description=__doc__)
_AP.add_argument("--n", type=int, default=8, help="number of distinct investigations to load")
_AP.add_argument("--model", default="gpt-4.1-mini", help="OpenAI model for keyword extraction + synthesis")
_AP.add_argument("--store", default="news_investigations/kg_explore_store", help="LightRAG working dir")
_AP.add_argument("--reset", action="store_true", help="wipe the store and re-ingest")
_AP.add_argument("-q", action="append", help="query (repeatable); overrides defaults")
ARGS = _AP.parse_args()
sys.argv = [sys.argv[0]]

from lightrag.llm.openai import openai_complete_if_cache  # noqa: E402

from investigator.analytics.cumulative_kg import CumulativeKG  # noqa: E402
from investigator.config import SecretLoader  # noqa: E402

ARTIFACT_DIR = Path("news_investigations/cross_event")
_TS = re.compile(r"_\d{8}_\d{6}")
MODES = ["local", "global", "hybrid", "mix"]
DEFAULT_QUERIES = [
    "What is the relationship between Hezbollah, Iran, and Israel?",
    "Who is Benjamin Netanyahu connected to and how?",
    "Which organizations and people are involved in sanctions?",
]


def make_openai_llm(model: str):
    """LightRAG-compatible llm_model_func backed by OpenAI.

    Deliberately avoids structured-output keyword extraction: openai 1.83.0 has
    no ``chat.completions.parse`` (only the beta path), but LightRAG calls the
    non-beta ``.parse`` whenever ``response_format`` is set. So we drop
    ``keyword_extraction``/``response_format`` and route through ``.create`` --
    LightRAG then parses the keyword JSON from the plain completion text.
    """
    api_key = os.environ.get("OPENAI_API_KEY")

    async def _llm(prompt, system_prompt=None, history_messages=None, **kwargs):
        kwargs.pop("keyword_extraction", None)
        kwargs.pop("response_format", None)
        return await openai_complete_if_cache(
            model,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages or [],
            api_key=api_key,
            **kwargs,
        )

    return _llm


def select_artifacts(n: int) -> list[Path]:
    """Latest file per distinct investigation prefix (collapse timestamps)."""
    latest: dict[str, Path] = {}
    for p in ARTIFACT_DIR.glob("*.json"):
        if ".enriched" in p.name:
            continue
        if '"final_merged_graph"' not in p.read_text():  # some files use a different schema
            continue
        key = _TS.sub("", p.stem)
        if key not in latest or p.stat().st_mtime > latest[key].stat().st_mtime:
            latest[key] = p
    chosen = sorted(latest.values(), key=lambda x: x.stat().st_mtime, reverse=True)[:n]
    return chosen


def _top_entity_names(data: dict, k: int = 6) -> list[str]:
    ents = (data.get("data") or {}).get("entities") or []
    return [e.get("entity_name", "?") for e in ents[:k]]


async def ingest(kg: CumulativeKG, artifacts: list[Path]) -> None:
    for p in artifacts:
        art = json.loads(p.read_text())
        graph = art["final_merged_graph"]
        source_id = f"inv::{_TS.sub('', p.stem)}"
        summary = await kg.merge_graph(graph, source_id=source_id, file_path=p.name,
                                       source_dates=art.get("source_dates"))
        print(f"  + {source_id:60} nodes={summary['nodes_merged']:>4} edges={summary['edges_merged']:>4}")


async def run(args) -> int:
    SecretLoader().export_to_env("OPENAI_API_KEY")
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not available (needed for keyword extraction + synthesis).", file=sys.stderr)
        return 1

    store = Path(args.store)
    graphml = store / "graph_chunk_entity_relation.graphml"
    if args.reset and store.exists():
        shutil.rmtree(store)

    kg = CumulativeKG(working_dir=store, llm_model_func=make_openai_llm(args.model))

    if args.reset or not graphml.exists():
        artifacts = select_artifacts(args.n)
        print(f"=== INGEST: {len(artifacts)} investigations into {store} (model={args.model}) ===")
        await ingest(kg, artifacts)
    else:
        print(f"=== Reusing existing store at {store} (use --reset to re-ingest) ===")

    st = await kg.stats()
    print(f"\n=== VALIDATE: entities={st['entities']} edges={st['edges']} canonicals={st['canonicals']} ===")
    review = store / "canonicalizer_review.jsonl"
    if review.exists():
        print(f"    canonicalizer review log: {len(review.read_text().splitlines())} borderline pair(s) flagged")

    queries = args.q or DEFAULT_QUERIES
    for q in queries:
        print(f"\n{'='*88}\nQUERY: {q}\n{'='*88}")
        for mode in MODES:
            try:
                data = await kg.retrieve(q, mode=mode)
                d = data.get("data") or {}
                ents, rels, chunks = d.get("entities") or [], d.get("relationships") or [], d.get("chunks") or []
                print(f"\n[{mode:6}] raw-data: entities={len(ents)} relationships={len(rels)} chunks={len(chunks)}")
                print(f"          top entities: {', '.join(_top_entity_names(data)) or '(none)'}")
            except Exception as e:  # noqa: BLE001
                print(f"\n[{mode:6}] raw-data FAILED: {type(e).__name__}: {e}")
            try:
                answer = await kg.query(q, mode=mode)
                ans = (answer or "").strip().replace("\n", " ")
                print(f"[{mode:6}] answer:   {ans[:280]}{'...' if len(ans) > 280 else ''}")
            except Exception as e:  # noqa: BLE001
                print(f"[{mode:6}] answer FAILED: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(ARGS)))
