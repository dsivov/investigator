"""Customer-facing OSINT report generator.

Takes a cross-event investigation JSON artifact and produces a
standard analyst-grade markdown report intended for a paying client.
Distinct from osint_analyst_review.py, which is an internal
data-quality artifact for the development team.

Voice and structure:
  - Third-person, formal analyst voice (no first-person plural, no
    development jargon).
  - Confidence language follows ICD-203 (Almost Certain / Highly
    Likely / Likely / Even Chance / Unlikely / Very Unlikely).
  - All claims footnote to source URLs from the underlying corpus.
  - No mention of TMFG, junction trees, dspy, or other internal
    methodology terms in the body. A short plain-language note on
    methodology sits at the end.

Usage:
    PYTHONPATH=.:src \\
      /home/dsivov/.conda/envs/tangos/bin/python research/build_customer_report.py \\
      news_investigations/cross_event/<artifact>.json
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Confidence language (ICD-203)
# ---------------------------------------------------------------------------

def _as_text(v) -> str:
    """Coerce a possibly-list/None field to a clean string. Field-merging
    across chunks can turn a scalar (event_type, location, …) into a list."""
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        parts = [str(x).strip() for x in v if x is not None and str(x).strip()]
        return ", ".join(dict.fromkeys(parts))
    return str(v).strip()


def _confidence_label(posterior: float) -> str:
    p = float(posterior or 0.0)
    if p >= 0.93: return "Almost certain"
    if p >= 0.80: return "Highly likely"
    if p >= 0.65: return "Likely"
    if p >= 0.45: return "Even chance"
    if p >= 0.25: return "Unlikely"
    if p >= 0.07: return "Very unlikely"
    return "Almost no chance"


def _bridge_confidence(b: dict, n_runs_total: int) -> str:
    """Customer-facing confidence for a bridging-entity finding.

    The raw posterior_prob on a bridge node sits at 0.50 by construction
    of the propagation (it carries no per-article truth claim, only a
    structural co-occurrence prior). Reporting that as 'Even chance' to
    a client is misleading -- an actor attested in three independent
    storylines by 60+ articles each is a strong structural finding.

    We derive a label from two structural quantities the client can
    interpret: how many of the threads the actor appears in, and how
    many distinct articles in the corpus attest it.
    """
    n_runs = len(b.get("runs") or [])
    score = float(b.get("score") or 0.0)  # corroboration-weighted structural score
    if n_runs >= n_runs_total and score >= 0.50:
        return "Almost certain"
    if n_runs >= n_runs_total:
        return "Highly likely"
    if n_runs >= 2 and score >= 0.40:
        return "Likely"
    if n_runs >= 2:
        return "Even chance"
    return "Unlikely"


def _is_country_like(node: dict) -> bool:
    """Heuristic: countries/GPEs get polluted by data fields meant for
    persons/orgs (position='President', etc.) because surface-form merges
    pull them in. Skip those fields when the node looks like a place."""
    ident = (node.get("identifier") or "").upper()
    # Short all-caps single words that are common country/region tokens.
    return (len(ident.split()) <= 3 and
            any(tok in {"CHINA", "RUSSIA", "IRAN", "UKRAINE", "TURKEY",
                        "INDIA", "EUROPE", "EU", "UNITED", "STATES", "USA",
                        "AMERICA", "BRITAIN", "FRANCE", "GERMANY", "JAPAN",
                        "KOREA", "ISRAEL", "PALESTINE", "LEBANON", "YEMEN",
                        "SAUDI", "ARABIA", "EGYPT", "SYRIA", "IRAQ",
                        "KAZAKHSTAN", "AZERBAIJAN", "CASPIAN", "STRAIT",
                        "HORMUZ", "MOSCOW", "BEIJING", "TEHRAN", "WASHINGTON"}
                for tok in ident.split()))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _humanise_run(run_id: str) -> str:
    """Turn run_id like 'russia_oil_darkfleet' into 'Russian-flagged dark-fleet oil shipments'."""
    return " ".join(w.capitalize() for w in (run_id or "").replace("_", " ").split())


def _publisher_of(url: str) -> str:
    if not url:
        return "unknown"
    try:
        host = urlparse(url).netloc.lower()
        host = host.removeprefix("www.")
        return host or "unknown"
    except Exception:
        return "unknown"


def _ref_id(artifact_path: Path) -> str:
    h = hashlib.sha256(str(artifact_path.name).encode()).hexdigest()[:6].upper()
    yyyymm = datetime.now().strftime("%Y%m")
    return f"TG-{yyyymm}-{h}"


def _date_iso(s: str) -> str | None:
    """Best-effort ISO-date parse from per-article 'published_date' strings."""
    if not s:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S GMT", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except Exception:
            continue
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(s))
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Source registry: every URL referenced gets a footnote number
# ---------------------------------------------------------------------------

class SourceRegistry:
    def __init__(self):
        self._by_url: dict[str, int] = {}
        self._order: list[tuple[int, str, str]] = []   # (number, url, publisher)

    def cite(self, url: str | None) -> str:
        """Return the footnote marker [N] for a URL, registering if new."""
        if not url or not isinstance(url, str):
            return ""
        url = url.strip()
        if not url.startswith("http"):
            return ""
        n = self._by_url.get(url)
        if n is None:
            n = len(self._order) + 1
            self._by_url[url] = n
            self._order.append((n, url, _publisher_of(url)))
        return f"[{n}]"

    def bibliography_md(self) -> str:
        lines = []
        by_pub: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for n, url, pub in self._order:
            by_pub[pub].append((n, url))
        for pub in sorted(by_pub):
            lines.append(f"**{pub}**")
            for n, url in sorted(by_pub[pub]):
                lines.append(f"  - [{n}] {url}")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _executive_summary(d: dict, sr: SourceRegistry, domain_label: str,
                       run_labels: list[str]) -> str:
    final = d["final_merged_graph"]
    bridges = final.get("bridging_entities", []) or []
    leads = final.get("cross_event_leads", []) or []
    cross_themes = [t for t in final.get("themes", []) or [] if t.get("is_cross_investigation")]

    n_runs = len(d.get("events", []))
    all_run_bridges = [b for b in bridges if len(b.get("runs") or []) >= n_runs]
    other_bridges = [b for b in bridges if len(b.get("runs") or []) < n_runs]

    lines = []
    lines.append("## Executive Summary\n")

    # Opening paragraph
    storylines = ", ".join(f'_"{l}"_' for l in run_labels)
    lines.append(
        f"This report consolidates open-source reporting across three "
        f"independent news investigations into {storylines}. The objective "
        f"is to identify entities, relationships, and incidents that "
        f"appear in **more than one** of the three investigative threads, "
        f"on the working hypothesis that recurring actors across "
        f"independent storylines indicate a structurally connected "
        f"underlying network rather than a coincidence of coverage.\n"
    )

    # Per-thread node counts so we can describe coverage asymmetry honestly.
    nodes = final["nodes"]
    nodes_per_run = {ev["name"]: sum(1 for n in nodes
                                       if ev["name"] in (n.get("runs") or []))
                     for ev in d.get("events", []) if isinstance(ev, dict)}
    if nodes_per_run:
        sparse = [n for n, c in nodes_per_run.items() if c <= 5]
        rich = [n for n, c in nodes_per_run.items() if c >= 20]
    else:
        sparse, rich = [], []

    # Headline finding
    if all_run_bridges:
        actor_list = ", ".join(f"**{b['identifier']}**" for b in all_run_bridges[:5])
        lines.append(
            f"The analysis identifies {len(all_run_bridges)} actors "
            f"({actor_list}) attested in **all three** investigative "
            f"threads with high structural confidence. A further "
            f"{len(other_bridges)} actors bridge two of the three threads. "
            f"In total {len(cross_themes)} cross-thread narrative themes "
            f"and {len(leads)} ranked cross-thread investigative leads "
            f"were extracted.\n"
        )
    elif other_bridges:
        actor_list = ", ".join(f"**{b['identifier']}**" for b in other_bridges[:5])
        lines.append(
            f"**No actor appeared in all three investigative threads.** "
            f"{len(other_bridges)} actors ({actor_list}) bridged two of "
            f"the three threads -- the strongest cross-thread structure "
            f"the corpus supports.\n"
        )
        if sparse and rich:
            sparse_lbl = ", ".join(f"`{n}`" for n in sparse)
            rich_lbl = ", ".join(f"`{n}`" for n in rich)
            lines.append(
                f"This finding is partly driven by an **asymmetric corpus**: "
                f"thread(s) {sparse_lbl} returned very limited material "
                f"(five or fewer extracted entities) over the analysis "
                f"window, while {rich_lbl} returned a rich corpus. Where a "
                f"working hypothesis assumes a connection that depends on "
                f"the sparse thread, the absence of cross-thread bridges "
                f"is **not** evidence that the connection does not exist; "
                f"it is evidence that the public news corpus does not "
                f"directly attest it. An extended timeframe, additional "
                f"named queries on the sparse thread, or non-news sourcing "
                f"is the appropriate next step.\n"
            )
        else:
            lines.append(
                f"The corpus was sparser than comparable investigations on "
                f"related topics; this report should be read as a baseline "
                f"assessment that warrants extension if additional sourcing "
                f"becomes available.\n"
            )

    # Key Findings list
    lines.append("### Key Findings\n")
    for i, b in enumerate(all_run_bridges[:5] + other_bridges[:max(0, 5 - len(all_run_bridges))], 1):
        ident = b["identifier"]
        n_b = len(b.get("runs") or [])
        conf = _bridge_confidence(b, n_runs)
        scope = "all three" if n_b >= n_runs else f"{n_b} of {n_runs}"
        lines.append(
            f"{i}. **{ident}** is attested across {scope} investigative "
            f"threads. Confidence in this structural finding: _{conf}_."
        )
    lines.append("")
    lines.append("---\n")
    return "\n".join(lines)


def _humanise_period(period: str) -> str:
    """Turn a GNews period code like '1y' / '30d' / '6m' into a phrase."""
    if not period:
        return "rolling 30-day window"
    s = str(period).strip().lower()
    m = re.match(r"^(\d+)([dmy])$", s)
    if not m:
        return f"the window covered by `{period}`"
    n, unit = int(m.group(1)), m.group(2)
    name = {"d": "day", "m": "month", "y": "year"}[unit]
    if n != 1:
        name += "s"
    return f"rolling {n}-{name.rstrip('s')} window" if n == 1 else f"rolling {n}-{name} window"


def _scope_section(d: dict, sr: SourceRegistry, domain_label: str,
                   run_labels: list[str]) -> str:
    fetched = sum(len(b) for s in d["per_event_states"] for b in s.get("article_batches", []))
    body_ok = sum(1 for s in d["per_event_states"] for b in s.get("article_batches", [])
                  for a in b if a.get("text"))
    headline_only = sum(1 for s in d["per_event_states"] for b in s.get("article_batches", [])
                        for a in b if not a.get("text") and (a.get("title") or "").strip())
    publishers = sorted({a.get("publisher") for s in d["per_event_states"]
                         for b in s.get("article_batches", []) for a in b
                         if a.get("publisher")})
    period_phrase = _humanise_period((d.get("params") or {}).get("period"))

    lines = []
    lines.append("## 1. Scope and Methodology\n")
    lines.append("### Investigative threads\n")
    for r, lbl in zip(d.get("events", []), run_labels):
        tag = r.get("name") if isinstance(r, dict) else str(r)
        lines.append(f"- **{lbl}** (working tag: `{tag}`)")
    lines.append("")
    lines.append(
        f"### Source corpus\n\n"
        f"- **{fetched}** news articles were retrieved across the three "
        f"threads from the Google News aggregator, covering the "
        f"{period_phrase} preceding the analysis date.\n"
        f"- **{body_ok}** articles were ingested with full body text; "
        f"**{headline_only}** additional articles were ingested at "
        f"headline-only granularity because the publisher's site could "
        f"not be retrieved (paywalls, anti-scraping controls, or "
        f"unavailable hosts). Headline-only items contribute identifier "
        f"and date evidence only, and are weighted accordingly.\n"
        f"- Distinct publishers contributing to the corpus: **{len(publishers)}**.\n"
    )
    return "\n".join(lines)


def _edge_context(edge: dict) -> tuple[str, str]:
    """Return (relation_type, context_sentence) parsed from an edge."""
    rel = edge.get("relations")
    if isinstance(rel, str):
        try:
            rel = json.loads(rel)
        except Exception:
            rel = {}
    if not isinstance(rel, dict):
        rel = {}
    return (rel.get("type") or "unknown", (rel.get("context") or "").strip())


def _edge_source_url(edge: dict) -> str:
    """Pull the best URL we can from an edge: source if it's a URL, then
    attributes.source_url, then search_url."""
    src = edge.get("source")
    if isinstance(src, str) and src.startswith("http"):
        return src
    attrs = edge.get("attributes") or {}
    for k in ("source_url", "url"):
        v = attrs.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    return edge.get("search_url") if (edge.get("search_url") or "").startswith("http") else ""


def _key_entities(d: dict, sr: SourceRegistry) -> str:
    final = d["final_merged_graph"]
    nodes_by_id = {n["identifier"]: n for n in final["nodes"]}
    bridges = final.get("bridging_entities", []) or []
    edges = final["edges"]
    n_runs_total = len(d.get("events", []))

    # Index edges by either endpoint, type-filtered to those with real semantics.
    edges_by_node: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        if e.get("type") not in ("affiliation",):   # event_participation is noisy here
            continue
        s = e.get("src_identifier"); t = e.get("dst_identifier")
        if s: edges_by_node[s].append(e)
        if t: edges_by_node[t].append(e)

    lines = []
    lines.append("\n## 2. Key Entities\n")
    if not bridges:
        lines.append(
            "No entity was attested across multiple investigative threads "
            "with sufficient corroboration to warrant a profile in this "
            "section.\n"
        )
        return "\n".join(lines)

    lines.append(
        "Each profiled entity below was attested in at least two of "
        f"the {n_runs_total} investigative threads. Profiles are ordered "
        "by the number of threads the entity bridged and by corroboration "
        "weight within each tier. Source citations refer to the "
        "References section.\n"
    )

    for b in bridges:
        ident = b["identifier"]
        node = nodes_by_id.get(ident)
        if not node:
            continue
        n_b = len(b.get("runs") or [])
        conf = _bridge_confidence(b, n_runs_total)
        evidence_count = node.get("evidence_count") or 0

        lines.append(f"### {ident}\n")
        scope_phrase = ("all " + str(n_runs_total)) if n_b >= n_runs_total else f"{n_b} of {n_runs_total}"
        lines.append(f"_Attested in {scope_phrase} thread(s). "
                     f"Structural confidence: **{conf}**. {evidence_count} attesting article(s)._\n")

        # Other names / labels
        raw_labels = node.get("labels") or []
        if isinstance(raw_labels, str):
            raw_labels = [raw_labels]
        labels = []
        for lab in raw_labels:
            if isinstance(lab, list) and lab:
                lab = lab[0]
            lab = str(lab).strip()
            if lab and lab.upper() != ident.upper() and lab not in labels:
                labels.append(lab)
        if labels:
            lines.append(f"**Other names / labels:** {', '.join(labels[:5])}.")

        # Country/place nodes get polluted person-fields after merging; skip
        # them. For org/person nodes, surface real role/location if known.
        if not _is_country_like(node):
            data = node.get("data") or {}
            position = data.get("position")
            location = data.get("location")
            if isinstance(position, str) and position.strip() and \
               position.lower() not in {"unknown", "not found", "n/a"}:
                lines.append(f"**Role:** {position}.")
            if isinstance(location, str) and location.strip() and \
               location.lower() not in {"unknown", "not found", "n/a"}:
                lines.append(f"**Location / jurisdiction:** {location}.")
        lines.append("")

        # Top attested relationships (pull from EDGES — they carry sources
        # and context that the node.data.relations field does not)
        my_edges = edges_by_node.get(ident, [])
        # Dedup on (counterparty, context) and keep ones with context first
        seen = set()
        relationship_rows = []
        for e in my_edges:
            other_side = e["dst_identifier"] if e.get("src_identifier") == ident else e.get("src_identifier")
            if not other_side or other_side == ident:
                continue
            rtype, ctx = _edge_context(e)
            key = (other_side, ctx[:80])
            if key in seen:
                continue
            seen.add(key)
            url = _edge_source_url(e)
            direction = "→" if e.get("src_identifier") == ident else "←"
            relationship_rows.append((rtype, direction, other_side, ctx, url))

        # Order: rows with context first, then no-context
        relationship_rows.sort(key=lambda r: (0 if r[3] else 1, -len(r[3])))

        if relationship_rows:
            lines.append("**Key attested relationships:**\n")
            for rtype, direction, other, ctx, url in relationship_rows[:6]:
                cite = sr.cite(url)
                rtype_str = f" _{rtype}_" if rtype and rtype != "unknown" else ""
                ctx_short = (ctx[:220].rstrip() + "…") if len(ctx) > 220 else ctx
                ctx_str = f" — {ctx_short}" if ctx_short else ""
                lines.append(f"- {direction}{rtype_str} **{other}**{ctx_str} {cite}")
            lines.append("")

    return "\n".join(lines)


def _network_map(d: dict, sr: SourceRegistry) -> str:
    final = d["final_merged_graph"]
    edges = final["edges"]
    cross_themes = sorted(
        [t for t in final.get("themes", []) or [] if t.get("is_cross_investigation")],
        key=lambda t: -(t.get("weight") or 0.0),
    )

    # Index attested edges by unordered member pair so we can show the
    # relationships that bind a theme's four members + cite their sources.
    edge_by_pair: dict[frozenset, list[dict]] = defaultdict(list)
    for e in edges:
        s = e.get("src_identifier"); t = e.get("dst_identifier")
        if not (s and t) or s == t:
            continue
        if e.get("type") == "evidence":   # synthetic root-wiring, no relation
            continue
        edge_by_pair[frozenset((s, t))].append(e)

    lines = []
    lines.append("\n## 3. Network Themes\n")
    if not cross_themes:
        lines.append(
            "No theme spans more than one investigative thread in the "
            "current corpus. This is uncommon for an investigation of "
            "this scope and likely reflects sparse source attestation; "
            "an extended corpus is recommended.\n"
        )
        return "\n".join(lines)

    lines.append(
        "A _theme_ is a group of four actors that recur together across "
        "attesting articles with enough corroborated structure to form a "
        "tight cluster. Themes are ranked by an evidence-weighted score, so "
        "a higher-ranked theme is more strongly corroborated, not merely "
        "more densely connected. Each theme below is unpacked into the "
        "attested relationships binding its members and the sources that "
        "attest them; relationships the analysis inferred structurally but "
        "that no single article states are marked _(structural inference)_.\n"
    )

    for i, t in enumerate(cross_themes[:6], 1):
        members = [str(m) for m in (t.get("members") or [])]
        weight = t.get("weight") or 0.0
        runs = t.get("runs_spanned") or []
        scope = "all three threads" if len(runs) >= 3 else f"{len(runs)} of the threads"
        lines.append(
            f"\n### Theme {i}: {' · '.join(members)}\n"
            f"_Spans {scope}; evidence-weighted score {weight:.1f}._\n"
        )

        # Walk the 6 member pairs; surface attested relationships + cite.
        rel_rows = []
        inferred = []
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                pair = frozenset((members[a], members[b]))
                pair_edges = edge_by_pair.get(pair) or []
                if not pair_edges:
                    inferred.append((members[a], members[b]))
                    continue
                # Prefer the edge carrying a context sentence.
                pair_edges.sort(key=lambda e: 0 if _edge_context(e)[1] else 1)
                e = pair_edges[0]
                rtype, ctx = _edge_context(e)
                url = _edge_source_url(e)
                cite = sr.cite(url)
                rtype_str = f" _{rtype}_" if rtype and rtype != "unknown" else ""
                ctx_str = f" — {ctx}" if ctx else ""
                rel_rows.append(
                    f"- **{members[a]}** ↔ **{members[b]}**{rtype_str}{ctx_str} {cite}"
                )

        if rel_rows:
            lines.append("**Attested relationships among the members:**\n")
            lines.extend(rel_rows)
            lines.append("")
        if inferred:
            pairs = ", ".join(f"{x} ↔ {y}" for x, y in inferred)
            lines.append(
                f"_Structural inference (no single article states these "
                f"directly): {pairs}._\n"
            )

    if len(cross_themes) > 6:
        lines.append(f"\n_({len(cross_themes) - 6} further cross-thread themes "
                     "in the underlying dataset.)_\n")
    return "\n".join(lines)


def _timeline(d: dict, sr: SourceRegistry) -> str:
    final = d["final_merged_graph"]
    events = [n for n in final["nodes"] if n.get("type") == "event"]
    dated = []
    for e in events:
        ed = (e.get("data") or {}).get("date")
        iso = _date_iso(ed) if ed and re.match(r"\d{4}-\d{2}-\d{2}", str(ed)) else (ed if isinstance(ed, str) and re.match(r"\d{4}-\d{2}-\d{2}", ed) else None)
        if iso:
            dated.append((iso, e))
    dated.sort(key=lambda p: p[0])

    lines = []
    lines.append("\n## 4. Timeline of Reported Incidents\n")
    if not dated:
        lines.append("No dated incidents were extracted at sufficient precision to construct a timeline.\n")
        return "\n".join(lines)

    lines.append(
        f"The following {len(dated)} dated incidents were reported across "
        "the investigative threads. Date precision reflects what the "
        "underlying reporting carried.\n"
    )
    for iso, e in dated[:40]:
        ident = e["identifier"]
        data = e.get("data") or {}
        # Field-merging across chunks can turn a scalar field into a list
        # (e.g. event_type = ["sanctions", "legal"]). Coerce to a string.
        desc = _as_text(data.get("description"))
        loc = _as_text(data.get("location"))
        etype = _as_text(data.get("event_type"))
        url = _as_text(data.get("source_url"))
        cite = sr.cite(url)
        meta_bits = []
        if etype and etype.lower() not in {"other", "unknown", ""}:
            meta_bits.append(f"_{etype}_")
        if loc and loc.lower() not in {"unknown", "not found", ""}:
            meta_bits.append(f"in {loc}")
        meta = f" ({'; '.join(meta_bits)})" if meta_bits else ""
        desc_str = f" {desc[:240]}" if isinstance(desc, str) and desc.strip() else ""
        lines.append(f"- **{iso}** — _{ident}_{meta}.{desc_str} {cite}")
    if len(dated) > 40:
        lines.append(f"\n_({len(dated) - 40} further dated incidents available in the underlying dataset.)_")
    lines.append("")
    return "\n".join(lines)


def _causation_section(d: dict, sr: SourceRegistry) -> str:
    edges = d["final_merged_graph"]["edges"]
    causal = [e for e in edges if e.get("type") == "claimed_caused_by"]

    lines = []
    lines.append("\n## 5. Causal Assertions Reported by Sources\n")
    if not causal:
        lines.append(
            "No source in the corpus made an explicit causal assertion "
            "linking two actors or incidents at a confidence level that "
            "survived the analysis filters. This is informative in "
            "itself: where reporting is descriptive rather than "
            "explanatory, the analyst should treat any causal framing "
            "downstream as their own inference rather than as a source "
            "claim.\n"
        )
        return "\n".join(lines)

    lines.append(
        "The following causal assertions were extracted **from the "
        "source articles themselves** -- they represent claims that "
        "the reporting attributes to its own narrative, not inferences "
        "from this analysis. Weights combine claim strength, source "
        "confidence, and multi-source corroboration.\n"
    )
    causal.sort(key=lambda e: -float((e.get("attributes") or {}).get("weight") or 0.0))
    for e in causal:
        src = e.get("src_identifier"); dst = e.get("dst_identifier")
        attrs = e.get("attributes") or {}
        w = attrs.get("weight") or 0
        ctx = attrs.get("context") or ""
        url = e.get("source") if isinstance(e.get("source"), str) and e.get("source", "").startswith("http") else ""
        cite = sr.cite(url)
        lines.append(f"- **{src}** caused → **{dst}** _(claim weight {float(w):.2f})_ "
                     f"{cite}\n  > {ctx[:280]}")
    lines.append("")
    return "\n".join(lines)


def _confidence_assessment(d: dict) -> str:
    final = d["final_merged_graph"]
    fetched = sum(len(b) for s in d["per_event_states"] for b in s.get("article_batches", []))
    body_ok = sum(1 for s in d["per_event_states"] for b in s.get("article_batches", [])
                  for a in b if a.get("text"))
    publishers = {a.get("publisher") for s in d["per_event_states"]
                  for b in s.get("article_batches", []) for a in b
                  if a.get("publisher")}
    n_runs = len(d.get("events", []))
    bridges = final.get("bridging_entities", []) or []
    all3 = [b for b in bridges if len(b.get("runs") or []) >= n_runs]

    lines = []
    lines.append("\n## 6. Confidence and Caveats\n")
    lines.append("**Source coverage.** "
                 f"{body_ok}/{fetched} articles ({100*body_ok/max(1,fetched):.0f} percent) "
                 f"contributed full body text; {len(publishers)} distinct "
                 "publishers attested at least one element of the analysis. "
                 "The corpus is therefore reasonably diverse but not "
                 "comprehensive; an absence in this report does not "
                 "establish an absence in the underlying real-world picture.\n")
    lines.append(
        "**Confidence assignment.** The structural-confidence labels "
        "above (Almost Certain / Highly Likely / Likely / Even Chance / "
        "Unlikely) follow standard analytic conventions. They reflect "
        "how strongly the cross-thread structure ties an actor to "
        "multiple stories. They are *not* statements about the "
        "underlying truth of any single article -- a high-confidence "
        "structural finding can still be sourced primarily to lower-"
        "credibility outlets, and the bibliography should be inspected "
        "before any downstream use.\n"
    )
    lines.append(
        "**Causal interpretation.** This analysis surfaces "
        "co-occurrence and attested relationships. Causation appears "
        "in this report only where a source article itself asserts it "
        "(Section 5). Any causal framing the reader infers beyond that "
        "should be marked as analyst inference rather than source claim.\n"
    )
    lines.append(
        "**Coverage of denied or rebutted claims.** The analysis "
        "extracts what sources report, not what they refute. Where the "
        "underlying reporting consists primarily of allegations or "
        "official statements, the network reflects those statements; "
        "responses, denials, or contemporaneous rebuttals from "
        "other parties may not be represented at proportionate weight.\n"
    )
    return "\n".join(lines)


def _recommendations(d: dict, run_labels: list[str]) -> str:
    final = d["final_merged_graph"]
    bridges = final.get("bridging_entities", []) or []
    n_runs = len(d.get("events", []))
    all3 = [b for b in bridges if len(b.get("runs") or []) >= n_runs]
    others = [b for b in bridges if len(b.get("runs") or []) < n_runs]

    lines = []
    lines.append("\n## 7. Recommended Next Steps\n")
    if all3:
        names = ", ".join(f"**{b['identifier']}**" for b in all3[:4])
        lines.append(
            f"1. **Deepen sourcing on the cross-thread actors.** "
            f"{names} attest in all investigative threads. Targeted "
            "supplementary sourcing -- e.g., regulatory filings, "
            "court records, sanctions lists, social-media activity -- "
            "should be commissioned for each before any operational "
            "conclusion is drawn from the structural finding alone.\n"
        )
    if others:
        names = ", ".join(f"**{b['identifier']}**" for b in others[:3])
        lines.append(
            f"2. **Probe two-thread bridges as candidate growth "
            f"vectors.** Actors such as {names} bridge two threads "
            "and may represent the next layer of the network if a "
            "fourth investigative thread is opened. The natural "
            "follow-up query is one anchored on that actor directly.\n"
        )
    lines.append(
        "3. **Extend the timeframe.** This analysis used a 30-day "
        "news window. For network topology that includes longer-lived "
        "facilitators (financial intermediaries, shell entities, "
        "logistical chokepoints), a 12-month corpus would substantially "
        "improve attestation density.\n"
    )
    lines.append(
        "4. **Triangulate against non-news sources.** Public databases "
        "(corporate registries, vessel-tracking, ship-AIS, sanctions "
        "lists, court dockets) are well-suited to falsify or strengthen "
        "any of the bridging actors named above.\n"
    )
    return "\n".join(lines)


def _methodology_appendix() -> str:
    return (
        "\n## Appendix: Methodology Note\n\n"
        "Analysis was performed by an automated pipeline that "
        "retrieves news articles for each investigative query, "
        "extracts named actors and reported incidents via a large "
        "language model, and builds a relationship network in which "
        "every actor is one node and each attested relationship is one "
        "edge tied to its source URL. The pipeline then identifies "
        "actors and themes that appear in more than one investigative "
        "thread, with confidence weighted by attestation density.\n\n"
        "The pipeline does not perform open-web crawling beyond the "
        "publisher pages returned by the news aggregator; it does not "
        "consult closed or paid databases; and it does not perform "
        "human-source elicitation. Where the pipeline reports a causal "
        "claim, the claim is taken verbatim from a source article and "
        "preserved with its URL; the pipeline does not assert causation "
        "of its own. All structural findings are reproducible from the "
        "underlying corpus and the analysis can be re-run on an "
        "extended corpus or different query set on request.\n"
    )


# ---------------------------------------------------------------------------
# Domain-specific labelling
# ---------------------------------------------------------------------------

DOMAIN_LABELS = {
    "terror_financing":          "Network analysis: terror-financing exposure",
    "sanctions_evasion":         "Network analysis: sanctions-evasion exposure",
    "corporate_misconduct":      "Network analysis: corporate-misconduct exposure",
    "election_interference":     "Network analysis: election-integrity exposure",
    "environmental_violations":  "Network analysis: environmental-regulatory exposure",
    "supply_chain_human_rights": "Network analysis: human-rights supply-chain exposure",
    "general":                   "Cross-thread open-source network analysis",
}


def _run_labels(d: dict) -> list[str]:
    """Render a human-friendly title for each run in the artifact."""
    out = []
    for ev in d.get("events", []):
        if isinstance(ev, dict):
            label = (ev.get("label") or ev.get("query") or
                     _humanise_run(ev.get("name") or ev.get("id", "")))
        else:
            label = _humanise_run(str(ev))
        out.append(str(label))
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_report(json_path: Path) -> Path:
    d = json.loads(json_path.read_text())
    params = d.get("params") or {}
    domain = params.get("domain") or "general"
    domain_label = DOMAIN_LABELS.get(domain, DOMAIN_LABELS["general"])
    run_labels = _run_labels(d)

    sr = SourceRegistry()
    ref = _ref_id(json_path)
    today = datetime.now().strftime("%Y-%m-%d")

    parts = []
    parts.append(f"# {domain_label}\n")
    parts.append(f"_Open-source intelligence report. Reference {ref}. "
                 f"Prepared {today}._\n")
    parts.append("---\n")
    parts.append(_executive_summary(d, sr, domain_label, run_labels))
    parts.append(_scope_section(d, sr, domain_label, run_labels))
    parts.append(_key_entities(d, sr))
    parts.append(_network_map(d, sr))
    parts.append(_timeline(d, sr))
    parts.append(_causation_section(d, sr))
    parts.append(_confidence_assessment(d))
    parts.append(_recommendations(d, run_labels))

    # References
    parts.append("\n## References\n")
    parts.append(sr.bibliography_md())

    parts.append(_methodology_appendix())

    out_path = json_path.with_suffix(".customer_report.md")
    out_path.write_text("\n".join(parts))
    print(f"Wrote: {out_path}")
    print(f"  size: {out_path.stat().st_size:,} bytes")
    print(f"  sources cited: {len(sr._order)}")
    return out_path


def main():
    if len(sys.argv) < 2:
        print("Usage: build_customer_report.py <artifact.json> [<artifact2.json> ...]", file=sys.stderr)
        sys.exit(1)
    for p in sys.argv[1:]:
        build_report(Path(p))


if __name__ == "__main__":
    main()
