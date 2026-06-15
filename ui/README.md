# OSINTGraph UI

Svelte 5 + TypeScript + Vite + Tailwind. Talks to the Flask backend
defined in `ui/server.py` (port 5050 by default).

## Run locally

```sh
# 1. Start the backend (in repo root)
PYTHONPATH=.:src \
  /home/dsivov/.conda/envs/tangos/bin/python ui/server.py

# 2. Install + start the frontend (in this directory)
pnpm install        # or npm install / yarn
pnpm dev            # opens http://localhost:5173
```

Vite proxies `/api/*` to the Flask backend on `:5050`, so the same
fetch calls work in dev (proxy) and prod (same-origin).

## Project shape

```
src/
  main.ts                 # mount App.svelte
  App.svelte              # route dispatcher
  app.css                 # Tailwind + report typography
  lib/
    api.ts                # fetch wrapper around docs/UI_API.md
    types.ts              # response shapes
    colors.ts             # thread/theme palettes
    helpers.ts            # publisher(url), bridge-confidence, escape, ...
    router.ts             # 30-line hash router (no external dep)
  components/
    AppShell.svelte       # sidebar (nav + recent + backend status)
    InvestigationList.svelte
    InvestigationView.svelte
    Placeholder.svelte
    tabs/
      Overview.svelte     # cards, bridges, per-thread, themes shortlist
      Graph.svelte        # full Cytoscape integration
      Tmfg.svelte         # theme list wired; polygon overlay TODO
      Data.svelte         # sortable table, three views
      Report.svelte       # marked.js renders the customer report
      Sources.svelte      # bibliography + diversity meter
```

## What's done

- App shell with sidebar, recent runs, backend health dot
- Hash routing: `#/`, `#/investigations/:id`, `#/investigations/:id/:tab`, `#/new`, `#/domains`
- `InvestigationList` reads `/api/investigations`
- `InvestigationView` reads `/api/investigations/:id` then dispatches to the active tab
- `Overview` reads `/graph` + `/tmfg` and renders the dashboard cards
  (bridges with ICD-203 confidence pills, coverage, per-thread bars,
  themes shortlist, asymmetric-corpus banner)
- `Graph` is fully working: Cytoscape with fcose layout, thread / type
  / min-articles filter chips, layout selector, side panel with
  attested-relationship rendering identical to the inline prototype
- `Data` is fully working for all three views (entities, events,
  relationships), sortable + searchable, 500-row cap
- `Report` renders the customer-report markdown with auto-TOC and a
  download link
- `Sources` reads `/api/investigations/:id/sources` and renders the
  bibliography + diversity meter

- `Tmfg` is fully working: Cytoscape + live SVG polygon overlay that
  tracks pan/zoom, theme selection dims non-members + highlights the
  polygon, attested/fill-in edge toggle, top-N + cross-thread filters
- `#/new` is the full three-step wizard (shape → domain → queries)
  with per-run hypothesis/threshold override, advanced knobs, and
  `POST /api/investigations`; on launch it navigates to the new
  investigation
- `InvestigationView` subscribes to the SSE progress stream for any
  queued/running investigation: a live progress banner shows the
  current phase, a Cancel button hits `DELETE`, and on completion the
  record reloads so the tabs get real data; a failed run shows a
  "view log" link

## What's stubbed (TODO)

- **`#/domains`**: backend `GET /api/domains` is live; the wizard uses
  it. A standalone CRUD screen (create / edit / delete user domains)
  plus `POST/PATCH/DELETE /api/domains` is the remaining work.

## Extension pattern

Every page lives in `src/components/`. Adding a new tab:

1. Drop a `.svelte` file in `src/components/tabs/`
2. Reference it from `InvestigationView.svelte`
3. Add an entry to the `TABS` array there

Every API call lives in `src/lib/api.ts`. Adding a new endpoint:

1. Append the type to `src/lib/types.ts`
2. Append the method to `src/lib/api.ts`
3. Backend endpoint goes in `ui/server.py`
