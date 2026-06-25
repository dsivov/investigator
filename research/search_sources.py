"""Pluggable search-source providers: fetch text about a subject from sources
beyond Google News, in the SAME article-dict shape the rest of the pipeline
consumes (see source_ingest.py):

    {"title", "publisher", "real_url", "published_date", "text", "error", "provenance"}

Each provider is independent and degrades gracefully: one that needs an API key
and has none simply yields nothing (with a note) rather than failing the run.
The set is configurable -- the UI enables sources per-investigation, the same
place as the Google-News toggle.

Providers in this pass (all free; key-gated ones skip without a key):
  * wikipedia    -- MediaWiki API: encyclopedic article text. No key.
  * gdelt        -- GDELT 2.0 DOC API: global news. No key (returns URLs we
                    extract with newspaper3k).
  * opensanctions-- OpenSanctions search API: sanctions / PEP / watchlist
                    entries as text. Needs INVESTIGATOR_OPENSANCTIONS_KEY.
  * websearch    -- Generic web search: Google Programmable Search (CSE) when
                    INVESTIGATOR_GOOGLE_API_KEY + INVESTIGATOR_GOOGLE_CSE_ID are
                    set, else DuckDuckGo (no key). Bodies via newspaper3k.

Add a provider by writing a fetch fn and registering it in SEARCH_SOURCES.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

_UA = {"User-Agent": "investigator-osint/1.0 (research; contact via repo)"}
_TIMEOUT = 20


def _record(title: str, text: str, url: str | None, publisher: str,
            provenance: str, published: str | None = None) -> dict:
    return {
        "title": (title or url or provenance)[:300],
        "publisher": publisher,
        "real_url": url,
        "published_date": published,
        "text": (text or "").strip(),
        "error": None,
        "provenance": provenance,
    }


# --- Wikipedia (MediaWiki API; no key) -------------------------------------

_WIKI_API = "https://en.wikipedia.org/w/api.php"


def fetch_wikipedia(query: str, max_items: int) -> list[dict]:
    out: list[dict] = []
    try:
        sr = requests.get(_WIKI_API, headers=_UA, timeout=_TIMEOUT, params={
            "action": "query", "list": "search", "srsearch": query,
            "srlimit": max_items, "format": "json"}).json()
        hits = sr.get("query", {}).get("search", [])
    except Exception as e:  # noqa: BLE001
        return [_record(f"wikipedia search failed", "", None, "wikipedia",
                        "wikipedia") | {"error": str(e)[:200], "text": ""}]
    for h in hits[:max_items]:
        pageid = h.get("pageid")
        try:
            ex = requests.get(_WIKI_API, headers=_UA, timeout=_TIMEOUT, params={
                "action": "query", "prop": "extracts", "explaintext": 1,
                "pageids": pageid, "format": "json"}).json()
            page = ex["query"]["pages"][str(pageid)]
            text = page.get("extract") or ""
            url = f"https://en.wikipedia.org/?curid={pageid}"
            if text.strip():
                out.append(_record(page.get("title", ""), text, url,
                                   "en.wikipedia.org", "wikipedia"))
        except Exception:  # noqa: BLE001 -- skip the page, keep the rest
            continue
    return out


# --- GDELT 2.0 DOC API (global news; no key) -------------------------------

_GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"


def fetch_gdelt(query: str, max_items: int) -> list[dict]:
    try:
        r = requests.get(_GDELT, headers=_UA, timeout=_TIMEOUT, params={
            "query": query, "mode": "artlist", "maxrecords": max_items,
            "format": "json", "sort": "hybridrel"})
        arts = r.json().get("articles", []) if r.headers.get(
            "content-type", "").startswith("application/json") else []
    except Exception as e:  # noqa: BLE001
        return [_record("gdelt search failed", "", None, "gdelt", "gdelt")
                | {"error": str(e)[:200]}]
    from source_ingest import ingest_url  # body extraction (newspaper3k)
    out: list[dict] = []
    for a in arts[:max_items]:
        url = a.get("url")
        if not url:
            continue
        rec = ingest_url(url)
        rec["provenance"] = "gdelt"
        if not rec.get("title"):
            rec["title"] = a.get("title", "")
        if a.get("seendate"):
            rec["published_date"] = a["seendate"]
        if rec.get("text"):
            out.append(rec)
    return out


# --- OpenSanctions (sanctions / PEP; needs API key) ------------------------

_OPENSANCTIONS = "https://api.opensanctions.org/search/default"


def _opensanctions_key() -> str | None:
    return os.environ.get("INVESTIGATOR_OPENSANCTIONS_KEY")


def fetch_opensanctions(query: str, max_items: int) -> list[dict]:
    key = _opensanctions_key()
    if not key:
        return []   # key-gated: skip cleanly (UI marks it unavailable)
    try:
        r = requests.get(_OPENSANCTIONS, timeout=_TIMEOUT,
                         headers={**_UA, "Authorization": f"ApiKey {key}"},
                         params={"q": query, "limit": max_items})
        results = r.json().get("results", [])
    except Exception as e:  # noqa: BLE001
        return [_record("opensanctions search failed", "", None,
                        "opensanctions", "opensanctions") | {"error": str(e)[:200]}]
    out: list[dict] = []
    for res in results[:max_items]:
        props = res.get("properties") or {}
        def _vals(k):
            v = props.get(k) or []
            return ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)
        caption = res.get("caption") or "(unnamed)"
        lines = [f"Name: {caption}",
                 f"Type: {res.get('schema', '')}",
                 f"Datasets: {', '.join(res.get('datasets', []))}",
                 f"Topics: {_vals('topics')}",
                 f"Aliases: {_vals('alias')}",
                 f"Countries: {_vals('country')}",
                 f"Sanctions/notes: {_vals('notes')}"]
        url = f"https://www.opensanctions.org/entities/{res.get('id', '')}/"
        out.append(_record(f"OpenSanctions: {caption}", "\n".join(lines), url,
                           "opensanctions.org", "opensanctions"))
    return out


# --- Generic web search (Google CSE w/ key, else DuckDuckGo) ---------------

def _websearch_urls(query: str, max_items: int) -> list[str]:
    api_key = os.environ.get("INVESTIGATOR_GOOGLE_API_KEY")
    cse_id = os.environ.get("INVESTIGATOR_GOOGLE_CSE_ID")
    if api_key and cse_id:
        try:
            r = requests.get("https://www.googleapis.com/customsearch/v1",
                             timeout=_TIMEOUT, params={
                                 "key": api_key, "cx": cse_id, "q": query,
                                 "num": min(10, max_items)})
            return [it["link"] for it in r.json().get("items", [])][:max_items]
        except Exception:  # noqa: BLE001 -- fall through to DDG
            pass
    # DuckDuckGo HTML (no key, best-effort).
    try:
        from bs4 import BeautifulSoup
        r = requests.post("https://html.duckduckgo.com/html/", headers=_UA,
                          timeout=_TIMEOUT, data={"q": query})
        soup = BeautifulSoup(r.text, "html.parser")
        urls: list[str] = []
        for a in soup.select("a.result__a"):
            href = a.get("href") or ""
            # DDG wraps targets as /l/?uddg=<encoded>
            if "uddg=" in href:
                import urllib.parse
                href = urllib.parse.unquote(href.split("uddg=", 1)[1].split("&", 1)[0])
            if href.startswith("http"):
                urls.append(href)
            if len(urls) >= max_items:
                break
        return urls
    except Exception:  # noqa: BLE001
        return []


def fetch_websearch(query: str, max_items: int) -> list[dict]:
    from source_ingest import ingest_url
    out: list[dict] = []
    for url in _websearch_urls(query, max_items):
        rec = ingest_url(url)
        rec["provenance"] = "websearch"
        if rec.get("text"):
            out.append(rec)
    return out


# --- Registry --------------------------------------------------------------

SEARCH_SOURCES: list[dict] = [
    {"id": "wikipedia", "label": "Wikipedia",
     "description": "Encyclopedic articles about the subject (MediaWiki API).",
     "requires_key": False, "fetch": fetch_wikipedia},
    {"id": "gdelt", "label": "GDELT news",
     "description": "Global news coverage (broader than Google News).",
     "requires_key": False, "fetch": fetch_gdelt},
    {"id": "opensanctions", "label": "OpenSanctions",
     "description": "Sanctions / PEP / watchlist entries. Needs an API key.",
     "requires_key": True, "fetch": fetch_opensanctions,
     "available": lambda: bool(_opensanctions_key())},
    {"id": "websearch", "label": "Web search",
     "description": "Generic web search (Google CSE if configured, else DuckDuckGo).",
     "requires_key": False, "fetch": fetch_websearch},
]

_BY_ID = {s["id"]: s for s in SEARCH_SOURCES}


def available_sources() -> list[dict]:
    """Source descriptors for the UI: id, label, description, requiresKey,
    available (a key-gated source is unavailable until its key is set)."""
    out = []
    for s in SEARCH_SOURCES:
        avail = s["available"]() if callable(s.get("available")) else True
        out.append({"id": s["id"], "label": s["label"], "description": s["description"],
                    "requiresKey": s["requires_key"], "available": avail})
    return out


def fetch_sources(ids: list[str], query: str, max_items: int = 10,
                  verbose: bool = False) -> list[dict]:
    """Fetch and merge usable text records from each enabled provider."""
    records: list[dict] = []
    for sid in dict.fromkeys(ids):
        prov = _BY_ID.get(sid)
        if not prov:
            continue
        if callable(prov.get("available")) and not prov["available"]():
            if verbose:
                print(f"     [{sid}] skipped (not configured)")
            continue
        try:
            recs = prov["fetch"](query, max_items)
        except Exception as e:  # noqa: BLE001 -- a provider never breaks the run
            if verbose:
                print(f"     [{sid}] failed: {type(e).__name__}: {e}")
            continue
        usable = [r for r in recs if r.get("text")]
        if verbose:
            print(f"     [{sid}] {len(usable)} usable record(s)")
        records.extend(usable)
    return records


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "Benjamin Netanyahu corruption"
    ids = sys.argv[2].split(",") if len(sys.argv) > 2 else ["wikipedia"]
    recs = fetch_sources(ids, q, max_items=3, verbose=True)
    print(f"\n{len(recs)} records:")
    for r in recs:
        print(f"  [{r['provenance']}] {r['title'][:70]}  ({len(r['text'])} chars)  {r['real_url']}")
