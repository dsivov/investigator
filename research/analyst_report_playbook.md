# Analyst Report Playbook — OSINTGraph merged-graph JSON

How to turn a OSINTGraph deep-investigation JSON (one merged graph after Stage 1 + Stage 2) into a credible, grounded OSINT-analyst-perspective report. Every claim in the report must trace back to a field in the JSON. The playbook covers source-JSON anatomy, the analytical method, section-by-section drafting, figures to produce, and quality-control checks. Reference implementation: `research/build_analyst_report.py`.

---

## 1. Source JSON anatomy

Top-level keys of the deep-investigation artifact:

| Field | What it is | Use it for |
|---|---|---|
| `session_id` | Pipeline session identifier | Cite in methodology section |
| `seed_query` | Stage-1 GNews query string | Section header; "what was investigated" |
| `params` | Run parameters (article counts, period, top-N) | Methodology / scope |
| `stages[0]` | Stage-1 input + response | Compare initial vs merged graph |
| `stages[1]` | Stage-2 input + **merged-graph response** | **Primary analytical surface** |
| `stages[1].response` | The full merged graph (Stage 1 + Stage 2 alias-merged) | The graph the report describes |
| `final_response` | Identical to `stages[1].response`; kept for compat | Ignore — use stages[1].response |

### Per-node fields (`stages[1].response.nodes[*]`)

| Field | Meaning | Use in report |
|---|---|---|
| `identifier` | Canonical entity name (uppercased) | Section heads, label in figures |
| `score` | Raw structural prominence in the affiliation graph | Sort actors; size in figures |
| `posterior_prob` | Belief-propagation result over the TMFG (0..1) | Color in figures; cite when promoting |
| `posterior_delta` | Posterior change vs raw belief | Highlight entities the network LIFTED |
| `hypothesis` | True iff promoted_entity | Mark in figures |
| `triangulated` | True iff entity is part of TMFG | Filter substantive set |
| `themes` | List of theme ids the entity belongs to | Theme-by-theme write-up |
| `evidence` | List of records: `{doc_id, reasoning, confidence, ...}` | **Quote in actor briefs** |
| `data.relations` | Directed list: `{related_node, direction, relations:{type, context}, attributes:{source_url}}` | **Build relationship narrative** |
| `data.location`, `data.financial_restrictions`, etc. | Structured per-entity attributes | Profile box if present |
| `labels` | Alternate names / alias variants | Cross-reference, footnote |

### Top-level analytical fields (also in `stages[1].response`)

| Field | Meaning | Use in report |
|---|---|---|
| `edges` | All directed attested relations | Network figure |
| `themes` | 4-entity TMFG-clique clusters with `members`, `weight`, `posterior_mean` | Section table of contents |
| `promoted_entities` | Network-promoted nodes with `identifier`, `reason` | "Leads to examine" section |
| `hypothesis_edges` | Co-located pairs not directly attested (`endpoints`, `joint_confidence`, `rationale`, `via_theme`) | "Open questions" / further-reading |

---

## 2. Methodology — step by step

The flow is **always JSON-first**: don't invent narrative, derive it. Every paragraph in the final report should be traceable to specific fields in the source JSON.

### Step 1 — Inventory

Programmatically dump:

- Total counts: `len(nodes), len(edges), len(themes), len(promoted_entities), len(hypothesis_edges)`
- Per-node evidence count + relations count + posterior + score
- Top themes sorted by `weight` (descending)
- The Stage-2 entity-subqueries list (`stages[1].stage2_entity_subqueries`) — these are the entities the operator picked for follow-up
- Stage-1 ID set vs Stage-2 ID set — quantify what Stage 2 added

Output this as a working table; the report's exec-summary numbers come from here.

### Step 2 — Separate signal from noise

Mark every entity as one of:

- **Substantive subject** — appears in evidence with operational context (designations, sanctions, indictments, leadership relations)
- **Authority / actor** — government entity acting ON a subject (Treasury, DOJ, courts)
- **Source / publisher** — news outlet (NEW YORK POST, CNN, REUTERS)
- **Commentator / politician** — person quoted across many topics (HASAN PIKER, ILHAN OMAR, public-figure noise)
- **Ambiguous / duplicate** — alias-miss or NER collision (USA vs UNITED STATES vs THE USA; PCC-gang vs PCC-college)

Only **substantive subjects** + **authorities-in-context** anchor narrative clusters. The other categories appear in the data-quality caveats section.

### Step 3 — Cluster substantive subjects into narrative threads

Use the per-node `themes` field and the directed relations to find connected subject sets. Heuristics:

- Two subjects belonging to the same `themes[*]` member set with `weight >= 3` → likely the same narrative thread.
- Two subjects with a directed relation whose `relations.type` is one of `{ownership, leadership, affiliation, non_direct, partnership}` → same narrative thread.
- A subject appearing as `related_node` in multiple other subjects' `relations` arrays → cluster anchor.

For each cluster pick a **title** that names the cluster (e.g., "Brazilian crime gangs designated as terrorist organisations"). The title comes from reading the relation `context` strings in the cluster — paraphrase what they collectively assert.

### Step 4 — Build per-actor briefs

For each anchor entity in a cluster, extract:

- **Header**: identifier, posterior, posterior_delta, provenance (Stage-1 / Stage-2-query / Stage-2-new)
- **Attested relations** (from `data.relations`): direction, type, related_node, context string (verbatim), source_url
- **Strongest evidence** (from `evidence`, sorted by `confidence` desc, take top 2-3): reasoning string + doc_id (URL)

Keep quotations short (≤ 250 chars per context, ≤ 380 chars per reasoning). Always include the source URL in the bullet.

### Step 5 — Promote leads, not subjects

`promoted_entities` is **leads, not findings**. The network's reason for promoting is structural (clique-mate of high-posterior subjects), not evidentiary. In the report:

- Treat as "examine further" candidates, never as established subjects.
- Filter out commentator/politician noise from this list before printing.
- Include the network's `reason` string verbatim so the reader knows it's structural.

### Step 6 — Document data-quality caveats

Walk the JSON looking for:

- **Alias misses** — pairs like `(US TREASURY, U.S. DEPARTMENT OF THE TREASURY)`, `(PFLP, POPULAR FRONT FOR THE LIBERATION OF PALESTINE (PFLP))`, `(TDA, TDA GANG)`. Flag every pair you find.
- **NER collisions** — same identifier referring to different real-world entities (PCC-gang vs PCC-college). Check the relations for off-topic context (e.g., a "gang" with relations to "graduating class" or "contract action team meetings").
- **High-degree commentators** — persons appearing in many themes with thin substantive evidence.
- **Generic geo-entities** — `USA`, `UNITED STATES` etc.; useful for context but rarely the subject.
- **Coverage window** — the period parameter limits historical depth.

Every caveat goes in the Data-quality section. Findings sections should briefly acknowledge ambiguity when it affects a specific claim.

### Step 7 — Compose the report

Section order (see §3) is fixed. Word-economy targets: exec summary ≤ 250 words, each per-cluster section ≤ 600 words including actor briefs, caveats ≤ 250 words, methodology ≤ 200 words.

### Step 8 — Render & verify

Generate three figures (see §4), assemble Markdown, convert to PDF (pandoc + pdflatex). Before delivery, **spot-check three claims at random** by following the source URL on the page and verifying the article supports the claim. If any fails, fix the report.

---

## 3. Standard section template

```
# Executive summary
  - What was investigated (seed query, time window, scope numbers)
  - Headline findings (3-5 cluster titles, one sentence each)
  - One sentence on data-quality position

# Methodology
  - Two-stage flow (Stage 1 broad seed, Stage 2 entity follow-ups)
  - Cross-stage merge with alias-aware dedup
  - Posterior + TMFG layer (one sentence on what the network adds)
  - Grounding rule: nothing in findings is invented; everything quotes the JSON

# Network overview
  - Embed fig_network.png
  - Caption explains colour (posterior), size (signal), edge styles

# Findings
  ## Cluster 1 title
    1-paragraph summary of the cluster
    ### Anchor entity 1
       - Attested relations
       - Strongest evidence
    ### Anchor entity 2
       ...
  ## Cluster 2 title
    ...
  ## etc.

# Top entities by attested signal
  - Embed fig_top_entities.png
  - Caption explains what the bar shows

# Structural themes
  - Embed fig_themes.png
  - Caption explains TMFG triangle weight

# Network-surfaced leads (promoted entities)
  - Bulleted list of substantive promoted entities with the network's reason
  - Explicit caveat: leads, not findings

# Data-quality caveats
  - Numbered list (alias misses, NER ambiguity, commentator noise, coverage window)

# Sources used
  - Deduplicated URL list from evidence[*].doc_id
```

---

## 4. Figures to produce

| Figure | What it shows | Recipe |
|---|---|---|
| `fig_network.png` | Full merged graph at a glance | spring layout; node fill = posterior; node size = score + evidence + relations; labels only on score >= 0.34 or evidence >= 2; hypothesis edges dashed |
| `fig_top_entities.png` | Which entities the corpus actually attests | horizontal stacked bar: evidence + relations per entity, top 15 |
| `fig_themes.png` | Network's structural clustering | horizontal bar of top 10 themes by `weight` with member-list labels |

Render with matplotlib (Agg backend), embed in Markdown with `![]( name.png ){ width=100% }`, render the PDF with pandoc + pdflatex (no unicode-math.sty needed). ASCII-fold the Markdown before pandoc to avoid LaTeX Unicode breakage.

---

## 5. Quality control — what the report must NOT do

1. **Never invent facts.** If the JSON doesn't carry it, it doesn't go in the report. No external knowledge supplements without explicit caveat.
2. **Never treat network position as evidence on its own.** A promoted entity is a lead. The structural reason goes in the bullet so the reader can judge.
3. **Never collapse alias-miss duplicates silently.** Note them in caveats so the reader knows the underlying actor is one entity.
4. **Never include commentator co-occurrence as substantive involvement.** Filter HASAN-PIKER-class noise out of the actor briefs.
5. **Never assert a relation without quoting the `context` string and citing the `source_url`.** A relation without source is a lead; treat as such.
6. **Never present `hypothesis_edges` as findings.** They're "examine these together" leads; they earn at most a sentence in the leads section.
7. **Never trust the merged-graph `themes` blindly.** Themes can be dominated by broad-context entities (USA, UNITED STATES) — only themes anchored on subject actors deserve sections.

---

## 6. Honest limitations to state explicitly

Every news-corpus report inherits these limits — name them in the caveats section so the reader doesn't over-trust the output:

- **Operational detail is thin.** News prose surfaces frames and designations; it rarely lists defendants by name, exact financial flows, specific dates of operational events. Curated OSINT dossiers do that; news does not.
- **Entity granularity follows article density.** Subjects that the corpus mentions once or twice will be tagged with one relation and one evidence record. Don't over-weight a single-mention finding.
- **Locations are weakly extracted from news prose.** The `data.location` field is often "Not found" even when the article body mentions cities/regions.
- **Timeline events from news are unreliable.** The pipeline's `timeline_events` extraction picks up publication metadata as much as actual dated facts; on news input we currently filter these out programmatically and rely on prose dates inside relation contexts.
- **Coverage window** — claims older than the configured period only surface when the corpus references them. Historical depth is shallow.

---

## 7. Reference run

A worked example using this playbook end-to-end:

- Source JSON: `news_investigations/deep/material_support_designated_terror_groups_2026_20260531_101806.json`
- Generated PDF: `news_investigations/deep/material_support_designated_terror_groups_2026_20260531_101806_report.pdf`
- Generator script: `research/build_analyst_report.py`

Run a new investigation through the pipeline:

```
python research/build_analyst_report.py news_investigations/deep/<run_id>.json
```

The script writes the report PDF next to the JSON and the three figures into `<run_id>_report/`.
