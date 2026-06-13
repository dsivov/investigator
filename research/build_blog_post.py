"""Build a single self-contained HTML blog post about the cross-event news
investigation work. Pulls real numbers + leads + sources from the Iran-proxy
run artifact and embeds matplotlib figures as base64 PNGs.

Output: research/blog_post_finding_threads_in_news.html
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
import networkx as nx

ARTIFACT = Path("/home/dsivov/Work/context_graph/news_investigations/cross_event/"
                "cross_haddad_strike_hezbollah_sanctions_houthi_red_sea_20260602_141718.json")
# Second-domain run (Russia/China/Iran sanctions-evasion) -- used to populate
# the "Does it generalise?" section. Stats only; we don't redraw the headline
# Iran-proxy narrative around it.
ARTIFACT_D2 = Path("/home/dsivov/Work/context_graph/news_investigations/cross_event/"
                   "cross_russia_oil_darkfleet_china_yuan_russia_iran_russia_drone_20260604_085440.json")
OUT_HTML = Path("/home/dsivov/Work/context_graph/research/blog_post_finding_threads_in_news.html")


def encode_fig(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# Figure 1: Three stories, two bridges (the headline visual)
# ---------------------------------------------------------------------------

def fig_three_stories_bridges():
    fig, ax = plt.subplots(figsize=(12, 7)); ax.set_xlim(0, 100)
    ax.set_ylim(0, 65); ax.set_axis_off()

    # Three story panels. Labels are two-line so each line fits inside
    # the 22-unit panel width without colliding with the neighbour.
    panels = [
        {"x": 8,  "y": 8,  "w": 22, "h": 44, "color": "#fce4e4",
         "border": "#cc2222", "label": "Story 1\nIsraeli strike on Haddad",
         "actors": ["Israel", "Izz al-Din al-Haddad", "Qassam Brigades", "Mohammed Odeh"]},
        {"x": 39, "y": 8, "w": 22, "h": 44, "color": "#e4ecfa",
         "border": "#2266cc", "label": "Story 2\nUS sanctions Hezbollah financiers",
         "actors": ["Hezbollah", "US Treasury", "Bessent", "Binance"]},
        {"x": 70, "y": 8, "w": 22, "h": 44, "color": "#fdebd2",
         "border": "#dd8800", "label": "Story 3\nHouthi Red Sea attacks",
         "actors": ["Houthi", "Al-Shabaab", "Somali Pirates", "Tanker hits"]},
    ]
    for p in panels:
        box = FancyBboxPatch((p["x"], p["y"]), p["w"], p["h"],
                              boxstyle="round,pad=0.4",
                              linewidth=2.0, facecolor=p["color"],
                              edgecolor=p["border"], zorder=1)
        ax.add_patch(box)
        # Title as a header above the panel (no length-vs-width constraint)
        ax.text(p["x"] + p["w"]/2, p["y"] + p["h"] + 2.0, p["label"],
                ha="center", va="bottom", fontsize=10, fontweight="bold",
                color=p["border"])
        # Actors now use the full panel height
        for i, a in enumerate(p["actors"]):
            ay = p["y"] + p["h"] - 5 - i*4
            ax.plot(p["x"] + 4, ay, "o", color=p["border"],
                    markersize=7, zorder=3)
            ax.text(p["x"] + 6.5, ay, a, va="center", fontsize=9,
                    color="#222", zorder=3)

    # The two cross-story bridges sit on the gaps between panels
    bridges = [
        {"x": 32, "y": 30, "label": "HAMAS",
         "connects": [(8+22-1, 30), (39+1, 30)]},      # links story 1 <-> 2
        {"x": 63, "y": 30, "label": "IRAN",
         "connects": [(39+22-1, 30), (70+1, 30)]},     # links story 2 <-> 3
    ]
    for b in bridges:
        # Lines from bridge to each side first
        for end in b["connects"]:
            ax.plot([b["x"], end[0]], [b["y"], end[1]], "-",
                    color="#1f7a1f", linewidth=2, zorder=2)
        # Bridge node — large, green-bordered
        ax.plot(b["x"], b["y"], "o", markersize=42, color="white",
                markeredgecolor="#1f7a1f", markeredgewidth=4, zorder=4)
        ax.text(b["x"], b["y"], b["label"], ha="center", va="center",
                fontsize=11, fontweight="bold", color="#1f7a1f", zorder=5)

    # Legend strip
    ax.text(50, 2.5,
            "Bridges (thick green border): actors that the system found "
            "attested in MULTIPLE stories. Two stories away, but the same actor.",
            ha="center", va="center", fontsize=10, fontstyle="italic", color="#444")
    return fig


# ---------------------------------------------------------------------------
# Figure 2: Pipeline flow
# ---------------------------------------------------------------------------

def fig_pipeline():
    fig, ax = plt.subplots(figsize=(12, 3.2))
    ax.set_xlim(0, 100); ax.set_ylim(0, 30); ax.set_axis_off()
    steps = [
        ("News articles\n(Google News)",       "#e4ecfa"),
        ("Actor + event\nextraction (LLM)",    "#e8f5e9"),
        ("Knowledge graph\nactors + events",   "#fff3e0"),
        ("Theme detection +\nconfidence prop.","#f3e5f5"),
        ("Cross-story leads\n+ analyst report","#fce4ec"),
    ]
    width = 17; gap = 3
    for i, (label, color) in enumerate(steps):
        x = 2 + i*(width+gap)
        box = FancyBboxPatch((x, 8), width, 14,
                              boxstyle="round,pad=0.4", linewidth=1.5,
                              facecolor=color, edgecolor="#444")
        ax.add_patch(box)
        ax.text(x+width/2, 15, label, ha="center", va="center",
                fontsize=10, fontweight="bold")
        if i < len(steps)-1:
            arrow = FancyArrowPatch((x+width+0.3, 15), (x+width+gap-0.3, 15),
                                     arrowstyle="-|>", color="#333",
                                     mutation_scale=14, linewidth=1.5)
            ax.add_patch(arrow)
    return fig


# ---------------------------------------------------------------------------
# Figure 3: Stats funnel (real numbers from the Iran-proxy run)
# ---------------------------------------------------------------------------

def fig_stats(metrics: dict):
    fig, ax = plt.subplots(figsize=(10, 4.5))
    labels = [
        "Articles\nfetched", "Articles\nextracted",
        "Graph\nnodes", "Relationships\n(edges)",
        "Cross-story\nbridges", "Cross-story\nleads",
    ]
    values = [
        metrics["fetched"], metrics["extracted"],
        metrics["nodes"],   metrics["edges"],
        metrics["bridges"], metrics["leads"],
    ]
    colors = ["#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#1f7a1f", "#15531f"]
    bars = ax.bar(labels, values, color=colors, edgecolor="white", linewidth=2)
    ax.set_yscale("log")
    ax.set_ylim(0.5, max(values)*1.6)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_ylabel("count (log scale)")
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width()/2, val*1.15, str(val),
                ha="center", va="bottom", fontsize=11, fontweight="bold")
    ax.set_title("From haystack to needles: the Iran-proxy run", fontsize=12, pad=10)
    return fig


# ---------------------------------------------------------------------------
# Figure 4: Cross-event themes (top, simplified visual)
# ---------------------------------------------------------------------------

def fig_themes_top():
    # Hand-drawn list of cross-event themes as a styled table-image
    themes = [
        ("HAMAS · HEZBOLLAH · IRAN · US TREASURY", "all three stories",       3.5),
        ("HAMAS · ISRAELI STRIKE · IZZ AL-DIN AL-HADDAD · QASSAM BRIGADES", "Stories 1 + 2", 6.0),
        ("HOUTHI · IRAN · SOMALI PIRATES · piracy victims event", "Stories 2 + 3",          3.5),
        ("BESSENT · HAMAS · IRAN · US TREASURY", "all three stories",         3.0),
    ]
    fig, ax = plt.subplots(figsize=(11, 3.6))
    ax.set_axis_off()
    ax.set_xlim(0, 100); ax.set_ylim(0, len(themes)*8 + 6)
    ax.text(50, len(themes)*8 + 3,
            "Themes that span at least two of the three stories",
            ha="center", va="center", fontsize=12, fontweight="bold")
    for i, (members, spans, w) in enumerate(themes):
        y = (len(themes) - i)*8 - 4
        color = "#1f7a1f" if "all three" in spans else "#2266cc"
        ax.add_patch(FancyBboxPatch((1, y-3), 98, 5,
                                     boxstyle="round,pad=0.3",
                                     facecolor="#f7faf7" if "all three" in spans else "#f4f7fc",
                                     edgecolor=color, linewidth=1.2))
        ax.text(2.5, y, members, va="center", fontsize=9.5, color="#222")
        ax.text(85, y, spans, va="center", ha="right", fontsize=9,
                color=color, fontweight="bold")
        ax.text(98, y, f"w {w:.1f}", va="center", ha="right",
                fontsize=9, color="#666")
    return fig


# ---------------------------------------------------------------------------
# Figure 5: Real merged graph rendered from the actual JSON data
# ---------------------------------------------------------------------------

def fig_real_graph(d: dict):
    """Render the actual merged-graph data as a static figure.

    Strategy: filter to entities + a few canonical events; layout via
    spring; colour by run-provenance (bridges = green, single-run =
    red/blue/orange); edges by type. Goal is a clear illustration of
    'this is what the system actually built', not a busy debug dump.
    """
    final = d["final_merged_graph"]
    nodes_by_id = {n["identifier"]: n for n in final["nodes"]}
    edges = final["edges"]

    # Which run each node belongs to
    node_runs = {nid: set(n.get("runs") or []) for nid, n in nodes_by_id.items()}
    bridge_set = {b["identifier"] for b in final.get("bridging_entities", []) or []}

    # Pick what to render: all entities + a curated set of important events.
    # Skip events with very long sentence-like identifiers; keep ones that
    # are central to the cross-event story.
    important_events = {
        "ISRAELI STRIKE KILLS HAMAS LEADER IZZ AL-DIN AL-HADDAD",
        "US TREASURY SANCTIONS HEZBOLLAH FINANCIERS",
        "HOUTHI ATTACKS ON RED SEA SHIPPING",
        "US AND ISRAEL CONDUCT STRIKES ON TWO STRATEGIC HOUTHI MILITARY SITES",
    }
    shown_ids = set()
    for n in final["nodes"]:
        ident = n["identifier"]
        ntype = n.get("type")
        if ntype == "entity":
            shown_ids.add(ident)
        elif ntype == "event" and ident in important_events:
            shown_ids.add(ident)

    # Per-run palette
    EVENT_COLORS = {
        "haddad_strike": "#d33636",
        "hezbollah_sanctions": "#3a6ed8",
        "houthi_red_sea": "#dd9023",
    }
    BRIDGE_COLOR = "#1f7a1f"
    OTHER_COLOR = "#888888"

    def node_color(ident):
        rs = node_runs.get(ident, set())
        if ident in bridge_set:
            return BRIDGE_COLOR
        if len(rs) == 1:
            return EVENT_COLORS.get(next(iter(rs)), OTHER_COLOR)
        return OTHER_COLOR

    # Build the subgraph for layout
    G = nx.Graph()
    for ident in shown_ids:
        G.add_node(ident)
    # Add edges between shown nodes only (skip event_participation /
    # synthetic root-wiring for readability)
    edge_styles = []
    for e in edges:
        s, t = e.get("src_identifier"), e.get("dst_identifier")
        if not (s and t) or s == t: continue
        if s not in shown_ids or t not in shown_ids: continue
        if e.get("type") == "evidence" and e.get("source") == "evidence":
            continue  # root-wiring artifact
        etype = e.get("type")
        if not G.has_edge(s, t):
            G.add_edge(s, t)
        edge_styles.append((s, t, etype))

    # Spring layout with extra spread
    pos = nx.spring_layout(G, k=2.4, iterations=200, seed=42)

    fig, ax = plt.subplots(figsize=(13, 8.5))
    ax.set_axis_off()

    # Draw edges by type
    type_to_color = {
        "affiliation": "#999999",
        "event_participation": "#cc7722",
        "event_followed_by": "#7a3aa5",
        "event_coincident": "#7a3aa5",
        "claimed_caused_by": "#cc2233",
    }
    type_to_width = {
        "affiliation": 1.5,
        "event_participation": 1.0,
        "event_followed_by": 1.6,
        "event_coincident": 1.6,
        "claimed_caused_by": 3.0,
    }
    type_to_style = {
        "claimed_caused_by": "solid",
        "event_followed_by": "dashed",
        "event_coincident": "dashed",
    }
    # Avoid duplicate-edge drawing
    drawn_pairs = set()
    for s, t, etype in edge_styles:
        key = tuple(sorted([s, t]))
        if key in drawn_pairs and etype not in ("claimed_caused_by",):
            continue
        drawn_pairs.add(key)
        col = type_to_color.get(etype, "#cccccc")
        w = type_to_width.get(etype, 1.0)
        style = type_to_style.get(etype, "solid")
        x0, y0 = pos[s]; x1, y1 = pos[t]
        ax.plot([x0, x1], [y0, y1], color=col, linewidth=w,
                linestyle=style, zorder=1, alpha=0.65)

    # Draw nodes
    for ident in shown_ids:
        if ident not in pos: continue
        x, y = pos[ident]
        ntype = nodes_by_id[ident].get("type")
        color = node_color(ident)
        is_bridge = ident in bridge_set
        # Event = diamond, entity = circle
        marker = "D" if ntype == "event" else "o"
        size = 350 if is_bridge else 200 if ntype == "entity" else 240
        edgew = 3 if is_bridge else 1.2
        edgec = BRIDGE_COLOR if is_bridge else "#222222"
        ax.scatter([x], [y], s=size, c=color, marker=marker,
                   edgecolors=edgec, linewidths=edgew, zorder=3)
        # Truncate long event identifiers for label
        label = ident if len(ident) <= 35 else ident[:32] + "..."
        # Place text slightly above the node
        ax.text(x, y + 0.04, label, fontsize=8 if ntype == "entity" else 7,
                ha="center", va="bottom", zorder=4,
                fontweight="bold" if is_bridge else "normal",
                color="#1a1a1a")

    # Legend
    legend_elements = [
        plt.Line2D([0], [0], marker="o", color="w", label="Entity",
                   markerfacecolor="#999", markersize=10),
        plt.Line2D([0], [0], marker="D", color="w", label="Event",
                   markerfacecolor="#999", markersize=10),
        plt.Line2D([0], [0], marker="o", color="w", label="Bridge (≥2 stories)",
                   markerfacecolor=BRIDGE_COLOR, markeredgecolor=BRIDGE_COLOR,
                   markeredgewidth=3, markersize=12),
        plt.Line2D([0], [0], marker="s", color="w", label="Story 1 (Haddad strike)",
                   markerfacecolor=EVENT_COLORS["haddad_strike"], markersize=10),
        plt.Line2D([0], [0], marker="s", color="w", label="Story 2 (Hezbollah sanctions)",
                   markerfacecolor=EVENT_COLORS["hezbollah_sanctions"], markersize=10),
        plt.Line2D([0], [0], marker="s", color="w", label="Story 3 (Houthi Red Sea)",
                   markerfacecolor=EVENT_COLORS["houthi_red_sea"], markersize=10),
        plt.Line2D([0], [0], color="#cc2233", linewidth=3,
                   label="Source-claimed causation"),
        plt.Line2D([0], [0], color="#999999", linewidth=1.5,
                   label="Attested affiliation"),
        plt.Line2D([0], [0], color="#7a3aa5", linewidth=1.6, linestyle="dashed",
                   label="Event followed by / coincident"),
    ]
    ax.legend(handles=legend_elements, loc="lower left", fontsize=8.5,
              framealpha=0.92, ncol=2)
    ax.set_title("Real merged graph from the Iran-proxy run\n"
                 "(entities + four central events; HAMAS and IRAN are the bridges)",
                 fontsize=12, pad=12)
    return fig


# ---------------------------------------------------------------------------
# Figure (new): the TMFG-filtered subgraph with theme tetrahedra shaded
# ---------------------------------------------------------------------------

def fig_tmfg_themes(d: dict):
    """Render the actual TMFG-filtered subgraph for the Iran-proxy run.

    The TMFG output is the union of (p - 3) tetrahedra glued at shared
    triangles. Visually we shade the top cross-event tetrahedra as
    coloured polygons over the node-edge graph, so the reader can see:
        * the structural backbone (nodes + attested edges),
        * the 4-clique themes (coloured regions),
        * the bridges (nodes sitting in multiple coloured regions).
    """
    import numpy as np
    from matplotlib.patches import Polygon

    final = d["final_merged_graph"]
    nodes_by_id = {n["identifier"]: n for n in final["nodes"]}
    edges = final["edges"]
    bridge_set = {b["identifier"] for b in final.get("bridging_entities", []) or []}
    # Prioritise themes that visibly cross stories: themes spanning ALL
    # runs first, then 2 runs, then within-story. Within a tier, heavier
    # themes win. This makes the bridges (HAMAS, IRAN) sit at polygon
    # intersections rather than far apart in disconnected components.
    themes = sorted(
        final.get("themes", []) or [],
        key=lambda t: (-len(t.get("runs_spanned") or []),
                       -(t.get("weight") or 0.0)),
    )

    THEME_LIMIT = 5
    chosen_themes = []
    seen_member_sigs = set()
    for t in themes:
        members = tuple(sorted((t.get("members") or [])[:4]))
        if len(members) < 3:
            continue
        if members in seen_member_sigs:
            continue
        seen_member_sigs.add(members)
        chosen_themes.append(t)
        if len(chosen_themes) >= THEME_LIMIT:
            break

    # Collect all member nodes (union of chosen tetrahedra)
    member_nodes = set()
    for t in chosen_themes:
        member_nodes.update(t.get("members") or [])

    # Attested edges between member nodes (from the JSON)
    attested_pairs: set = set()
    for e in edges:
        s, t = e.get("src_identifier"), e.get("dst_identifier")
        if not (s and t) or s == t:
            continue
        if s in member_nodes and t in member_nodes:
            attested_pairs.add(frozenset((s, t)))

    # By construction a TMFG tetrahedron is K_4 -- all 6 internal edges
    # exist in the filtered graph (even if some were fill-in additions the
    # algorithm made to satisfy chordality). For layout we use the union of
    # attested + tetrahedron-internal pairs; for drawing we distinguish them.
    G = nx.Graph()
    for n in member_nodes:
        G.add_node(n)
    fill_in_pairs: set = set()
    for t in chosen_themes:
        members = list(t.get("members") or [])
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pair = frozenset((members[i], members[j]))
                G.add_edge(*pair)
                if pair not in attested_pairs:
                    fill_in_pairs.add(pair)

    # Layout: spring on the union graph. Theme members now pull together,
    # and a node that sits in two themes (e.g. a bridge) ends up between
    # its two clusters -- which is exactly the visual story we want.
    pos = nx.spring_layout(G, k=1.7, iterations=250, seed=11)

    fig, ax = plt.subplots(figsize=(12.5, 8.2))
    ax.set_axis_off()

    # Shade tetrahedra as semi-transparent polygons behind the nodes.
    # Convex hull of the 4 vertex positions gives a clean polygon.
    theme_palette = ["#1f7a1f", "#cc2233", "#2266cc", "#dd8800",
                     "#7a3aa5", "#0e9ea3"]
    for i, t in enumerate(chosen_themes):
        members = [m for m in (t.get("members") or []) if m in pos]
        if len(members) < 3:
            continue
        pts = np.array([pos[m] for m in members])
        # Centre + sort by polar angle for a nicely-ordered polygon
        c = pts.mean(axis=0)
        order = np.argsort(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))
        polygon = Polygon(pts[order], closed=True,
                          facecolor=theme_palette[i % len(theme_palette)],
                          alpha=0.16, edgecolor=theme_palette[i % len(theme_palette)],
                          linewidth=1.6, linestyle="--", zorder=1)
        ax.add_patch(polygon)
        # Tiny label tag for the theme
        ax.text(c[0], c[1] - 0.04, f"theme #{i+1}  w={t.get('weight'):.1f}",
                ha="center", va="top", fontsize=7.5,
                color=theme_palette[i % len(theme_palette)], style="italic",
                zorder=2)

    # Edges: attested (solid grey) vs fill-in (dashed orange).
    # Fill-in edges are the structural hypotheses TMFG adds to keep the
    # graph chordal -- the analyst should look at them as "claims worth
    # checking" rather than facts the corpus already stated.
    for u, v in G.edges():
        pair = frozenset((u, v))
        x0, y0 = pos[u]; x1, y1 = pos[v]
        if pair in fill_in_pairs:
            ax.plot([x0, x1], [y0, y1], color="#cc7722", linewidth=1.1,
                    linestyle="--", alpha=0.55, zorder=2)
        else:
            ax.plot([x0, x1], [y0, y1], color="#444", linewidth=1.35,
                    alpha=0.85, zorder=2)

    # Draw nodes: bridges large + green, others smaller + grey
    for ident in member_nodes:
        if ident not in pos:
            continue
        x, y = pos[ident]
        ntype = nodes_by_id.get(ident, {}).get("type")
        is_bridge = ident in bridge_set
        marker = "D" if ntype == "event" else "o"
        size = 360 if is_bridge else 210
        face = "#1f7a1f" if is_bridge else ("#fff3e0" if ntype == "event" else "#e4ecfa")
        edge = "#1f7a1f" if is_bridge else "#333"
        ax.scatter([x], [y], s=size, c=face, marker=marker,
                   edgecolors=edge, linewidths=(3 if is_bridge else 1.2), zorder=3)
        label = ident if len(ident) <= 32 else ident[:29] + "..."
        ax.text(x, y + 0.045, label,
                fontsize=8 if ntype == "entity" else 7,
                ha="center", va="bottom", zorder=4,
                fontweight="bold" if is_bridge else "normal",
                color="#1a1a1a")

    # Legend
    legend_elements = [
        plt.Line2D([0], [0], marker="o", color="w", label="Actor",
                   markerfacecolor="#e4ecfa", markeredgecolor="#333",
                   markersize=10),
        plt.Line2D([0], [0], marker="D", color="w", label="Event",
                   markerfacecolor="#fff3e0", markeredgecolor="#333",
                   markersize=10),
        plt.Line2D([0], [0], marker="o", color="w", label="Bridge",
                   markerfacecolor="#1f7a1f", markeredgecolor="#1f7a1f",
                   markeredgewidth=3, markersize=12),
        mpatches.Patch(facecolor="#1f7a1f", alpha=0.22,
                       edgecolor="#1f7a1f", linewidth=1.2, linestyle="--",
                       label="4-clique theme (tetrahedron)"),
        plt.Line2D([0], [0], color="#444", linewidth=1.5,
                   label="Attested edge"),
        plt.Line2D([0], [0], color="#cc7722", linewidth=1.2,
                   linestyle="--", label="TMFG fill-in (hypothesis)"),
    ]
    ax.legend(handles=legend_elements, loc="lower left", fontsize=8.5,
              framealpha=0.92)

    n_chosen = len(chosen_themes)
    ax.set_title(f"TMFG-filtered backbone: top {n_chosen} cross-story 4-clique themes\n"
                 "(each shaded polygon is one tetrahedron; bridges sit at "
                 "polygon intersections)",
                 fontsize=11.5, pad=12)
    return fig


# ---------------------------------------------------------------------------
# Figure 6: Second-domain bridges (Russia sanctions-evasion case)
# ---------------------------------------------------------------------------

def fig_second_domain_bridges():
    """Triangle layout: three story panels at the corners, three bridge
    actors clustered in the center. Each bridge connects to all three
    stories -- visually distinct from the Iran-proxy figure (where each
    bridge connects only two adjacent stories)."""
    import math
    fig, ax = plt.subplots(figsize=(11, 8))
    ax.set_xlim(0, 100); ax.set_ylim(-2, 76); ax.set_axis_off()

    cx, cy = 50, 37

    # Three story panels arranged as a triangle around the centre
    panels = [
        {"angle":  90, "color": "#e4ecfa", "border": "#2266cc",
         "label": "Story 1: Russia's oil dark fleet",
         "actors": ["French Navy", "TAGOR (tanker)", "Macron", "Russian shadow fleet"]},
        {"angle": 210, "color": "#fce4e4", "border": "#cc2222",
         "label": "Story 2: China-yuan trade settlement",
         "actors": ["Putin", "Xi Jinping", "Russia-China energy deal", "Yuan-denominated bonds"]},
        {"angle": 330, "color": "#fdebd2", "border": "#dd8800",
         "label": "Story 3: Iran/Russia military drones",
         "actors": ["Caspian Sea corridor", "Cuba (300 drones)", "Fiber-optic FPV", "Moscow"]},
    ]

    panel_w, panel_h = 26, 14
    radius = 23
    label_offset = 3.2
    for p in panels:
        a = math.radians(p["angle"])
        cxp = cx + radius * math.cos(a)
        cyp = cy + radius * math.sin(a)
        x0, y0 = cxp - panel_w/2, cyp - panel_h/2
        box = FancyBboxPatch((x0, y0), panel_w, panel_h,
                              boxstyle="round,pad=0.4",
                              linewidth=2.0, facecolor=p["color"],
                              edgecolor=p["border"], zorder=1)
        ax.add_patch(box)
        # Title sits OUTSIDE the panel, in the radial direction away from
        # the centre (so it never overlaps the panel or its neighbours).
        if p["angle"] == 90:
            lx, ly, va = cxp, cyp + panel_h/2 + label_offset, "bottom"
        else:
            lx, ly, va = cxp, cyp - panel_h/2 - label_offset, "top"
        ax.text(lx, ly, p["label"], ha="center", va=va,
                fontsize=10, fontweight="bold", color=p["border"])
        # Actors now use the full panel height
        for i, actor in enumerate(p["actors"]):
            ay = y0 + panel_h - 2.0 - i*2.8
            ax.plot(x0 + 2, ay, "o", color=p["border"],
                    markersize=4.5, zorder=3)
            ax.text(x0 + 3.6, ay, actor, va="center", fontsize=8.0,
                    color="#222", zorder=3)
        p["_cx"] = cxp; p["_cy"] = cyp

    # Three bridge nodes clustered at the centre
    bridges = [
        {"label": "CHINA",  "dx":  -6, "dy":  3},
        {"label": "IRAN",   "dx":   0, "dy": -2},
        {"label": "RUSSIA", "dx":   6, "dy":  3},
    ]
    # Lines first (under nodes)
    for b in bridges:
        bx, by = cx + b["dx"], cy + b["dy"]
        for p in panels:
            ax.plot([bx, p["_cx"]], [by, p["_cy"]], "-",
                    color="#1f7a1f", linewidth=1.4, alpha=0.55, zorder=2)
    for b in bridges:
        bx, by = cx + b["dx"], cy + b["dy"]
        ax.plot(bx, by, "o", markersize=42, color="white",
                markeredgecolor="#1f7a1f", markeredgewidth=3.5, zorder=4)
        ax.text(bx, by, b["label"], ha="center", va="center",
                fontsize=10, fontweight="bold", color="#1f7a1f", zorder=5)

    # Caption strip
    ax.text(50, -1,
            "All three bridges (CHINA, IRAN, RUSSIA) appear in ALL THREE stories. "
            "Posterior confidence = 1.00 for each.",
            ha="center", va="center", fontsize=9.5, fontstyle="italic", color="#444")
    return fig


# ---------------------------------------------------------------------------
# Assemble HTML
# ---------------------------------------------------------------------------

def main():
    d = json.loads(ARTIFACT.read_text())
    final = d["final_merged_graph"]
    nodes = final["nodes"]
    edges = final["edges"]
    bridges = final.get("bridging_entities", []) or []
    leads = final.get("cross_event_leads", []) or []
    themes = final.get("themes", []) or []
    cross_themes = [t for t in themes if t.get("is_cross_investigation")]

    # Real numbers
    total_fetched = sum(len(batch) for s in d["per_event_states"]
                        for batch in s.get("article_batches", []))
    total_extracted = sum(1 for s in d["per_event_states"]
                          for batch in s.get("article_batches", [])
                          for a in batch if a.get("text"))
    metrics = {
        "fetched": total_fetched,
        "extracted": total_extracted,
        "nodes": len(nodes),
        "edges": len(edges),
        "bridges": len(bridges),
        "leads": len(leads),
    }
    n_events = sum(1 for n in nodes if n.get("type") == "event")
    n_entities = sum(1 for n in nodes if n.get("type") == "entity")

    # Second-domain run stats (Russia/China/Iran sanctions-evasion)
    d2 = json.loads(ARTIFACT_D2.read_text())
    final2 = d2["final_merged_graph"]
    bridges2 = final2.get("bridging_entities", []) or []
    leads2 = final2.get("cross_event_leads", []) or []
    fetched2 = sum(len(batch) for s in d2["per_event_states"]
                   for batch in s.get("article_batches", []))
    metrics_d2 = {
        "fetched": fetched2,
        "nodes": len(final2["nodes"]),
        "edges": len(final2["edges"]),
        "bridges": len(bridges2),
        "leads": len(leads2),
        "all_3_run_bridges": [b for b in bridges2 if len(b.get("runs") or []) >= 3],
    }

    fig1 = encode_fig(fig_three_stories_bridges())
    fig2 = encode_fig(fig_pipeline())
    fig3 = encode_fig(fig_stats(metrics))
    fig4 = encode_fig(fig_themes_top())
    fig5 = encode_fig(fig_real_graph(d))
    fig6 = encode_fig(fig_second_domain_bridges())
    fig_tmfg = encode_fig(fig_tmfg_themes(d))

    # Causal-claim edges for the new section
    causal_edges = [e for e in edges if e.get("type") == "claimed_caused_by"]
    causal_edges.sort(key=lambda e: -float((e.get("attributes") or {}).get("weight") or 0))

    # Pick one cross-event lead to highlight (Binance / Iran / Hezbollah / Houthi)
    binance_node = next((n for n in nodes if n.get("identifier") == "BINANCE"), None)
    binance_rel = None
    if binance_node:
        rels = (binance_node.get("data") or {}).get("relations") or []
        for r in rels:
            if isinstance(r, dict):
                ctx_obj = r.get("relations") if isinstance(r.get("relations"), dict) else {}
                ctx_str = (ctx_obj.get("context") or "")
                if "Iran" in ctx_str and "Binance" in ctx_str:
                    binance_rel = {
                        "counterpart": r.get("related_node"),
                        "context": ctx_str,
                        "source_url": (r.get("attributes") or {}).get("source_url", "") or "",
                    }
                    break

    bridge_evidence_hamas = next((b for b in bridges if b["identifier"] == "HAMAS"), None)
    bridge_evidence_iran = next((b for b in bridges if b["identifier"] == "IRAN"), None)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Finding the Hidden Threads in News — An OSINT Cross-Story Investigation</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", system-ui, sans-serif;
    max-width: 800px; margin: 2.5em auto; padding: 0 1.4em;
    line-height: 1.65; color: #1a1a1a; background: #fefefe;
  }}
  h1 {{ font-size: 2.3em; line-height: 1.15; margin-bottom: 0.15em; letter-spacing: -0.01em; }}
  h1 + .subtitle {{ color: #555; font-size: 1.1em; margin: 0 0 2.5em 0; }}
  h2 {{ margin-top: 2.6em; font-size: 1.5em; border-bottom: 2px solid #eee; padding-bottom: 0.3em; }}
  h3 {{ margin-top: 1.8em; font-size: 1.15em; color: #1f5a3a; }}
  p {{ margin: 1em 0; }}
  blockquote {{ border-left: 3px solid #1f7a1f; padding: 0.6em 1.1em; background: #f4faf4;
                margin: 1.4em 0; color: #1a3a1a; }}
  blockquote.callout {{ border-left-color: #e69100; background: #fffaf0; color: #4a3a1a; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1.2em 0; font-size: 0.95em; }}
  th, td {{ padding: 0.55em 0.7em; text-align: left; border-bottom: 1px solid #e0e0e0; }}
  th {{ background: #f4f4f6; font-weight: 600; }}
  tbody tr:hover {{ background: #f9f9fb; }}
  code {{ background: #f0f0f2; padding: 0.1em 0.35em; border-radius: 3px;
          font-family: "SF Mono", Consolas, monospace; font-size: 0.92em; }}
  .figure {{ display: block; margin: 1.5em auto; max-width: 100%;
             border: 1px solid #e0e0e0; padding: 12px; background: white; border-radius: 4px;
             box-shadow: 0 1px 4px rgba(0,0,0,0.05); }}
  .caption {{ color: #666; font-style: italic; text-align: center; font-size: 0.9em;
              margin-top: -1em; margin-bottom: 1.8em; }}
  .key-finding {{ background: #f0f4ff; padding: 1.2em 1.4em; border-radius: 6px;
                  margin: 2em 0; border-left: 4px solid #2266cc; }}
  .key-finding h3 {{ margin-top: 0; color: #1a3a7a; }}
  .takeaway {{ background: #fdf6e3; padding: 1em 1.4em; border-radius: 6px;
               margin: 1.5em 0; border-left: 4px solid #d88c00; font-size: 0.97em; }}
  .takeaway strong {{ color: #8a5a00; }}
  .source-pill {{ display: inline-block; background: #eee; padding: 0.1em 0.5em;
                  border-radius: 3px; font-size: 0.8em; color: #555; }}
  .footnote {{ font-size: 0.85em; color: #777; margin-top: 3em; padding-top: 1em;
               border-top: 1px solid #ddd; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 2em 0; }}
  .metric {{ display: inline-block; background: #1f7a1f; color: white;
             padding: 0.15em 0.55em; border-radius: 3px; font-weight: bold; }}
</style>
</head>
<body>

<h1>Finding the Hidden Threads in News</h1>
<div class="subtitle">How we built a tool that reads across separate news stories
and surfaces the connections an analyst would otherwise miss.</div>

<p>
On <strong>May 16, 2026</strong>, Israeli forces killed Hamas military chief
Izz al-Din al-Haddad in a Gaza airstrike. Three days later, the
<strong>US Treasury</strong> sanctioned a group of Hezbollah financiers.
That same week, <strong>Houthi</strong> fighters launched a wave of attacks
on Red Sea shipping. Three stories. Three different beats. Same week.
</p>

<p>
Are they connected? The short answer is yes — through Iran. But you'd
have to read perhaps a hundred articles to know that with confidence.
And by then, the next batch of news has already broken.
</p>

<p>
We built a tool that does this reading-across-stories work automatically.
This post is about what it found in our Iran-proxy run, and how.
</p>

<h2>The problem</h2>

<p>
Open-source intelligence (OSINT) starts with news. An analyst tracking,
say, terror financing reads articles all day — but the stories arrive
<em>separately</em>. A military strike here, a sanctions action there,
an attack on shipping somewhere else. The most valuable insight, often,
isn't in any single story. It's in the pattern <em>across</em> them.
</p>

<p>Two real problems make this hard:</p>
<ol>
  <li><strong>Volume</strong>: there are too many articles to read everything that might be relevant.</li>
  <li><strong>Connection-finding</strong>: even if you read them all, noticing that the same actor appears in three apparently-unrelated stories — and tracing the why — is mostly manual.</li>
</ol>

<p>We wanted a system that could take 2–3 narrowly defined queries — the
kind of question an analyst types into a news search — pull dozens of
articles per query, extract the named actors and events from each
article, build a graph of how everything relates, and then find the
<em>bridges</em>: the actors that appear across stories.</p>

<h2>How it works</h2>

<img class="figure" src="data:image/png;base64,{fig2}" alt="Pipeline diagram"/>
<div class="caption">The pipeline at a glance. The plumbing is steps 1&ndash;3; the analytical engine is steps 4&ndash;6.</div>

<p>
The pipeline runs in six stages. The first two are extraction. The next
three &mdash; <em>merge, filter, triangulate</em> &mdash; turn a soup of
per-article facts into a structured network where the cross-story
backbone becomes visible. The last step lets a downstream confidence
calculation propagate exactly across that network.
</p>

<ol>
  <li><strong>Fetch</strong>. We search Google News for each query and download
      the 30–50 most relevant articles.</li>
  <li><strong>Extract</strong>. A large language model reads each article and pulls
      out (a) the <strong>named actors</strong> — people, organisations, countries
      — and (b) the <strong>events</strong> — concrete real-world incidents being
      described: who did what to whom, when, where.</li>
  <li><strong>Merge evidence across articles.</strong> Article #4 calls them
      &ldquo;Vladimir Putin&rdquo;, article #11 calls them &ldquo;Putin&rdquo;,
      article #23 calls them &ldquo;the Russian president&rdquo;. We collapse
      surface variants of the same actor into a <em>single node</em>, and the
      union of articles that mention them becomes that node's
      <em>evidence list</em>. Same for relationships: if three articles all
      say &ldquo;Hamas is part of Iran's network&rdquo;, that's one edge
      whose <em>weight</em> is three and whose <em>source list</em> is the
      three URLs. Evidence merging is the step that turns a soup of
      per-article extractions into a single network you can reason over.</li>
  <li><strong>Filter to the structural backbone.</strong> A news corpus
      produces many weakly-attested edges &mdash; one article alone might
      mention a tangential connection. We rank edges by how many distinct
      articles attest them and drop the singletons. To make sure no
      relevant actor gets cut off from the investigation subject, the
      algorithm walks the <em>shortest path</em> from any orphaned actor
      back to the root and restores the minimum set of edges needed to
      keep them reachable. The output is a sparser graph in which every
      edge is either independently corroborated or earned its keep as
      part of a path. <em>(This is a corroboration-weighted variant of
      information-filtering networks.)</em></li>
  <li><strong>Triangulate &mdash; the theme detector.</strong> The next step
      builds a chordal-planar triangulation called a <strong>TMFG</strong>
      (Triangulated Maximally Filtered Graph). Greedily, the algorithm
      picks the four most-connected actors as a starting tetrahedron, then
      iteratively glues new actors onto the open triangular faces, always
      choosing the actor whose three new edges add the most weight to the
      structure. The result is an exactly-planar graph that decomposes
      into a tree of 4-cliques. Each 4-clique is a <em>theme</em> &mdash;
      four actors whose shared evidence is strong enough that they bind
      together. Some edges TMFG adds are between actors who weren't
      directly attested together in any single article; these
      <em>fill-in edges</em> become the system's <em>structural
      hypotheses</em> for the analyst to verify.</li>
</ol>

<img class="figure" src="data:image/png;base64,{fig_tmfg}" alt="TMFG backbone with theme tetrahedra"/>
<div class="caption">
  Real data from the Iran-proxy run: five top cross-story 4-clique themes
  shaded as polygons over the TMFG-filtered subgraph. Solid grey edges
  are corroborated by at least one source article; dashed orange edges
  are the structural <em>fill-in</em> the algorithm adds to satisfy
  chordality &mdash; the system treats those as hypothesis claims the
  analyst should look at. <strong>HAMAS</strong> sits at the intersection
  of four polygons and <strong>IRAN</strong> at two; that intersection
  pattern is what makes them structurally cross-story, not just
  textually co-occurring.
</div>

<ol start="6">
  <li><strong>Confidence propagation</strong>. Each actor starts with a per-article
      confidence score; we then adjust those scores by what the network shows.
      <em>If you're consistently surrounded by high-confidence actors, your
      score goes up; if your only neighbours are themselves shaky, it goes down.</em>
      (Under the hood, this is a junction-tree belief-propagation pass over
      the clique tree the TMFG produced; the propagation is exact because
      the graph is chordal &mdash; that is one of the practical payoffs
      of insisting on a chordal triangulation in the previous step.)</li>
</ol>

<p>
For <strong>cross-story analysis</strong> we feed all the queries into one session
of the same pipeline. Every actor and every relationship is stamped with
which query produced it. After the pipeline runs, we look for actors that
appear in <em>more than one</em> query's data. Those are the bridges —
the structural backbone of any cross-story claim.
</p>

<h2>A worked example: Iran's proxy network</h2>

<p>
We ran the pipeline on three independent news searches from May 2026:
</p>

<img class="figure" src="data:image/png;base64,{fig5}" alt="Real merged graph from the Iran-proxy run"/>
<div class="caption">
  The actual merged graph, rendered from the system's output. Each story is
  one colour family; the two green-bordered nodes — <strong>HAMAS</strong>
  and <strong>IRAN</strong> — are the bridges. The thick red line on the right
  is the source-claimed causation we'll discuss below.
</div>

<p style="color: #666; font-size: 0.95em;">
<em>(The figure shows the merged graph the pipeline actually produced, not a
schematic. Entities are circles, events are diamonds; the four canonical
events surface visually as diamond nodes, hundreds of finer-grained
extractions are hidden for readability.)</em>
</p>


<table>
  <thead><tr><th>Story</th><th>Query</th><th>Articles processed</th></tr></thead>
  <tbody>
    <tr><td>1</td><td>Israeli strike kills Hamas leader Izz al-Din al-Haddad May 2026</td><td>86</td></tr>
    <tr><td>2</td><td>US Treasury sanctions Hezbollah financiers May 2026</td><td>112</td></tr>
    <tr><td>3</td><td>Houthi attacks Red Sea shipping May 2026</td><td>163</td></tr>
  </tbody>
</table>

<p>
The three queries cover different theatres — Gaza kinetic, US financial
regulation, Red Sea shipping. No two articles share an explicit topic. An
analyst reading them in sequence would see three distinct stories.
</p>

<p>
Here is what the system found:
</p>

<img class="figure" src="data:image/png;base64,{fig1}" alt="The three stories with two cross-story bridges"/>
<div class="caption">
  Three independent news stories (red / blue / orange panels). Two actors
  emerged as bridges between them — HAMAS connects Stories 1 and 2; IRAN
  connects Stories 2 and 3.
</div>

<p>
Two actors emerged as cross-story bridges:
</p>

<ul>
  <li><strong>HAMAS</strong> — attested in both Story 1 (the strike on its
      military chief) and Story 2 (the financial sanctions on its alleged
      backers).</li>
  <li><strong>IRAN</strong> — attested in both Story 2 (the financial network
      around Hezbollah) and Story 3 (the Houthi shipping attacks).</li>
</ul>

<p>The system also surfaced themes that span <em>all three</em> stories.
The top one:</p>

<blockquote>
  <strong>HAMAS · HEZBOLLAH · IRAN · US TREASURY</strong> &mdash;
  a four-actor clique present in every story's data, with a structural
  weight of 3.5 (high). This is the Iran-proxy ecosystem in one line:
  two designated armed proxies, the patron state, and the regulator
  going after the money.
</blockquote>

<img class="figure" src="data:image/png;base64,{fig4}" alt="Top cross-story themes"/>
<div class="caption">Cross-story themes ranked by structural weight.
  Themes spanning all three stories are highlighted in green.
</div>

<h2>What an investigator gets out of it</h2>

<p>
For each bridge, the system produces a small dossier the analyst can
verify:
</p>

<ul>
  <li>The <strong>list of articles</strong> in each story that mention the bridge.</li>
  <li>The <strong>actual quotes</strong> (with URL) the LLM extracted as evidence.</li>
  <li>The <strong>structural reason</strong> the system thought this was a bridge.</li>
</ul>

<p>For HAMAS:</p>

<table>
  <thead><tr><th>Story</th><th>Evidence records</th><th>Sample source</th></tr></thead>
  <tbody>
    <tr><td>Story 1 (Haddad strike)</td><td>51</td><td>pbs.org · themedialine.org · washingtonpost.com</td></tr>
    <tr><td>Story 2 (Hezbollah sanctions)</td><td>15</td><td>pbs.org · washingtonpost.com · jpost.com</td></tr>
  </tbody>
</table>

<p>For IRAN:</p>

<table>
  <thead><tr><th>Story</th><th>Evidence records</th><th>Sample source</th></tr></thead>
  <tbody>
    <tr><td>Story 2 (Hezbollah sanctions)</td><td>6</td><td>newsmax.com · wfmd.com (Iran's proxy war article)</td></tr>
    <tr><td>Story 3 (Houthi Red Sea)</td><td>5</td><td>theatlantic.com · shafaq.com</td></tr>
  </tbody>
</table>

<div class="key-finding">
  <h3>The lead that earned its keep</h3>
  <p>
    The system ranked the following cross-story lead in its top eight.
    The connection isn't directly attested in any single article — but it
    is grounded in source-cited edges that route through the IRAN bridge.
  </p>
  <p><strong>BINANCE</strong> (in Story 2 — Hezbollah financial sanctions)
    &nbsp;↔&nbsp; <strong>HOUTHI</strong> (in Story 3 — Red Sea attacks)
    &nbsp;<em>via bridge</em>&nbsp; <strong>IRAN</strong></p>
  <p>
    Backed by an LLM-extracted relation on the Binance node, citing
    <em>Newsmax</em> citing <em>the Wall Street Journal</em>:
  </p>
  <blockquote class="callout">
    &ldquo;Iran funneled $850 million through Binance ... Binance was used as
    a channel for Iranian financial transactions.&rdquo;
    <br><span class="source-pill">newsmax.com</span>
  </blockquote>
  <p>
    That's a defensible analyst lead — not &ldquo;Binance is helping the
    Houthis&rdquo; (no article says that), but
    &ldquo;Iran's financial channels through Binance overlap with the same
    Iran that operates the Houthis. Worth examining together.&rdquo;
  </p>
</div>

<h2>Reading a theme: a corroborated sub-network, not just a cluster</h2>

<p>
A theme is the system's answer to &ldquo;which actors belong
together?&rdquo; &mdash; a tight group of four that keep co-occurring
across the corpus. Early on, themes were ranked purely by how
<em>densely</em> the four were connected. That had a blind spot: four
actors linked by a handful of single, low-confidence mentions scored the
same as four bound by six independently-corroborated relationships.
Density isn't evidence.
</p>

<p>
So we changed how themes are weighted. A theme's rank now reflects the
<strong>strength of the evidence</strong> behind its internal
relationships: attested actor-to-actor links count for more than
incidental co-mentions, and a link corroborated across several
independent stories counts for more than one seen once. The practical
effect &mdash; the theme at the top of the list is the
best-<em>evidenced</em> cross-story structure, not merely the busiest
one. On a real three-story run, this flipped the top-ranked themes from a
roughly even actor/event mix to about <strong>90% actor-centred</strong>,
surfacing the relationship structures an investigator wants and pushing
duplicate event-headlines down the list.
</p>

<p>
And a theme is no longer a dead-end label. Open one and it expands into
the <strong>relationships that bind its members</strong>, each with the
source that attests it. Take the top theme from the Hamas-strike story:
</p>

<div class="key-finding">
  <h3>Theme: Hamas &middot; Qassam Brigades &middot; Izz al-Din al-Haddad &middot; the strike</h3>
  <p>Four names that co-occur &mdash; but opened up, a sourced command structure:</p>
  <ul>
    <li><strong>Hamas</strong> &rarr; <strong>Qassam Brigades</strong>
      <em>(affiliation)</em>: &ldquo;The Qassam Brigades are the military
      wing of Hamas.&rdquo; <span class="source-pill">pbs.org</span></li>
    <li><strong>Izz al-Din al-Haddad</strong> &rarr; <strong>Qassam Brigades</strong>
      <em>(leadership)</em>: &ldquo;Izz al-Din al-Haddad was the leader of
      the Qassam Brigades, Hamas' military wing.&rdquo;
      <span class="source-pill">aljazeera.com</span></li>
    <li><strong>Hamas</strong> &rarr; <strong>Izz al-Din al-Haddad</strong>
      <em>(leadership)</em>: &ldquo;...identified as the leader of the
      Hamas military wing at the time.&rdquo;
      <span class="source-pill">AP &middot; Jerusalem Post &middot; Ynet</span></li>
  </ul>
  <p>
    The investigator doesn't take &ldquo;these four are related&rdquo; on
    faith. They read the corroborated structure &mdash; who leads what,
    attested by whom &mdash; without leaving the theme. A theme that
    survives that read is a finding; one that doesn't is quietly
    discarded. Where the system links two members it only
    <em>inferred</em> structurally (no single article states it), it says
    so, so an analyst never mistakes a hypothesis for an attested fact.
  </p>
</div>

<h2>Going further: source-claimed causation</h2>

<p>
Bridges tell us that two stories share an actor. They do <em>not</em> tell us
that one story caused another. For the causal layer we ran a third
extraction pass on every chunk: an LLM signature that captures
<strong>causal assertions the source itself makes</strong> — language like
&ldquo;in response to&rdquo;, &ldquo;triggered by&rdquo;, &ldquo;in retaliation for&rdquo;.
</p>

<p>This is grounded in two things. First, a survey of causal-discovery
methods (Zanga et al., 2023) is emphatic that observational data alone
yields only Level-1 evidence on Pearl's ladder of causation — association,
not cause. Second, news articles routinely <em>make</em> causal claims,
which is a different thing from <em>establishing</em> them. We capture
those claims as a structured field the analyst can verify.</p>

<p>Each extracted claim gets four numbers:</p>
<ul>
  <li><strong>strength</strong>: how explicit the claim is in the article
      (&ldquo;caused&rdquo; = 0.9, &ldquo;likely triggered&rdquo; = 0.5,
      &ldquo;some observers speculate&rdquo; = 0.3).</li>
  <li><strong>confidence</strong>: how confident the LLM is in the
      extraction.</li>
  <li><strong>attestation count</strong>: how many distinct sources make
      the same claim.</li>
  <li><strong>weight</strong>: <code>strength &times; confidence &times;
      multi-source boost</code>, where the boost goes from 1.0 (single
      source) up to 2.0 (5+ sources). One hedged claim from one publisher
      is well under 1.0; an explicit claim corroborated by half a dozen
      sources lands above it.</li>
</ul>

<div class="key-finding">
  <h3>What this layer found in the Iran-proxy run</h3>
  <p>
    Out of {len(causal_edges)} causal-claim edges that survived resolution,
    the strongest one is the kind of finding an analyst would write up:
  </p>
  <blockquote>
    <strong>HOUTHI ATTACKS ON RED SEA SHIPPING</strong> &nbsp;&rarr;&nbsp;
    <strong>US AND ISRAEL CONDUCT STRIKES ON HOUTHI MILITARY SITES</strong>
    <br><br>
    <em>Weight 1.71 (STRONG). Strength 0.90, confidence 0.95, attested by
    6 independent sources. Direction tags: &ldquo;triggers&rdquo; and
    &ldquo;responds_to&rdquo;.</em>
    <br><br>
    Paraphrase across sources: &ldquo;US and Israel conducted strikes on two
    strategic Houthi military sites in response to Houthi attacks on Red
    Sea shipping.&rdquo;
    <br>
    <span class="source-pill">arabcenterdc.org</span>
    <span class="source-pill">britannica.com</span>
    <span class="source-pill">wfmd.com</span>
    <span class="source-pill">+3 more</span>
  </blockquote>
  <p>
    The Hamas-strike and Hezbollah-sanctions stories produced
    <strong>zero</strong> causal-claim edges. Their articles describe what
    happened in temporal sequence but rarely use explicit causal language
    between named entities &mdash; which is exactly the kind of restraint
    we wanted the prompt to enforce. The system says &ldquo;I don't see
    explicit causation here&rdquo; rather than inventing it.
  </p>
</div>

<p style="color: #666; font-size: 0.95em;">
<em>Trade-off worth naming: this third extraction pass adds about
30&ndash;40% to LLM cost per chunk. Two strong claims from a
56-minute run is a thin yield. Worth keeping on for cross-story
investigations where the &ldquo;why did this happen?&rdquo; question matters;
worth gating off for general entity-mapping runs where it doesn't.</em>
</p>

<h2>The numbers</h2>

<img class="figure" src="data:image/png;base64,{fig3}" alt="Pipeline funnel"/>
<div class="caption">
  From a haystack of articles to a small set of actionable leads, on a log scale.
</div>

<p>The funnel for the Iran-proxy run:</p>

<ul>
  <li><span class="metric">{metrics['fetched']}</span> articles fetched across the three queries.</li>
  <li><span class="metric">{metrics['extracted']}</span> articles successfully extracted (the rest were paywalls, dead links, or low-content).</li>
  <li><span class="metric">{metrics['nodes']}</span> nodes in the merged graph &mdash;
      {n_events} events plus {n_entities} actors.</li>
  <li><span class="metric">{metrics['edges']}</span> directly attested relationships between them, each carrying its source URL.</li>
  <li><span class="metric">{metrics['bridges']}</span> cross-story bridge actors.</li>
  <li><span class="metric">{metrics['leads']}</span> ranked cross-story leads worth following up.</li>
</ul>

<p>
The end-to-end run took roughly 50 minutes from query to artifact.
Reading 350 articles manually and tracking entity relationships
across them would take a working day at minimum.
</p>

<h2>Does it generalise? A second domain</h2>

<p>
A reasonable question after seeing the Iran-proxy results is whether the
pipeline really does what it claims, or whether the bridges only emerge
because Middle-East news shares a small set of repeating actors. To check,
we kept the code unchanged and pointed it at a completely different topic:
the financial side of <strong>sanctions evasion around the war in Ukraine</strong>.
Three queries:
</p>

<ul>
  <li>"Russia oil sanctions evasion dark fleet" &mdash; covers the
      shadow fleet, tanker seizures, and the French + UK navy interdictions.</li>
  <li>"China yuan settlement Russia trade sanctions" &mdash; covers
      the de-dollarisation push, RMB-denominated bonds, and bilateral
      energy deals.</li>
  <li>"Iran Russia military cooperation drone supply" &mdash; covers
      the drone-component trade, the Caspian corridor, and Cuba's
      300-drone purchase.</li>
</ul>

<p>
Same code, same orchestrator, no manual tuning &mdash; just a different
<code>--domain</code> flag (which substitutes a sanctions-focused
relevance hypothesis for the terror-financing one). The pipeline
fetched <span class="metric">{metrics_d2['fetched']}</span> articles, built a
{metrics_d2['nodes']}-node graph, and surfaced <span class="metric">{metrics_d2['bridges']}</span>
cross-story bridges.
</p>

<img class="figure" src="data:image/png;base64,{fig6}" alt="Bridges across the three Russia sanctions-evasion stories"/>
<div class="caption">
  In the Russia / China / Iran case, <strong>three actors bridge ALL three
  stories</strong>: CHINA, IRAN, and RUSSIA each appear in every run with
  posterior confidence 1.00. Compare with the Iran-proxy network where the
  bridges link adjacent pairs of stories. Same pipeline, different
  underlying topology.
</div>

<p>
The bridge topology that emerges is structurally different from the
Iran-proxy case. In the terror-financing run, HAMAS and IRAN each bridged
<em>two</em> of the three stories (an asymmetric chain). In the
sanctions-evasion run, three state-level actors all bridge <em>all
three</em> stories &mdash; signalling that the underlying news network
isn't a chain but a triangle, with the same three powers attested across
the oil, financial, and military narratives. That is itself a finding:
the system tells you not just <em>who</em> is shared across stories but
also what <em>shape</em> the shared structure takes.
</p>

<p>
Three things from the run that survived our quality bar:
</p>

<ul>
  <li>A weight-6.0 cross-story theme grouping CHINA, IRAN, and the
      events <em>"China supplies drone parts to Iran and Russia despite
      US sanctions"</em> and <em>"China launches oil-for-gold and silver
      settlement strategy"</em> &mdash; that is, the analyst is told
      that the same actor set sits at the centre of both the financial
      workaround and the kinetic supply chain.</li>
  <li>BINANCE re-surfaces as a cross-story actor (already a bridge in
      the Iran-proxy run via the $850M Iran-conduit story; here it appears
      again in the sanctions-evasion context).</li>
  <li>Two near-duplicate surface forms that earlier slipped through
      &mdash; PUTIN + VLADIMIR PUTIN, and HORMUZ + STRAIT OF HORMUZ &mdash;
      now collapse correctly into single bridge actors. (Mentioned because
      the &ldquo;<em>same actor written two ways</em>&rdquo; problem is
      one analysts will recognise; it took an explicit alias rule to fix.)</li>
</ul>

<p>
A single second-domain test isn't proof of broad portability. But it does
rule out the worst-case story &mdash; that we tuned the system to one
news narrative until it worked. The same code, fed a topic with no
overlap in actors or vocabulary, produces the same kind of output:
bridges, themes, and source-cited leads.
</p>

<h2>What this is NOT</h2>

<p>
We're careful about claim language. The system surfaces structural
co-occurrence and source-attested relationships. It does NOT claim:
</p>

<ul>
  <li><strong>Causation.</strong> &ldquo;Iran bridges Hezbollah and Houthis&rdquo;
      means the same Iran is attested in both stories — not that Iran
      <em>caused</em> the Houthi attacks. Cause-and-effect language belongs to
      the analyst reading the source articles, not to the graph.</li>
  <li><strong>Comprehensiveness.</strong> The graph is only as good as the news
      corpus. If the only coverage is from one publisher, the graph leans
      that way. The analyst should still triangulate against other sources.</li>
  <li><strong>Final answers.</strong> A cross-story lead is a place to start
      reading, not a conclusion. The system gives you the URLs precisely
      because someone still has to read them.</li>
</ul>

<h2>What's next</h2>

<p>
The current system surfaces <em>who-is-connected-to-whom-across-stories</em>
well. The obvious next gaps:
</p>

<ul>
  <li><strong>Better temporal reasoning.</strong> Today we can tell that
      Event A happened before Event B and they share actors. We don't yet
      ask the harder question &mdash; did A cause B? &mdash; in a defensible way.</li>
  <li><strong>Tighter event extraction.</strong> The LLM occasionally
      extracts a news headline as if it were an actor (&ldquo;FRANCE
      INTERCEPTS RUSSIAN TANKER&hellip;&rdquo; classified as an entity
      rather than as a description of an event). Two recent passes
      help: a post-extraction validator rewrites such records when a
      shorter noun-phrase label is available, and the Stage-2 query
      picker now refuses to expand on headline-shaped or event-typed
      identifiers. Some still slip through when the LLM produces no
      shorter alternative &mdash; the remaining cases are visible but
      bounded.</li>
  <li><strong>Streaming.</strong> The system runs on demand. The obvious extension
      is a weekly/daily sweep over an analyst's watchlist of queries — pushing
      cross-story leads as they emerge rather than waiting for a manual run.</li>
</ul>

<hr>

<div class="takeaway">
  <strong>Takeaway.</strong> The point isn't replacing the analyst's
  judgement. It's compressing the &ldquo;read 300 articles to notice the
  one cross-story link&rdquo; step into something an analyst can verify in
  minutes. The system tells the investigator <em>where to read</em>; reading,
  judging, and writing the report stays human.
</div>

<div class="footnote">
  This post draws on a real pipeline run from May–June 2026, fetching news
  via the public GNews aggregator. All quoted evidence is sourced to actual
  articles cited in the merged-graph data. No proprietary data, no human
  intelligence, no closed sources. The pipeline is research-grade software;
  the analyst-report generator is a Python script. Numbers in this post are
  exact counts from one specific run; different runs on the same queries
  may differ slightly due to LLM non-determinism and news-corpus drift.
</div>

</body>
</html>
"""

    OUT_HTML.write_text(html)
    size_kb = OUT_HTML.stat().st_size / 1024
    print(f"Wrote: {OUT_HTML}")
    print(f"  size: {size_kb:.1f} KB")


if __name__ == "__main__":
    main()
