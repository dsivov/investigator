"""Enhanced GNews retrieval (opt-in).

The plain pipeline runs one literal GNews query and ingests its top-N
results. A single query has a hard recall ceiling: Google returns a
capped, opaquely-ranked set for one phrasing, so relevant articles framed
differently are never seen, and we only get title+metadata back until we
pay to fetch a body.

This module widens recall and then concentrates the body-fetch budget on
the best candidates:

  1. EXPAND   - an LLM turns (seed query, domain, hypothesis) into N
                focused sub-queries.
  2. RETRIEVE - run every query through GNews for TITLES + metadata only
                (no redirect-decode, no body fetch -> cheap), union + dedup.
  3. RERANK   - embed each candidate title (WordLlama, local) and score
                cosine-similarity against the ORIGINAL seed query.
  4. DEEPEN   - (depth > 1) extract the most relevant entities from the
                top titles and run one more retrieval turn per depth level,
                merging + reranking each time.
  5. TOP-K    - decode redirects + fetch bodies for the top-k only, then
                hand off to the normal pipeline (extract -> graph -> the
                body-level relevance gate, which is the real precision step).

The title rerank is deliberately a coarse recall->precision funnel; final
body-level relevance is enforced downstream by the pipeline's
hypothesis-scored evidence filter.

Standalone (inspect the ranked pool without running the full pipeline):

    PYTHONPATH=.:src \\
      /home/dsivov/.conda/envs/tangos/bin/python research/enhanced_retrieval.py \\
      --query "Russia oil sanctions evasion" --domain sanctions_evasion \\
      --depth 2 --top-k 40 --period 30d
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from gnews import GNews
from googlenewsdecoder import gnewsdecoder
from newspaper import Article

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import domain_presets as dp                       # noqa: E402
from evaluate_investigator_server import is_publisher  # noqa: E402


# ---------------------------------------------------------------------------
# Lazy models: WordLlama (local rerank) + dspy LM (expand / entity extraction)
# ---------------------------------------------------------------------------

_WL = None
_LM = None


def _wl():
    global _WL
    if _WL is None:
        from wordllama import WordLlama
        _WL = WordLlama.load_m2v("potion_base_8m")
    return _WL


def _lm():
    """Lazy dspy LM for the expand + key-entity calls. Key from repo .env."""
    global _LM
    if _LM is None:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=str(ROOT.parent / ".env"), override=False)
        import dspy
        _LM = dspy.LM("openai/gpt-4.1", temperature=0.0, max_tokens=400)
    return _LM


def _expand_queries(seed: str, domain_name: str, hypothesis: str, n: int) -> list[str]:
    """LLM: seed + domain + hypothesis -> N focused sub-queries."""
    import dspy

    class ExpandQueries(dspy.Signature):
        """Generate focused news-search sub-queries that widen recall for an
        investigation. Each sub-query targets a different angle of the seed
        topic that is relevant to the domain hypothesis (different actors,
        mechanisms, venues, or phrasings). Keep each <= 10 words, plain
        search strings, no quotes/booleans. Do not just restate the seed."""
        seed_query: str = dspy.InputField()
        domain_name: str = dspy.InputField()
        domain_hypothesis: str = dspy.InputField()
        how_many: int = dspy.InputField()
        sub_queries: list[str] = dspy.OutputField()

    try:
        with dspy.context(lm=_lm()):
            out = dspy.Predict(ExpandQueries)(
                seed_query=seed, domain_name=domain_name,
                domain_hypothesis=hypothesis, how_many=n)
        qs = [q.strip() for q in (out.sub_queries or []) if q and q.strip()]
        return qs[:n]
    except Exception as e:  # noqa: BLE001
        print(f"[expand] LLM unavailable: {e}", file=sys.stderr)
        return []


# Generic institutional / bridge entities crawl to unrelated cases when used
# as search-deepening seeds: deepening retrieval on "International Criminal
# Court" pulls in every other ICC case; on "District Court" or "Police", every
# other case they handle. They still enter the graph from the articles -- they
# are only barred from being *deepening seeds*. Excluded from depth-2+ turns.
_INSTITUTIONAL_TOKENS = frozenset({
    "court", "tribunal", "police", "prosecutor", "prosecution", "parliament",
    "congress", "senate", "assembly", "knesset", "ministry", "agency",
    "bureau", "interpol", "commission", "council", "directorate", "judiciary",
})
_INSTITUTIONAL_PHRASES = (
    "international criminal court", "supreme court", "district court",
    "high court", "attorney general", "department of justice", "united nations",
)


def _is_institutional(name: str) -> bool:
    """True for generic judicial / law-enforcement / legislative institutions
    that bridge across unrelated cases (poor deepening seeds)."""
    n = name.lower()
    if any(p in n for p in _INSTITUTIONAL_PHRASES):
        return True
    return bool(set(re.findall(r"[a-z]+", n)) & _INSTITUTIONAL_TOKENS)


def _key_entities(titles: list[str], domain_name: str, n: int) -> list[str]:
    """LLM: a batch of titles -> the most investigation-relevant named
    entities (people / orgs / mechanisms) to deepen the search on."""
    import dspy

    class KeyEntities(dspy.Signature):
        """From a list of news headlines, extract the named entities
        (people, organisations, mechanisms, places) most central to the
        investigation domain. Return the most relevant first. Exclude news
        outlets / publishers, AND generic institutions (courts, police,
        parliaments, agencies, the ICC) -- those bridge to unrelated cases;
        deepen on case-specific people and organisations only. Plain names."""
        headlines: list[str] = dspy.InputField()
        domain_name: str = dspy.InputField()
        how_many: int = dspy.InputField()
        entities: list[str] = dspy.OutputField()

    try:
        with dspy.context(lm=_lm()):
            out = dspy.Predict(KeyEntities)(
                headlines=titles[:40], domain_name=domain_name, how_many=n)
        ents = [e.strip() for e in (out.entities or [])
                if e and e.strip()
                and not is_publisher(e.strip())
                and not _is_institutional(e.strip())]
        return ents[:n]
    except Exception as e:  # noqa: BLE001
        print(f"[key-entities] LLM unavailable: {e}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Title-only retrieval + rerank
# ---------------------------------------------------------------------------

def _fetch_titles(query: str, *, period: str, max_results: int,
                  language: str = "en", country: str = "US") -> list[dict]:
    """GNews metadata only -- no redirect decode, no body fetch. Cheap."""
    gn = GNews(max_results=max_results, period=period, country=country, language=language)
    try:
        raw = gn.get_news(query) or []
    except Exception as e:  # noqa: BLE001
        print(f"[titles] '{query}' failed: {e}", file=sys.stderr)
        return []
    out = []
    for a in raw:
        pub = a.get("publisher") or {}
        out.append({
            "title": (a.get("title") or "").strip(),
            "google_url": a.get("url"),
            "publisher": pub.get("title") if isinstance(pub, dict) else (pub or ""),
            "published_date": a.get("published date"),
            "found_via": query,
        })
    return out


def _rerank(pool: dict, seed_query: str) -> None:
    """Score every candidate's title by cosine similarity to the seed query
    (WordLlama), writing `score` in place."""
    import numpy as np
    cands = list(pool.values())
    titles = [c["title"] or "" for c in cands]
    if not titles:
        return
    wl = _wl()
    q = np.asarray(wl.embed([seed_query]))[0]
    q = q / (np.linalg.norm(q) + 1e-9)
    embs = np.asarray(wl.embed(titles))
    embs = embs / (np.linalg.norm(embs, axis=1, keepdims=True) + 1e-9)
    sims = embs @ q
    for c, s in zip(cands, sims):
        c["score"] = float(s)


def enhanced_retrieve(seed_query: str, *, domain: str = "general",
                      hypothesis: str | None = None, depth: int = 1,
                      top_k: int = 50, expansions: int = 4,
                      entities_per_turn: int = 6, period: str = "30d",
                      titles_per_query: int = 50, verbose: bool = True) -> list[dict]:
    """Return up to `top_k` article dicts (title/publisher/url/date/text),
    bodies fetched for the top-k only. `depth`>=1; each level beyond 1 adds
    one entity-driven retrieval turn."""
    preset = dp.PRESETS.get(domain)
    hyp = hypothesis or (preset.hypothesis if preset else "")
    dom_name = domain.replace("_", " ")

    pool: dict[str, dict] = {}   # google_url -> candidate

    def _ingest(arts: list[dict]):
        for a in arts:
            key = a.get("google_url")
            if key and key not in pool:
                pool[key] = a

    # ---- Turn 1: expand + retrieve ----
    subqs = _expand_queries(seed_query, dom_name, hyp, expansions)
    queries = [seed_query] + subqs
    if verbose:
        print(f"[depth 1] {len(queries)} queries: {queries}")
    for q in queries:
        _ingest(_fetch_titles(q, period=period, max_results=titles_per_query))
    _rerank(pool, seed_query)
    if verbose:
        print(f"[depth 1] pool: {len(pool)} unique candidates")

    # ---- Turns 2..depth: deepen on most relevant entities ----
    for level in range(2, depth + 1):
        ranked = sorted(pool.values(), key=lambda c: -c.get("score", 0.0))
        top_titles = [c["title"] for c in ranked[:30] if c["title"]]
        ents = _key_entities(top_titles, dom_name, entities_per_turn)
        if verbose:
            print(f"[depth {level}] deepening on entities: {ents}")
        for ent in ents:
            eq = f"{ent} {dom_name}"
            _ingest(_fetch_titles(eq, period=period, max_results=titles_per_query))
        _rerank(pool, seed_query)
        if verbose:
            print(f"[depth {level}] pool: {len(pool)} unique candidates")

    # ---- Top-k: decode redirects + fetch bodies ----
    ranked = sorted(pool.values(), key=lambda c: -c.get("score", 0.0))[:top_k]
    if verbose:
        print(f"[fetch] decoding + fetching bodies for top {len(ranked)}")
    out = []
    for c in ranked:
        rec = {"title": c["title"], "publisher": c["publisher"], "real_url": None,
               "published_date": c["published_date"], "text": "", "error": None,
               "score": c.get("score"), "found_via": c.get("found_via")}
        try:
            r = gnewsdecoder(c["google_url"], interval=1)
            if not r.get("status"):
                rec["error"] = f"redirect-decode: {r}"; out.append(rec); continue
            rec["real_url"] = r["decoded_url"]
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"redirect-decode: {type(e).__name__}: {e}"; out.append(rec); continue
        try:
            art = Article(rec["real_url"]); art.download(); art.parse()
            rec["text"] = (art.text or "").strip()
            if not rec["text"]:
                rec["error"] = "empty body"
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"extract: {type(e).__name__}: {str(e)[:150]}"
        out.append(rec)
        time.sleep(0.3)
    return out


# ---------------------------------------------------------------------------
# Standalone CLI (inspect the ranked pool; no full pipeline)
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--query", required=True)
    p.add_argument("--domain", default="general")
    p.add_argument("--hypothesis", default=None)
    p.add_argument("--depth", type=int, default=1)
    p.add_argument("--top-k", type=int, default=40)
    p.add_argument("--expansions", type=int, default=4)
    p.add_argument("--period", default="30d")
    p.add_argument("--no-bodies", action="store_true",
                   help="stop after rerank; print the ranked titles only (no body fetch)")
    args = p.parse_args()

    if args.no_bodies:
        # Run the expand+retrieve+rerank+deepen stages, print the pool.
        preset = dp.PRESETS.get(args.domain)
        hyp = args.hypothesis or (preset.hypothesis if preset else "")
        dom = args.domain.replace("_", " ")
        pool: dict[str, dict] = {}
        for q in [args.query] + _expand_queries(args.query, dom, hyp, args.expansions):
            for a in _fetch_titles(q, period=args.period, max_results=50):
                pool.setdefault(a["google_url"], a)
        _rerank(pool, args.query)
        for lvl in range(2, args.depth + 1):
            ranked = sorted(pool.values(), key=lambda c: -c.get("score", 0.0))
            ents = _key_entities([c["title"] for c in ranked[:30] if c["title"]], dom, 6)
            print(f"[depth {lvl}] entities: {ents}", file=sys.stderr)
            for e in ents:
                for a in _fetch_titles(f"{e} {dom}", period=args.period, max_results=50):
                    pool.setdefault(a["google_url"], a)
            _rerank(pool, args.query)
        ranked = sorted(pool.values(), key=lambda c: -c.get("score", 0.0))
        print(f"\n=== {len(pool)} unique candidates; top {min(args.top_k, len(ranked))} by title-relevance ===")
        for c in ranked[:args.top_k]:
            print(f"  {c.get('score', 0):.3f}  {c['title'][:90]}   [{c['publisher']}]")
        return 0

    arts = enhanced_retrieve(args.query, domain=args.domain, hypothesis=args.hypothesis,
                             depth=args.depth, top_k=args.top_k, expansions=args.expansions,
                             period=args.period)
    bodies = sum(1 for a in arts if a.get("text"))
    print(f"\n=== retrieved {len(arts)} top-k; {bodies} with body, {len(arts)-bodies} headline-only ===")
    for a in arts[:args.top_k]:
        print(f"  {a.get('score', 0):.3f}  {a['title'][:80]}  ({len(a.get('text') or '')} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
