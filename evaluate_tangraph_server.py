"""GNews-driven OSINT investigation runner for OSINTGraph.

Takes an investigation query, fetches recent news articles via GNews, follows
the Google News redirects to the real publisher URLs, extracts article body
text with newspaper3k, builds the JSON payload the pipeline expects, POSTs to
the running OSINTGraph server, and saves the enriched response (Phase 0-3:
entities, edges, themes, promoted entities, hypothesis edges) for review.

Why news instead of hand-curated dossiers: the OSINT pipeline already handles
people / organisations / financial relationships, so news articles fit the
existing domain (no domain-adaptive prompt scaffolding required). GNews gives
us unlimited free queries refreshable on demand, so we can validate the
network analysis on many investigations and share the artefacts.

Usage:
    PYTHONPATH=.:src:/home/dsivov/Work/tangos_mvp /home/dsivov/.conda/envs/tangos/bin/python \\
      evaluate_tangraph_server.py --query "Wagner Group Russia" --max-articles 15 --period 30d

Output: debug_output/news_investigations/<slug>_<timestamp>.json
        Contains the input articles + the full OSINTGraph response (with Phase-3
        themes / promoted_entities / hypothesis_edges sections).

Requires the OSINTGraph server to be running on 127.0.0.1:5003 with
TANGRAPH_TMFG=1 set (so Phase 1-3 network analysis is included).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests
from gnews import GNews
from googlenewsdecoder import gnewsdecoder
from newspaper import Article

DEFAULT_HYPOTHESIS = (
    "Identify entities -- organisations and individuals -- and the relationships among them "
    "that appear in the source articles, surfacing any material connections, transactions, "
    "or affiliations relevant to the investigation query."
)

# News outlets / encyclopedias / aggregators -- entities that are the SOURCE
# of an article, not the SUBJECT. The LLM tends to extract them anyway because
# they're heavily cited in the body. We drop them from the report.
#
# IMPORTANT: this list excludes *think tanks* (RAND, ISW, CSIS, CFR, Brookings,
# Atlantic Council, Africa Center, etc.) -- they often publish their own
# research articles on a topic, but they are also legitimately INVESTIGATIVE
# SUBJECTS in OSINT analyses. Trying to use the dynamic GNews publisher set
# accidentally filters all of them out (see commit history); the static list
# below is the safer default.
STATIC_PUBLISHERS_DROP = {
    # Major US/UK newspapers + magazines
    "WSJ", "WALL STREET JOURNAL", "NYT", "NY TIMES", "THE NEW YORK TIMES",
    "WASHINGTON POST", "WAPO", "BOSTON GLOBE", "LOS ANGELES TIMES", "USA TODAY",
    "THE GUARDIAN", "FINANCIAL TIMES", "THE TIMES",
    "THE NEW YORKER", "THE ATLANTIC", "TIME", "TIME MAGAZINE", "WIRED",
    "FORBES", "FORTUNE", "THE ECONOMIST",
    # Wire services + broadcast
    "REUTERS", "AP", "ASSOCIATED PRESS", "AFP", "BLOOMBERG", "DOW JONES",
    "CNN", "BBC", "BBC NEWS", "AL JAZEERA", "FOX NEWS", "MSNBC",
    "ABC NEWS", "CBS NEWS", "NBC NEWS", "NPR", "PBS", "C-SPAN",
    # Aggregators / web pubs
    "BUSINESS INSIDER", "POLITICO", "AXIOS", "THE HILL", "MSN",
    "FOREIGN POLICY", "VOX", "SLATE", "BUZZFEED", "QUARTZ",
    "THE DAILY BEAST", "THE DISPATCH", "HUFFPOST", "THE INTERCEPT",
    # Encyclopedias / reference
    "BRITANNICA", "ENCYCLOPEDIA BRITANNICA", "WIKIPEDIA",
    # International + regional + state outlets
    "DEUTSCHE WELLE", "DW",
    "LE MONDE", "FRANCE 24", "EL PAÍS",
    "IRAN INTERNATIONAL", "IRANINTL",
    "RFE/RL", "RADIO FREE EUROPE", "VOA", "VOICE OF AMERICA",
    "EURONEWS", "POLITICO EUROPE",
    "RT", "TASS", "SPUTNIK",
    "UNITED24 MEDIA",
    "УКРАЇНСЬКА ПРАВДА", "УНН", "УКРАЇНСЬКІ НАЦІОНАЛЬНІ НОВИНИ",
}


def _id_tokens(s: str) -> set:
    """Uppercase alphanumeric tokens; used for fuzzy publisher matching."""
    return set(re.findall(r"[A-ZА-ЯЇЄІҐ0-9]+", (s or "").upper()))


def is_publisher(ident: str) -> bool:
    """A node is a publisher if its identifier matches the static drop list.

    Matching rules:
      * exact (case-insensitive) -- covers WSJ <-> WSJ
      * token-set subset in EITHER direction AND the smaller set has >= 2
        tokens -- covers "BUSINESS INSIDER" <-> "BUSINESS INSIDER (US)" but
        prevents single-token false-positives like "IRAN" matching
        "IRAN INTERNATIONAL".
    """
    if not ident:
        return False
    u = ident.upper().strip()
    ut = _id_tokens(ident)
    if not ut:
        return False
    for c in STATIC_PUBLISHERS_DROP:
        cu = c.upper().strip()
        if cu == u:
            return True
        ct = _id_tokens(c)
        if not ct:
            continue
        if ct <= ut and len(ct) >= 2:
            return True
        if ut <= ct and len(ut) >= 2:
            return True
    return False


_HTTP_DATE_RE = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+\d{1,2}\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}",
    re.IGNORECASE,
)
_PUBLICATION_EVENT_PREFIXES = (
    "published article", "publication date", "article published",
    "reported by", "as reported", "reporting on", "this article",
    "news article", "news story", "publication of",
)


def _is_publication_metadata_event(ev) -> bool:
    """True if this timeline_events entry is article publication metadata
    rather than a factual dated event in the article's content.

    Two signals:
      * date is in HTTP / RFC format (only ever comes from GNews
        publication metadata, never from natural-language body extraction);
      * event text starts with a publication phrase (e.g. "Published article
        on ...", "As reported by ...").
    """
    if not isinstance(ev, dict):
        return False
    date = (ev.get("date") or "").strip()
    text = (ev.get("event") or "").strip().lower()
    if _HTTP_DATE_RE.match(date):
        return True
    if any(text.startswith(p) for p in _PUBLICATION_EVENT_PREFIXES):
        return True
    return False


def filter_timeline_events(response: dict) -> dict:
    """Drop publication-metadata `timeline_events` entries from every entity.

    Background: when news articles are fed into NER, the LLM tends to record
    each article's HTTP publication date as a "timeline event" for entities
    mentioned in it. That's article metadata, not a fact about the entity, and
    it crowds out the actual dated events in the article body (e.g. 'In
    April 2025 …'). This pass removes the metadata entries while keeping
    substantive timeline lines untouched.
    """
    dropped = 0
    kept = 0
    sample_dropped: list[str] = []
    for n in response.get("nodes", []):
        data = n.get("data") or {}
        tl = data.get("timeline_events") or []
        if not tl:
            continue
        kept_events = []
        for ev in tl:
            if _is_publication_metadata_event(ev):
                dropped += 1
                if len(sample_dropped) < 5 and isinstance(ev, dict):
                    sample_dropped.append(f"{ev.get('date','?')} -- {(ev.get('event') or '')[:90]}")
            else:
                kept_events.append(ev)
                kept += 1
        if len(kept_events) != len(tl):
            data["timeline_events"] = kept_events
            n["data"] = data
    response["_timeline_filter"] = {"dropped": dropped, "kept": kept, "sample_dropped": sample_dropped}
    return response


def filter_publishers(response: dict, articles: list[dict]) -> dict:
    """Drop publisher entities from the response, plus any edges / themes /
    promoted / hypothesis_edges that touch them. Returns the filtered response
    with a `_publisher_filter` summary explaining what was removed.

    Uses STATIC_PUBLISHERS_DROP (news outlets only). Think tanks survive --
    they're sometimes the article SOURCE but they're also legitimate
    investigative subjects, so dropping them blindly removes real signal.
    """
    publisher_ids = {
        n["identifier"] for n in response.get("nodes", [])
        if is_publisher(n["identifier"])
    }
    if not publisher_ids:
        response["_publisher_filter"] = {"dropped": [], "summary": "no publishers detected"}
        return response

    nodes_before = len(response.get("nodes", []))
    edges_before = len(response.get("edges", []))
    themes_before = len(response.get("themes", []))
    promoted_before = len(response.get("promoted_entities", []))
    hyp_before = len(response.get("hypothesis_edges", []))

    response["nodes"] = [n for n in response.get("nodes", []) if n["identifier"] not in publisher_ids]
    response["edges"] = [
        e for e in response.get("edges", [])
        if e.get("src_identifier") not in publisher_ids and e.get("dst_identifier") not in publisher_ids
    ]
    response["themes"] = [
        t for t in response.get("themes", [])
        if not (set(t.get("members", [])) & publisher_ids)
    ]
    response["promoted_entities"] = [
        p for p in response.get("promoted_entities", []) if p["identifier"] not in publisher_ids
    ]
    response["hypothesis_edges"] = [
        h for h in response.get("hypothesis_edges", [])
        if not (set(h.get("endpoints", [])) & publisher_ids)
    ]

    response["_publisher_filter"] = {
        "dropped": sorted(publisher_ids),
        "before": {"nodes": nodes_before, "edges": edges_before,
                   "themes": themes_before, "promoted": promoted_before,
                   "hypothesis_edges": hyp_before},
        "after":  {"nodes": len(response["nodes"]),
                   "edges": len(response["edges"]),
                   "themes": len(response["themes"]),
                   "promoted": len(response["promoted_entities"]),
                   "hypothesis_edges": len(response["hypothesis_edges"])},
    }
    return response


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", s).strip("_").lower()[:60]


def fetch_news(query: str, *, max_articles: int = 15, period: str = "30d",
                language: str = "en", country: str = "US") -> list[dict]:
    """Search Google News for `query`, decode redirects, extract article body
    text. Returns a list of dicts (title, publisher, url, real_url, published_date,
    text, error). Articles whose body fails to extract are kept with an
    `error` field so the report can show what didn't load."""
    gn = GNews(max_results=max_articles, period=period, country=country, language=language)
    raw = gn.get_news(query)
    out = []
    for a in raw:
        pub = a.get("publisher") or {}
        pub_title = pub.get("title") if isinstance(pub, dict) else (pub or "")
        rec = {
            "title": (a.get("title") or "").strip(),
            "publisher": pub_title,
            "google_url": a.get("url"),
            "real_url": None,
            "published_date": a.get("published date"),
            "text": "",
            "error": None,
        }
        # Decode Google News redirect to the publisher URL.
        try:
            r = gnewsdecoder(a["url"], interval=1)
            if not r.get("status"):
                rec["error"] = f"redirect-decode: {r}"
                out.append(rec); continue
            rec["real_url"] = r["decoded_url"]
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"redirect-decode: {type(e).__name__}: {e}"
            out.append(rec); continue
        # Extract article body.
        try:
            art = Article(rec["real_url"])
            art.download()
            art.parse()
            rec["text"] = (art.text or "").strip()
            if not rec["text"]:
                rec["error"] = "empty body (extraction returned 0 chars)"
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"extract: {type(e).__name__}: {str(e)[:200]}"
        out.append(rec)
        time.sleep(0.5)   # be polite to publishers
    return out


def build_payload(query: str, articles: list[dict],
                  *, per_article_query: str | None = None) -> dict:
    """Shape the articles into the dict the pipeline's chunker eats.

    Shape mirrors the original OSINT-fixture pattern (`{<subject>: {<source_id>:
    {query: ..., title: ..., text: ...}, ...}}`): the outer key is the
    investigation subject; each inner entry is a self-contained article record
    carrying an inline `query` field. When the chunker walks the JSON, every
    chunk preserves the seed query (as the outer path component) AND the
    article-specific query (as an inline field), so the LLM gets the framing
    "this article was found while investigating <entity>" per chunk.

    `per_article_query` controls the inline query stamped on each article.
    Defaults to the seed `query` -- so Stage-1 articles inherit the seed
    framing. Stage-2 callers override per-batch with the entity that drove
    the GNews fetch.
    """
    body = {}
    inline_q = per_article_query or query
    for i, a in enumerate(articles):
        title = (a.get("title") or "").strip()
        text = a.get("text") or ""
        # Skip records that have neither body nor headline -- nothing to feed.
        if not text and not title:
            continue
        # Title-only fallback. Body extraction fails ~20-30% of the time
        # (403 blocks on FT/Economist/Axios, paywalls, redirect loops). The
        # headline alone still carries entity signal ("Iran supplies Russia
        # with fiber-optic FPV drones" -> IRAN, RUSSIA, supply-edge), so
        # rather than dropping the article we synthesise a headline-only
        # chunk and flag it. Downstream code can choose to de-rate
        # relations attested only by headline-only chunks.
        body_available = bool(text)
        if not body_available:
            err = (a.get("error") or "").strip() or "body extraction failed"
            text = (
                "[HEADLINE ONLY -- full article body could not be retrieved "
                f"({err}). Treat as a single-sentence news lead, not a full "
                "article: extract only what is explicitly stated in the title.]\n"
                f"Title: {title}\n"
                f"Publisher: {a.get('publisher') or 'unknown'}\n"
                f"Date: {a.get('published_date') or 'unknown'}"
            )
        # Stable per-article key so chunking is deterministic.
        key = f"{a['publisher'] or 'unknown'}__{i}"
        body[key] = {
            "query": inline_q,
            "title": title,
            "publisher": a["publisher"],
            "url": a["real_url"],
            "published_date": a["published_date"],
            "text": text,
            "body_available": body_available,
        }
    return {query: body}


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--query", required=True, help="Investigation query (Google News search string).")
    p.add_argument("--max-articles", type=int, default=15)
    p.add_argument("--period", default="30d", help="Recency window: e.g. 7d, 30d, 12m.")
    p.add_argument("--language", default="en")
    p.add_argument("--country", default="US")
    p.add_argument("--domain", default="terror_financing",
                   help="Pipeline domain hint (terror_financing / general / ...).")
    p.add_argument("--hypothesis", default=DEFAULT_HYPOTHESIS)
    p.add_argument("--base-url", default="http://127.0.0.1:5003/api/v1")
    p.add_argument("--output-dir", default="debug_output/news_investigations")
    args = p.parse_args()

    print(f"\n[1/3] Fetching news for query={args.query!r}  max={args.max_articles}  period={args.period}")
    articles = fetch_news(args.query, max_articles=args.max_articles, period=args.period,
                          language=args.language, country=args.country)
    n_ok = sum(1 for a in articles if a["text"])
    n_fail = len(articles) - n_ok
    print(f"      extracted {n_ok} articles ({sum(len(a['text']) for a in articles):,} chars total); {n_fail} failed")
    for a in articles:
        marker = " " if a["text"] else "X"
        print(f"        [{marker}] {a['publisher'] or '?':30s} {a['title'][:70]!r}")
        if a["error"]:
            print(f"              -> {a['error']}")

    if n_ok == 0:
        print("\nNo articles extracted; aborting.")
        return 1

    print(f"\n[2/3] POSTing to {args.base_url}/get_nodes  domain={args.domain!r}")
    payload = {
        "session_id": str(uuid.uuid4()),
        "text": json.dumps(build_payload(args.query, articles)),
        "query": args.query,
        "hypotests": args.hypothesis,
        "domain": args.domain,
        "use_regular_triangulation": False,
        "relevance_threshold": 0.6,
    }
    t0 = time.time()
    r = requests.post(f"{args.base_url}/get_nodes", json=payload, timeout=900)
    r.raise_for_status()
    response = r.json()
    print(f"      response: status={response.get('status')}  nodes={len(response.get('nodes', []))}  edges={len(response.get('edges', []))}  ({time.time()-t0:.1f}s)")

    network = {k: response.get(k) for k in ("themes", "promoted_entities", "hypothesis_edges") if k in response}
    if network:
        print(f"      network analysis (raw): themes={len(network.get('themes',[]))}  "
              f"promoted={len(network.get('promoted_entities',[]))}  "
              f"hypothesis_edges={len(network.get('hypothesis_edges',[]))}")
    else:
        print("      network analysis: (none -- is TANGRAPH_TMFG=1 set on the server?)")

    # --- Publisher noise filter -----------------------------------------
    # The OSINT NER prompts often surface news outlets (WSJ, BBC, Business
    # Insider, ...) as entities because they're heavily cited in the text.
    # Drop them so the report focuses on subjects, not sources.
    response = filter_publishers(response, articles)
    pf = response.get("_publisher_filter", {})
    if pf.get("dropped"):
        b, a = pf["before"], pf["after"]
        print(f"      publisher filter: dropped {len(pf['dropped'])} publishers "
              f"({', '.join(pf['dropped'][:5])}{'…' if len(pf['dropped'])>5 else ''})")
        print(f"        nodes {b['nodes']}->{a['nodes']}  edges {b['edges']}->{a['edges']}  "
              f"themes {b['themes']}->{a['themes']}  promoted {b['promoted']}->{a['promoted']}  "
              f"hypothesis {b['hypothesis_edges']}->{a['hypothesis_edges']}")
    else:
        print(f"      publisher filter: nothing matched")

    # --- Timeline metadata filter ---------------------------------------
    # Drop article-publication-date entries from each entity's
    # timeline_events; keep substantive dated events from article bodies.
    response = filter_timeline_events(response)
    tf = response.get("_timeline_filter", {})
    if tf.get("dropped"):
        print(f"      timeline filter: dropped {tf['dropped']} publication-metadata entries; {tf['kept']} substantive events kept")
        for s in tf.get("sample_dropped", [])[:3]:
            print(f"        e.g. {s}")
    else:
        print(f"      timeline filter: nothing matched")

    print(f"\n[3/3] Saving to {args.output_dir}/")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slug(args.query)
    out_path = out_dir / f"{slug}_{ts}.json"
    out_path.write_text(json.dumps({
        "query": args.query,
        "fetched_at": ts,
        "params": {k: v for k, v in vars(args).items() if k != "hypothesis"},
        "articles": articles,            # input articles (with failures noted)
        "response": response,            # full pipeline response inc. Phase-3 sections
    }, indent=2, ensure_ascii=False))
    print(f"      wrote: {out_path}  ({out_path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
