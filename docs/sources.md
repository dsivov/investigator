# Sources & enrichment

Investigator gathers text from configurable **search sources** during a run, and
can attach external company records to entities afterwards via **enrichment**.

---

## Search sources

Google News is the default. Each investigation can also pull from additional
sources, toggled per-source in the New-Investigation **Sources** step (and via
repeatable `--source` on the CLI). They fetch text about the subject and flow
through the identical extract → graph pipeline. Code:
`research/search_sources.py`.

| Source | Key needed | What it adds |
|---|---|---|
| **Wikipedia** | no | Encyclopedic article text (MediaWiki API). |
| **GDELT** | no | Global news coverage, broader than Google News. The free tier is **aggressively rate-limited** (1 request / 5s), so it can return nothing under load; we retry once on 429 and trim the query to distinctive terms, but it remains best-effort. |
| **OpenSanctions** | yes (`INVESTIGATOR_OPENSANCTIONS_KEY`) | Sanctions / PEP / watchlist entries. Shown as *needs key* until set. |
| **Web search** | no\* | Generic web results — Google Programmable Search if `INVESTIGATOR_GOOGLE_API_KEY` + `INVESTIGATOR_GOOGLE_CSE_ID` are set, else DuckDuckGo. Bodies extracted with newspaper3k. |

Each provider yields the standard article-dict shape and is independent: one that
fails or isn't configured is **skipped** — it never breaks a run. The UI lists
available sources at `GET /api/search-sources` (key-gated ones report
`available: false`). Adding a provider is one fetch function + one registry
entry.

CLI:

```sh
PYTHONPATH=.:src python research/cross_event_investigation.py \
  --domain terror_financing --period 1y \
  --event "subject:your query" \
  --source wikipedia --source gdelt --source websearch
```

Your **own documents** (PDF uploads + URLs) are handled separately by
`research/source_ingest.py` and are always included (they skip the relevance
cutoff but are still scored against the domain hypothesis).

---

## Entity enrichment (SEC EDGAR / OpenRegistry)

After a run, `research/enrichment.py` attaches external records to the top
company (`ORG`) entities — written to `<artifact>.enriched.json` and surfaced in
the customer report (and the **Sources** tab) under each entity as **External
records**. It is opt-in and makes network calls.

```sh
PYTHONPATH=.:src python research/enrichment.py <artifact.json> [--top-n 12]
```

From the app: the **Sources** tab has an **Enrich** button
(`POST /api/investigations/<id>/enrich`) that runs the same lookup and lists each
entity's records. The enriched artifact folds into the same investigation.

### Providers

- **SEC EDGAR** — works out of the box, no key. Identity + recent filings for US
  SEC filers (so it matches public multinationals; private / non-US companies
  won't match — that's OpenRegistry's job). Disable with `--no-edgar`.
- **OpenRegistry** — 30 national company registries (beneficial owners, officers,
  shareholders). Free tier, **no username/password** — auth is OAuth 2.1 with
  Dynamic Client Registration; the access token (~15 min) auto-refreshes from a
  stored refresh token. Disable with `--no-openregistry`.

Enrichment sends extracted entity *names* to these external services.

### Connecting OpenRegistry

One-time browser authorization. From the **app**: **Settings → OpenRegistry →
Connect** (or CLI `python research/enrichment.py --openregistry-login`). Tokens
persist at `~/.config/investigator/openregistry_oauth.json` and refresh
thereafter.

> **Browser gotcha.** Do the **Authorise** step in **Firefox**. Chrome / Edge /
> Brave block the provider's redirect from `https://…` back to
> `http://localhost`, so the login can't complete there. The Settings page also
> offers a paste-the-callback-URL fallback. A static
> `INVESTIGATOR_OPENREGISTRY_TOKEN` bearer skips OAuth entirely.

On a headless server, run the login on a machine with a browser and copy the
token file over (or point `INVESTIGATOR_OAUTH_DIR` at it).
