"""Claim verification — plan (adversarial) → retrieve → stance → verdict.

Self-contained and additive: does NOT touch the entity-seeded investigation
pipeline. Given a claim, it plans both supporting and refuting search angles
(anti-confirmation-bias), retrieves via the NL-capable sources, classifies each
snippet's stance toward the claim, and aggregates to an ICD-203 confidence
verdict tempered by how many *independent* sources back the winning side.

Public entrypoint: ``verify_claim(claim, seed_entities=None, max_per_query=3)``.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"), override=False)
from openai import OpenAI  # noqa: E402

try:  # robust to both import contexts (repo-root on path vs research/ on path)
    from research.search_sources import fetch_gdelt, fetch_wikipedia
except ImportError:  # ui/server.py puts research/ itself on sys.path
    from search_sources import fetch_gdelt, fetch_wikipedia  # noqa: E402

_MODEL = os.environ.get("INVESTIGATOR_CLAIM_MODEL", "gpt-4o-mini")
# How many distinct sources on the winning side are needed before the verdict is
# allowed to reach full confidence; fewer sources temper the band toward the middle.
_SOURCES_FOR_FULL = 3

# ICD-203 ladder over the (source-tempered) net signal in [-1, 1].
_LADDER = [
    (0.80, "Almost Certainly"), (0.55, "Highly Likely"), (0.25, "Likely"),
    (-0.25, "Roughly Even Chance"), (-0.55, "Unlikely"), (-0.80, "Highly Unlikely"),
    (-2.0, "Almost Certainly Not"),
]

_client: OpenAI | None = None


def _llm_json(system: str, user: str) -> dict:
    global _client
    if _client is None:
        _client = OpenAI()
    r = _client.chat.completions.create(
        model=_MODEL, temperature=0.0,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
    )
    return json.loads(r.choices[0].message.content)


def plan_queries(claim: str, seed_entities: list[str] | None = None) -> dict:
    seeds = f" Known related entities: {', '.join(seed_entities[:8])}." if seed_entities else ""
    system = ("You are an OSINT fact-checker planning web/news searches to TEST a claim. "
              "Avoid confirmation bias: produce queries that would surface SUPPORTING evidence "
              "AND queries that would surface REFUTING evidence (denials, rebuttals, corrections, "
              "'no evidence', debunks). Short keyword phrases, not sentences.")
    user = (f'Claim: "{claim}".{seeds}\n'
            'Return JSON: {"support": [2-3 keyword queries], "refute": [2-3 keyword queries]}')
    out = _llm_json(system, user)
    return {"support": out.get("support", [])[:3], "refute": out.get("refute", [])[:3]}


def _classify(claim: str, item: dict) -> dict:
    snippet = f"{item.get('title','')} — {(_text(item))[:800]}".strip()
    system = ("You judge whether a snippet SUPPORTS, REFUTES, or is NEUTRAL toward a specific "
              "claim, based ONLY on the snippet. NEUTRAL if off-topic or it merely mentions the "
              "entities without bearing on the claim.")
    user = (f'Claim: "{claim}"\nSnippet: {snippet}\n'
            'Return JSON: {"stance":"SUPPORTS|REFUTES|NEUTRAL","confidence":0.0-1.0,"quote":"short verbatim span or empty"}')
    return _llm_json(system, user)


def _text(item: dict) -> str:
    t = item.get("text")
    if isinstance(t, list):
        t = " ".join(str(x) for x in t)
    return str(t or "")


def _source_of(item: dict) -> str:
    # Prefer the publisher recorded in metadata; else the url host; else the source tag.
    md = (item.get("metadata") or {}).get("doc_metadata") or {}
    if md.get("publisher"):
        return str(md["publisher"])
    url = item.get("url") or item.get("doc_id") or ""
    if "://" in url:
        return url.split("/")[2]
    return str(item.get("source") or "unknown")


def _verdict_label(tempered_net: float) -> str:
    for thresh, label in _LADDER:
        if tempered_net >= thresh:
            return label
    return "Almost Certainly Not"


def verify_claim(claim: str, seed_entities: list[str] | None = None, max_per_query: int = 3) -> dict:
    """Verify a claim end-to-end. Returns a structured verdict dict."""
    plan = plan_queries(claim, seed_entities)

    def _fetch(q):
        out = []
        for fn, n in ((fetch_gdelt, max_per_query), (fetch_wikipedia, 2)):
            try:
                out += fn(q, n)
            except Exception:  # noqa: BLE001 -- a flaky source must not sink the verdict
                pass
        return out

    items: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for batch in pool.map(_fetch, plan["support"] + plan["refute"]):
            items += batch
    # Dedup by url|title, keep only items with text.
    seen, uniq = set(), []
    for it in items:
        key = it.get("url") or it.get("doc_id") or it.get("title")
        if key and key not in seen and _text(it).strip():
            seen.add(key); uniq.append(it)

    sup_w = ref_w = 0.0
    sup_src, ref_src = set(), set()
    ev = {"SUPPORTS": [], "REFUTES": [], "NEUTRAL": []}

    def _safe(it):
        try:
            return _classify(claim, it), it
        except Exception:  # noqa: BLE001
            return None, it

    # Stance calls are independent I/O-bound LLM calls — fan them out.
    with ThreadPoolExecutor(max_workers=8) as pool:
        classified = list(pool.map(_safe, uniq))

    for c, it in classified:
        if c is None:
            continue
        stance = c.get("stance", "NEUTRAL")
        conf = float(c.get("confidence") or 0.0)
        src = _source_of(it)
        row = {"source": src, "title": it.get("title", "")[:120],
               "url": it.get("url") or it.get("doc_id", ""), "confidence": round(conf, 2),
               "quote": (c.get("quote") or "")[:200]}
        ev.get(stance, ev["NEUTRAL"]).append(row)
        if stance == "SUPPORTS":
            sup_w += conf; sup_src.add(src)
        elif stance == "REFUTES":
            ref_w += conf; ref_src.add(src)

    tot = sup_w + ref_w
    net = (sup_w - ref_w) / tot if tot else 0.0
    # Temper by the winning side's independent-source count: 1 source can't reach
    # full confidence.
    win_sources = len(sup_src) if net >= 0 else len(ref_src)
    tempered = net * min(1.0, win_sources / _SOURCES_FOR_FULL)

    return {
        "claim": claim,
        "verdict": _verdict_label(tempered),
        "net": round(net, 3),
        "tempered_net": round(tempered, 3),
        "queries": plan,
        "counts": {"snippets": len(uniq), "supports": len(ev["SUPPORTS"]),
                   "refutes": len(ev["REFUTES"]), "neutral": len(ev["NEUTRAL"]),
                   "support_sources": len(sup_src), "refute_sources": len(ref_src)},
        "support": sorted(ev["SUPPORTS"], key=lambda r: -r["confidence"])[:8],
        "refute": sorted(ev["REFUTES"], key=lambda r: -r["confidence"])[:8],
    }
