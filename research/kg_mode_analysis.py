"""Which LightRAG retrieval MODE (local/global/hybrid/mix) is most useful for
our cumulative KG, for each ENDPOINT (data = aquery_data, query = aquery)?

Runs a fixed set of representative queries against an existing CumulativeKG store
and reports objective metrics so the choice is measured, not assumed:

DATA endpoint (structured retrieval, no synthesis), per query x mode:
  * n_entities / n_relationships / n_chunks
  * overlap: how much each mode's entity set is contained in hybrid, and the
    mix-vs-hybrid Jaccard (are they effectively identical here?)
  * unique entities a mode contributes that the others miss

QUERY endpoint (LLM answer), per query x mode:
  * answer length
  * grounding: fraction of the mode's retrieved entities named in the answer
    (proxy for "the prose actually uses the retrieved context")

Plus wall-clock latency per call (data vs query cost).

Usage:
    PYTHONPATH=.:src python research/kg_mode_analysis.py
        [--store news_investigations/kg_explore_store] [--model gpt-4.1-mini]
        [-q "a question" -q "another"]
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_AP = argparse.ArgumentParser(description=__doc__)
_AP.add_argument("--store", default="news_investigations/kg_explore_store")
_AP.add_argument("--model", default="gpt-4.1-mini")
_AP.add_argument("-q", action="append", help="query (repeatable); overrides defaults")
ARGS = _AP.parse_args()
sys.argv = [sys.argv[0]]

from investigator.analytics.cumulative_kg import CumulativeKG  # noqa: E402
from investigator.analytics.llm import make_openai_llm  # noqa: E402
from investigator.config import SecretLoader  # noqa: E402

MODES = ["local", "global", "hybrid", "mix"]

# Representative query shapes: entity-centric, relational, thematic, broad.
DEFAULT_QUERIES = [
    "Who is Benjamin Netanyahu connected to and how?",            # entity-centric
    "What is the relationship between Netanyahu and Shaul Elovich?",  # relational pair
    "Which organizations and people are involved in corruption?",  # thematic
    "What links the Unification Church to political figures?",     # thematic/relational
    "What are the main corruption cases across these investigations?",  # broad
]


def _names(entities) -> list[str]:
    return [(e.get("entity_name") or "").upper() for e in entities if e.get("entity_name")]


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


async def _analyze_query(kb: CumulativeKG, q: str) -> dict:
    data_by_mode, ans_by_mode, lat = {}, {}, {}
    for mode in MODES:
        t0 = time.time()
        d = await kb.retrieve(q, mode=mode)
        lat[(mode, "data")] = time.time() - t0
        dd = (d or {}).get("data") or {}
        data_by_mode[mode] = {
            "entities": _names(dd.get("entities") or []),
            "n_rel": len(dd.get("relationships") or []),
            "n_chunk": len(dd.get("chunks") or []),
        }
        t0 = time.time()
        ans = await kb.query(q, mode=mode)
        lat[(mode, "query")] = time.time() - t0
        ans_by_mode[mode] = ans or ""
    return {"q": q, "data": data_by_mode, "ans": ans_by_mode, "lat": lat}


def _report(results: list[dict]) -> None:
    print("\n" + "=" * 90)
    print("PER-QUERY METRICS")
    print("=" * 90)
    agg = {m: {"ent": [], "rel": [], "chunk": [], "ans_len": [], "ground": [],
               "in_hybrid": [], "lat_data": [], "lat_query": []} for m in MODES}
    mix_hybrid_jac = []

    for r in results:
        print(f"\nQUERY: {r['q']}")
        hybrid_ents = set(r["data"]["hybrid"]["entities"])
        for m in MODES:
            dm = r["data"][m]
            ents = set(dm["entities"])
            ans = r["ans"][m]
            grounded = sum(1 for e in ents if e in ans.upper()) / len(ents) if ents else 0.0
            in_hybrid = (len(ents & hybrid_ents) / len(ents)) if ents else 0.0
            agg[m]["ent"].append(len(ents)); agg[m]["rel"].append(dm["n_rel"])
            agg[m]["chunk"].append(dm["n_chunk"]); agg[m]["ans_len"].append(len(ans))
            agg[m]["ground"].append(grounded); agg[m]["in_hybrid"].append(in_hybrid)
            agg[m]["lat_data"].append(r["lat"][(m, "data")])
            agg[m]["lat_query"].append(r["lat"][(m, "query")])
            print(f"  [{m:6}] data: ent={len(ents):3} rel={dm['n_rel']:3} chunk={dm['n_chunk']} "
                  f"| in_hybrid={in_hybrid:4.0%} | query: ans={len(ans):4}c grounded={grounded:4.0%}")
        mix_hybrid_jac.append(_jaccard(set(r["data"]["mix"]["entities"]), hybrid_ents))

    print("\n" + "=" * 90)
    print("AGGREGATE (mean across queries)")
    print("=" * 90)
    print(f"{'mode':8} {'ent':>5} {'rel':>5} {'chunk':>6} {'in_hybrid':>10} "
          f"{'ans_chars':>10} {'grounded':>9} {'data_s':>7} {'query_s':>8}")
    for m in MODES:
        a = agg[m]
        mean = lambda xs: statistics.mean(xs) if xs else 0.0  # noqa: E731
        print(f"{m:8} {mean(a['ent']):5.0f} {mean(a['rel']):5.0f} {mean(a['chunk']):6.1f} "
              f"{mean(a['in_hybrid']):9.0%} {mean(a['ans_len']):10.0f} {mean(a['ground']):8.0%} "
              f"{mean(a['lat_data']):7.1f} {mean(a['lat_query']):8.1f}")
    print(f"\nmix vs hybrid entity-set Jaccard (mean): {statistics.mean(mix_hybrid_jac):.2f} "
          f"(1.00 => mix adds nothing over hybrid here)")
    # unique contribution: entities a mode finds that NO other mode does
    print("\nUNIQUE entities per mode (found by this mode, missed by the other three), summed:")
    for r in results:
        pass
    uniq = {m: 0 for m in MODES}
    for r in results:
        sets = {m: set(r["data"][m]["entities"]) for m in MODES}
        for m in MODES:
            others = set().union(*(sets[o] for o in MODES if o != m))
            uniq[m] += len(sets[m] - others)
    for m in MODES:
        print(f"  {m:8} {uniq[m]} unique entities across all queries")


async def main() -> int:
    SecretLoader().export_to_env("OPENAI_API_KEY")
    store = Path(ARGS.store)
    if not (store / "graph_chunk_entity_relation.graphml").exists():
        print(f"No KG store at {store}", file=sys.stderr); return 1
    kb = CumulativeKG(working_dir=store, llm_model_func=make_openai_llm(ARGS.model))
    st = await kb.stats()
    print(f"store={store}  entities={st['entities']} edges={st['edges']}")
    queries = ARGS.q or DEFAULT_QUERIES
    results = []
    for q in queries:
        print(f"... running {q!r}", flush=True)
        results.append(await _analyze_query(kb, q))
    _report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
