"""Daily intake: fetch fresh news for the watched subjects and extract a graph.

Reuses the existing stack end-to-end: GNews fetch (``fetch_news``) for each
watched subject, then the engine's extraction pipeline (``POST
/api/v1/get_nodes``) to turn the day's articles into canonicalised entities +
dated events + edges. The engine should run with analytics OFF so it only
extracts and does NOT merge into the global KG (the monitor is read-only; the
intersection filter decides what's relevant afterwards).

``fetch_fn`` is injectable so the module is testable without network/GNews.
"""
from __future__ import annotations

import json
import uuid

import requests

ENGINE_BASE = "http://127.0.0.1:5003/api/v1"


def default_fetch(subject: str, *, k: int, period: str) -> list[dict]:
    """Fetch top-k news for one subject via the existing GNews fetcher (lazy
    import -- it lives at repo root and pulls heavy deps)."""
    import evaluate_investigator_server as ev  # noqa: PLC0415
    return ev.fetch_news(subject, max_articles=k, period=period)


def _payload(query: str, articles: list[dict]) -> dict:
    """Shape articles into the engine's chunker input: ``{query: {source_id:
    {query, title, ..., text}}}``. Mirrors ``build_payload`` but kept local so the
    monitor doesn't depend on a root research script."""
    body = {}
    for i, a in enumerate(articles):
        text = (a.get("text") or "").strip()
        if not text and not (a.get("title") or "").strip():
            continue
        key = f"{a.get('publisher') or 'unknown'}__{i}"
        body[key] = {
            "query": query,
            "title": a.get("title") or "",
            "publisher": a.get("publisher") or "",
            "url": a.get("real_url") or a.get("url") or "",
            "published_date": a.get("published_date") or "",
            "text": text,
            "body_available": bool(text),
        }
    return {query: body}


def extract_via_engine(query: str, articles: list[dict], *, domain: str = "general",
                       base_url: str = ENGINE_BASE, session_id: str | None = None,
                       timeout: int = 600) -> dict:
    """POST a batch of articles to the running engine; return ``{nodes, edges}``."""
    if not articles:
        return {"nodes": [], "edges": []}
    resp = requests.post(f"{base_url}/get_nodes", timeout=timeout, json={
        "session_id": session_id or str(uuid.uuid4()),
        "text": json.dumps(_payload(query, articles)),
        "query": query,
        "domain": domain,
    })
    resp.raise_for_status()
    d = resp.json()
    return {"nodes": d.get("nodes") or [], "edges": d.get("edges") or []}


def daily_intake(watchlist, *, k: int = 8, period: str = "1d", domain: str = "general",
                 base_url: str = ENGINE_BASE, fetch_fn=default_fetch, extract_fn=None) -> dict:
    """Fetch news for every watched subject and extract one combined day-graph.

    ``fetch_fn(subject, k, period) -> [article]`` and ``extract_fn(query,
    articles, domain, base_url) -> {nodes, edges}`` are injectable (tests stub
    them; default uses GNews + the running engine). Returns ``{"nodes", "edges",
    "articles", "subjects"}``.
    """
    extract_fn = extract_fn or (lambda q, a, **kw: extract_via_engine(q, a, **kw))
    subjects = watchlist.subjects()
    articles: list[dict] = []
    for subj in subjects:
        try:
            articles += fetch_fn(subj, k=k, period=period) or []
        except Exception:  # noqa: BLE001 -- one bad subject shouldn't sink the run
            continue
    query = " / ".join(subjects)[:200] or "monitor"
    graph = extract_fn(query, articles, domain=domain, base_url=base_url)
    return {"nodes": graph.get("nodes") or [], "edges": graph.get("edges") or [],
            "articles": articles, "subjects": subjects}
