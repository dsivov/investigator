# Investigator — finding the hidden threads in news

**Investigator** is an OSINT analysis pipeline that reads a corpus of news
articles about several separate stories and surfaces the **actors, events, and
relationships that connect them** — the cross-story structure a human analyst
would otherwise have to find by reading hundreds of articles by hand.

It turns a bag of source material into a **scored knowledge graph**, detects the
**themes** (tight clusters of actors that recur together) and the **bridges**
(actors that appear in more than one story), and produces an analyst-grade
report in which **every claim is traced back to a source URL**.

---

## The problem

An investigator following a region or a topic juggles many parallel stories at
once. The interesting finding is rarely inside one story — it's the **actor who
shows up in two of them**: the financier named in a sanctions story who also
appears in a separate shipping story; the same intermediary in two unrelated
indictments. Finding those links means holding hundreds of articles in your head
and noticing the one name that crosses over.

Investigator does that crossover detection mechanically.

![Three stories, two bridges](docs/images/three_stories_bridges.png)

Three independent news searches. The two green-bordered nodes —
**HAMAS** and **IRAN** — are *bridges*: actors the system found attested in more
than one story. Two stories apart, but the same actor.

---

## How it works

![Pipeline](docs/images/pipeline.png)

The pipeline runs in six stages. The first two are extraction; the next three
turn a soup of per-article facts into a structured network; the last lets a
confidence calculation propagate across it.

1. **Fetch.** Search Google News for each query and download the most relevant
   articles. Bodies that fail to fetch (paywalls, blocks) are kept as
   **headline-only** records rather than dropped — a headline still carries
   entity signal. Beyond news you can enable **additional search sources**
   (Wikipedia, GDELT, OpenSanctions, generic web search — see below), and bring
   your **own sources** — upload PDFs or paste URLs — analysed alongside the
   fetch, or on their own (`--no-gnews`) to run the same graph machinery over a
   single case file or document.
2. **Extract.** An LLM reads each article and pulls out the **named actors**
   (people, organisations, places) and the **events** (concrete incidents:
   who did what to whom, when, where), plus any **source-claimed causation**.
3. **Merge evidence across articles.** Surface variants of the same actor
   ("Vladimir Putin" / "Putin" / "the Russian president") collapse into one
   node, and the union of articles that mention it becomes that node's
   **evidence list**. Relationships merge the same way — three articles
   asserting the same link become one edge of weight three carrying three URLs.
4. **Filter to the backbone.** Rank relationships by how many independent
   articles attest them, drop the singletons, but restore the **shortest path**
   from any orphaned actor back to the investigation subject so nothing relevant
   gets cut off.
5. **Triangulate (TMFG).** Build a chordal-planar **Triangulated Maximally
   Filtered Graph**. It decomposes into a tree of 4-cliques; each 4-clique is a
   **theme**. Edges TMFG adds that weren't directly attested become the system's
   **structural hypotheses**, kept distinct from facts.
6. **Confidence propagation.** A junction-tree belief-propagation pass over the
   clique tree adjusts each actor's confidence by the company it keeps — exact,
   because the graph is chordal.

For **cross-story analysis**, every query is fed into one session of the same
pipeline, and every node/edge is stamped with which query produced it. Actors
that appear in more than one query's data are the **bridges**.

A full diagram of the data flow is in [`FLOW.md`](FLOW.md).

---

## Themes you can interrogate

A *theme* is the system's answer to "which actors belong together?" — a tight
group of four that keep co-occurring. Themes are ranked by an **evidence-weighted
score**, so the top theme is the best-*corroborated* cross-story structure, not
merely the densest one. (Switching from connectivity-ranking to evidence-ranking
moved the top themes of a real three-story run from a roughly even actor/event
mix to ~90% actor-centred — surfacing the relationship structures an
investigator wants and pushing duplicate event-headlines down.)

![TMFG themes](docs/images/tmfg_themes.png)

The TMFG backbone with its top 4-clique themes shaded as polygons. Solid edges
are corroborated by a source; dashed edges are the structural **fill-in** the
algorithm adds — hypotheses to verify. Bridges sit where polygons intersect.

A theme isn't a dead-end label. Open one and it expands into the **relationships
binding its four members**, each with its source. A real example, from a
sanctions-evasion run:

> **Theme: CHINA · CIPS · IRAN · RUSSIA** — *evidence-weighted score 5.9*
> - **CHINA** ↔ **CIPS** *(ownership)*: "China operates and controls the
>   Cross-Border Interbank Payment System (CIPS), used to facilitate
>   yuan-denominated transactions… amid sanctions." `[cnbc.com]`
> - **CIPS** ↔ **RUSSIA** *(non-direct)*: "CIPS has seen increased usage by
>   Russian entities… following Western sanctions." `[…]`
> - **CIPS** ↔ **IRAN** *(non-direct)*: "CIPS volumes reached record highs in
>   global oil trade involving Iran." `[…]`

The theme names the *mechanism* (CIPS, China's yuan payment rail) binding the
sanctions network — not just four co-occurring names.

---

## A worked example: Iran's proxy network

Three independent searches from one run — an Israeli strike on a Hamas leader,
US Treasury sanctions on Hezbollah financiers, and Houthi Red-Sea attacks —
fuse into one graph:

![Real merged graph](docs/images/real_graph.png)

The actual merged graph the system built. Each story is one colour; the two
green bridges (**HAMAS**, **IRAN**) connect them. The thick red edge is a
**source-claimed causation** the analysis preserved verbatim.

### What an investigator gets

For each bridge, a small dossier they can verify: the articles in each story that
mention it, the **actual quotes** (with URL) extracted as evidence, and the
structural reason the system flagged it. And ranked cross-story **leads** like:

> **BINANCE** (sanctions story) ↔ **HOUTHI** (Red-Sea story) *via bridge* **IRAN**
>
> Grounded in a source-cited relation on the Binance node:
> *"Iran funneled \$850 million through Binance… used as a channel for Iranian
> financial transactions."* — `newsmax.com`

That's a defensible analyst lead — not "Binance is helping the Houthis" (no
article says that), but "Iran's financial channels through Binance overlap with
the same Iran that operates the Houthis. Worth examining together."

---

## Search sources you can enable

Google News is the default, but each investigation can pull from additional
sources, toggled per-source in the New-Investigation **Sources** step (and via
repeatable `--source` on the CLI). They fetch text about the subject and flow
through the identical extract → graph pipeline. A source that fails or isn't
configured is skipped — it never breaks a run.

| Source | Key needed | What it adds |
|---|---|---|
| **Wikipedia** | no | Encyclopedic article text about the subject (MediaWiki API). |
| **GDELT** | no | Global news coverage, broader than Google News (free tier is rate-limited). |
| **OpenSanctions** | yes (`INVESTIGATOR_OPENSANCTIONS_KEY`) | Sanctions / PEP / watchlist entries; shows as *needs key* until set. |
| **Web search** | no* | Generic web results — Google Programmable Search if `INVESTIGATOR_GOOGLE_API_KEY`+`INVESTIGATOR_GOOGLE_CSE_ID` are set, else DuckDuckGo. |

---

## Connecting the dots: hidden relationships between entities

The whole graph can be dense. When you want to ask a focused question — *how are
**these** specific actors/events related?* — select any set of them in the
**Data** tab and build a **Connections** subgraph on demand. Three modes:

- **Shortest path** — the single thinnest route between each selected pair, plus
  the intermediary ("connector") entities it passes through.
- **Hidden (indirect)** — the interesting one. It finds the *non-obvious*
  multi-hop chains (up to *k* distinct shortest paths per pair), so links that
  run through intermediaries surface **even when a direct edge already exists**.
  Intermediaries are ranked by **betweenness** and the central ones flagged as
  **brokers** — the entities that actually bind the selection together.
- **Direct only** — just the edges that exist directly among the selection.

Pathfinding runs on the *relationship* edges only (the evidence-to-root scaffold
is excluded, so connections aren't faked through the hub).

> **Example (Netanyahu corruption run).** Select **Arnon Milchan** and **Shaul
> Elovitch**. *Shortest path* shows only the obvious
> `Milchan → Netanyahu → Elovitch`. *Hidden* mode also surfaces
> `… → Bezeq → Elovitch` and `… → Walla! News → Elovitch` — the actual Case-4000
> entities — and flags **Netanyahu** as the broker. The indirect structure a
> shortest path hides.

Hit **Analyse** and the connected subgraph — actors, events, relationships, and
the computed paths — is sent to the LLM, which writes a short, evidence-grounded
report on *how* the selected entities interconnect (naming each chain and what
each broker bridges).

---

## Fact-checking: how many sources agree

Corroboration — the core of fact-checking — is surfaced throughout the UI at two
levels:

- **Claim-level badges** on every actor and every piece of evidence (Data tab):
  **weak** (1 source), **moderate** (2), **strong** (3+) — by how many
  *independent* sources confirm the **same claim**. Claims are clustered by
  meaning (paraphrases count together), and near-identical **syndicated** copies
  collapse to one source — so a wire story reprinted by 20 outlets is *not*
  mistaken for 20 confirmations.
- **Confidence boost** in the scoring: an entity attested by more independent
  sources is pushed further from neutral, so well-corroborated actors rank above
  single-source ones (a single source still counts, just less).

---

## A cumulative knowledge graph across investigations (optional)

With the analytics engine enabled (`--analytic_engine_enabled`), each finished
investigation's graph accumulates into **one persistent knowledge graph**
(in-code LightRAG, no separate server) stored outside the code tree at
`~/.local/share/investigator/kg` (override with `INVESTIGATOR_KG_STORE`). Later
investigations can draw on what earlier ones found.

- **Cross-investigation canonicalization.** A conservative layer keeps the same
  real-world entity from fragmenting across runs — it auto-merges only safe name
  variants (exact + formatting) and routes fuzzy matches to a review log rather
  than risking a wrong, permanent merge.
- **Nothing is lost.** LightRAG's graph keeps only a fixed schema, so a sidecar
  **structured store** preserves every property we build — belief scores,
  evidence (with confidence + source URLs), labels, themes, per-edge relation
  type/context, hypothesis flags, and which investigations attested each item —
  keyed by the same canonical names and merged across runs.
- **Query it from the UI.** The **Knowledge Base** tab asks questions across
  *everything* seen in all investigations: it returns the structured entities
  and relationships (entity-anchored *hybrid* retrieval) plus an optional
  LLM-synthesised answer, and each entity expands to show its belief score,
  corroborating evidence, source links, and the investigations it appears in.
- **Pre-seeding.** When a new investigation starts it is pre-seeded with what the
  KG already knows about its subject, surfaced alongside the fresh findings.

---

## What this is NOT

- **Not a causation engine.** It surfaces co-occurrence and source-attested
  relationships. Causation appears only where a source article itself asserts it.
- **Not comprehensive.** The graph is only as good as the news corpus.
- **Not a final answer.** A cross-story lead is a place to start reading — the
  system gives you the URLs precisely because someone still has to read them.

---

## Architecture

Three processes:

```
 ┌────────────────────┐   spawns    ┌──────────────────────┐   HTTP POST   ┌─────────────────────┐
 │  Frontend (Svelte) │  /api proxy │  UI backend (Flask)  │ ───────────▶  │  Pipeline engine     │
 │  Vite dev :5180    │ ──────────▶ │  ui/server  │               │  python -m investigator  │
 │  graph / themes /  │             │  :5050  REST + SSE   │ ◀───────────  │  :5003               │
 │  data / report UI  │ ◀────────── │  job queue + reports │   graph JSON  │  NER · graph · TMFG  │
 └────────────────────┘             └──────────────────────┘               │  · belief propagation│
                                                                            └─────────────────────┘
```

- **Pipeline engine** (`python -m investigator`, port **5003**) — the core: entity +
  event extraction (dspy + GPT-4.1), evidence consolidation, graph build, the
  corroboration filter, TMFG triangulation, and junction-tree belief propagation.
- **UI backend** (`ui/server.py`, port **5050**) — REST + SSE API
  (see [`docs/UI_API.md`](docs/UI_API.md)). Runs investigations as subprocesses
  that POST to the engine, streams progress, generates customer reports, and
  serves the Cytoscape-ready graph/theme payloads.
- **Frontend** (`ui/`, Svelte 5 + Vite, port **5180**) — the investigator UI:
  New-Investigation wizard (domain-aware query refinement + a vetoable review
  step, plus a Sources step for adding your own PDFs/URLs), live progress, the
  Graph / TMFG-themes / Data / Report / Sources tabs, on-demand **Connections**
  analysis (select entities → hidden-relationship subgraph + LLM summary),
  per-actor/per-evidence **corroboration** badges, a **Knowledge Base** tab
  (query the cumulative cross-investigation KG), and a **Settings** page for
  connecting data providers.

---

## Running locally

### Prerequisites

- **Python** environment with the pipeline dependencies: `dspy`, `networkx`,
  `flask`, `python-dotenv`, `gnews`, `newspaper3k`, `googlenewsdecoder`,
  `semhash`, `wordllama`, `matplotlib`, `pymupdf`.
- The engine is **self-contained**: run it with `PYTHONPATH=src` (the package
  lives in `src/investigator`); no external sibling packages required.
- **Node.js** (18+) for the frontend.
- An **OpenAI API key**.

### 1. Secrets

Copy the template and fill in your key (the real `.env` is git-ignored):

```sh
cp .env.example .env
# edit .env and set OPENAI_API_KEY
```

### 2. Start the pipeline engine (port 5003)

```sh
INVESTIGATOR_TMFG=1 INVESTIGATOR_VIZ=1 INVESTIGATOR_DISABLE_CACHE=1 \
  PYTHONPATH=src:. \
  python -m investigator
```

`INVESTIGATOR_TMFG=1` enables the theme/network-analysis stages.

### 3. Start the UI backend (port 5050)

```sh
PYTHONPATH=.:src python ui/server.py --port 5050
```

It auto-discovers any past investigation artifacts under
`news_investigations/cross_event/` and exposes them via the API. Add
`--host 0.0.0.0` to reach it from another machine on the LAN (the Vite dev
server already binds all interfaces).

To accumulate finished investigations into the cumulative knowledge graph (so
the **Knowledge Base** tab has data), start the **engine** with
`--analytic_engine_enabled`.

### 4. Start the frontend (port 5180)

```sh
cd ui
npm install
npm run dev          # serves http://localhost:5180, proxies /api -> :5050
```

Open **http://localhost:5180**.

### Useful environment variables

| Variable | Effect |
|---|---|
| `OPENAI_API_KEY` | LLM access (engine, and the UI's query-refinement endpoint). |
| `INVESTIGATOR_TMFG=1` | Enable TMFG themes + belief propagation (required for the themes tab). |
| `INVESTIGATOR_DISABLE_CACHE=1` | Disable the LLM response cache. |
| `INVESTIGATOR_TMFG_UNIFORM_WEIGHTS=1` | Restore the old topology-only theme weighting (default is evidence-aware). |
| `INVESTIGATOR_UI_MAX_CONCURRENT` | Max concurrent investigations the UI backend runs (default 1). |
| `INVESTIGATOR_CORRO_GAIN` / `INVESTIGATOR_CORRO_CAP` | Multi-source corroboration confidence boost (default gain 0.35, cap 8). |
| `INVESTIGATOR_CLAIM_SIM` / `INVESTIGATOR_SYNDICATION_SIM` | Claim-clustering / syndication thresholds for the fact-checking badges (default 0.78 / 0.97). |
| `INVESTIGATOR_KG_LLM_MODEL` | OpenAI model for the cumulative-KG layer (default `gpt-4.1-mini`). |
| `INVESTIGATOR_KG_STORE` | Cumulative-KG store directory (default `~/.local/share/investigator/kg`). |
| `INVESTIGATOR_OPENSANCTIONS_KEY` | API key enabling the OpenSanctions search source. |
| `INVESTIGATOR_GOOGLE_API_KEY` / `INVESTIGATOR_GOOGLE_CSE_ID` | Google Programmable Search for the web-search source (else DuckDuckGo). |

---

## Running an investigation from the CLI (no UI)

```sh
PYTHONPATH=.:src python research/cross_event_investigation.py \
  --domain sanctions_evasion --period 30d \
  --event "russia_oil:Russia oil sanctions evasion dark fleet 2026" \
  --event "china_yuan:China yuan settlement Russia trade sanctions 2026" \
  --event "iran_drone:Iran Russia military cooperation drone supply 2026"
```

Add extra search sources with repeatable `--source` (e.g.
`--source wikipedia --source gdelt --source websearch`).

Then turn the artifact into a customer report:

```sh
python research/build_customer_report.py news_investigations/cross_event/<artifact>.json
```

### Analysing your own documents

Add PDFs or URLs as extra sources, with or without a news fetch. With
`--no-gnews` the pipeline runs purely over what you supply — e.g. a single case
file under the `criminal_investigation` domain:

```sh
PYTHONPATH=.:src python research/cross_event_investigation.py \
  --domain criminal_investigation --no-gnews \
  --event "case:GBH stabbing investigation" \
  --extra-pdf /path/to/report.pdf --extra-url https://example.com/filing
```

---

## Enriching entities (optional)

After a run, `research/enrichment.py` can attach external records to the top
company (`ORG`) entities in the graph — written to `<artifact>.enriched.json`
and surfaced in the customer report under each entity as **External records**.
It's opt-in and decoupled from the engine (it makes network calls).

```sh
PYTHONPATH=.:src python research/enrichment.py <artifact.json> [--top-n 12]
```

Two free providers:

- **SEC EDGAR** — works out of the box, no key. US public-company identity +
  recent filings.
- **OpenRegistry** — 30 national company registries (beneficial owners,
  officers, shareholders). Free tier, and crucially **no account and no API
  key**: auth is OAuth 2.1 with Dynamic Client Registration. Authorise **once**:

  ```sh
  PYTHONPATH=.:src python research/enrichment.py --openregistry-login
  ```

  This opens OpenRegistry's authorisation page in your browser — you just click
  **Authorize** (there is *no* username/password; optionally enter an email to
  raise the free limit from 20→30 rpm). The tokens (incl. a refresh token) are
  stored at `~/.config/investigator/openregistry_oauth.json` and **auto-refresh**
  thereafter, so you only do this once. On a headless server, run the login on a
  machine with a browser and copy that file over (or point `INVESTIGATOR_OAUTH_DIR`
  at it).

  You can also do this **without the CLI**, from the app: **Settings →
  OpenRegistry → Connect**, which runs the same one-time browser authorisation
  and shows live connection status. *Tip:* do the **Authorise** step in
  **Firefox** — Chrome/Edge/Brave block the provider's redirect back to
  `localhost`, so the login can't complete there (the Settings page also offers
  a paste-the-callback-URL fallback).

Disable a provider with `--no-edgar` / `--no-openregistry`. Note: enrichment
sends extracted entity *names* to these external services. You can also run
enrichment **from the app** — the **Sources** tab has an *Enrich* button that
runs the same lookup and lists each entity's external records.

---

## Repository layout

```
src/investigator/            Pipeline engine: NER, graph build, dedup/merge,
                         corroboration filter, TMFG, junction-tree BP.
  graph/connector.py         Connections subgraph (shortest-path / hidden / brokers)
  graph/corroboration.py     Claim-level multi-source corroboration (fact-check badges)
  analytics/                 Cumulative KG: in-code LightRAG merge, cross-
                             investigation canonicalization, structured_store
                             (preserves all node/edge props), retrieval
research/
  cross_event_investigation.py   CLI driver for a multi-query run
  search_sources.py              Wikipedia / GDELT / OpenSanctions / web providers
  enhanced_retrieval.py          Query-expansion + rerank + entity-deepening
  build_customer_report.py       Analyst-grade markdown report generator
  build_graph_prototype.py       Cytoscape graph-payload + standalone prototype
  build_tmfg_prototype.py        TMFG-themes payload + standalone prototype
  build_full_ui_prototype.py     Single-file six-tab UI prototype
  build_blog_post.py             Generates the illustrated blog post
  domain_presets.py              Per-domain relevance hypotheses
ui/                      Svelte 5 + Vite frontend (the investigator UI)
  server.py              UI backend (REST + SSE) — see docs/UI_API.md
docs/
  UI_API.md              REST + SSE contract
  images/                README figures
FLOW.md                  Graph-creation pipeline diagram
news_investigations/     Run artifacts + job state (git-ignored)
```

---

## Method notes

- **Confidence language** in reports follows ICD-203 analytic standards
  (Almost Certain / Highly Likely / Likely / …).
- **Themes** are ranked by an evidence-weighted score (attested actor-to-actor
  links and cross-run corroboration count for more than incidental co-mentions).
- **No closed sources, no human-intelligence, no open-web crawling** beyond the
  publisher pages the news aggregator returns.

This is research-grade software. Numbers in the examples are exact counts from
specific runs; different runs on the same queries may differ due to LLM
non-determinism and news-corpus drift.
