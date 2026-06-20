"""OSINTGraph UI backend.

Single Flask app implementing the REST + SSE contract in docs/UI_API.md.
Wraps the existing pipeline scripts; does not replace the OSINTGraph
server itself (which still runs on :5003).

Run:
    PYTHONPATH=.:src \\
      /home/dsivov/.conda/envs/tangos/bin/python ui/server.py

Default port 5050. Pass --port to change.

Concurrency: a single subprocess per investigation. Job state lives in
memory (the in-process JOBS dict); on restart, in-flight jobs are
considered failed and their partial artifacts can still be browsed.
Suitable for single-user local-network deployment per docs/UI_API.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_file

# Reuse the payload builders so the frontend gets exactly the shape it
# already consumes from the inline prototype. This server lives in ui/ but
# reuses the analysis scripts in research/, so put research/ (+ src) on the path.
ROOT = Path(__file__).resolve().parent           # ui/
REPO = ROOT.parent                               # repo root
sys.path.insert(0, str(REPO / "research"))
sys.path.insert(0, str(REPO / "src"))

import build_graph_prototype as bg        # noqa: E402
import build_tmfg_prototype as bt         # noqa: E402
import build_customer_report as bcr       # noqa: E402
import domain_presets as dp               # noqa: E402

from investigator.graph.connector import connector_subgraph  # noqa: E402


# bg._payload now runs WordLlama claim-corroboration clustering, and the
# graph/entities/events/relationships/sources endpoints each rebuild it for the
# same artifact -- so memoise by (path, mtime). Small bounded cache.
_PAYLOAD_CACHE: dict[tuple[str, int], dict] = {}


def _graph_payload(path: Path) -> dict:
    key = (str(path), path.stat().st_mtime_ns)
    hit = _PAYLOAD_CACHE.get(key)
    if hit is None:
        if len(_PAYLOAD_CACHE) > 16:
            _PAYLOAD_CACHE.clear()
        hit = bg._payload(json.loads(path.read_text()))
        _PAYLOAD_CACHE[key] = hit
    return hit


# ---------------------------------------------------------------------------
# Lazy dspy LM for the query-refinement endpoint. Kept separate from the
# heavy pipeline orchestrator (which we do NOT import here) so the UI server
# stays light. Configured on first use; the OPENAI_API_KEY is loaded from
# the repo-root .env.
# ---------------------------------------------------------------------------

_REFINER = None


def _get_refiner():
    """Return a cached dspy.Predict for query refinement, or None if the
    LLM stack is unavailable (missing key / import error)."""
    global _REFINER
    if _REFINER is not None:
        return _REFINER if _REFINER is not False else None
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=str(ROOT.parent / ".env"), override=False)
        import dspy

        class RefineQuery(dspy.Signature):
            """Rewrite a broad news-search query into a focused query that
            targets a specific investigative domain.

            Rules:
            - Keep the core subject of the raw query.
            - Correct obvious misspellings of well-known proper names
              (people, organisations, places) to their standard spelling
              -- e.g. "Francesca Alabanese" -> "Francesca Albanese". Only
              fix names you are confident about; if unsure, leave the name
              as written rather than guess.
            - Add the domain-specific angle so a news search returns
              material relevant to the hypothesis, not generic coverage.
            - Output a plain search string of at most 12 words. No quotes,
              no boolean operators, no explanation.
            """
            raw_query: str = dspy.InputField()
            domain_name: str = dspy.InputField()
            domain_hypothesis: str = dspy.InputField()
            refined_query: str = dspy.OutputField()

        # Reuse the pipeline's model for consistency. One short call.
        lm = dspy.LM("openai/gpt-4.1", temperature=0.0, max_tokens=200)
        predictor = dspy.Predict(RefineQuery)
        # Bind the LM to this predictor's calls via a context wrapper.
        def _run(raw_query: str, domain_name: str, domain_hypothesis: str) -> str:
            with dspy.context(lm=lm):
                out = predictor(raw_query=raw_query, domain_name=domain_name,
                                domain_hypothesis=domain_hypothesis)
            return (out.refined_query or "").strip()

        _REFINER = _run
        return _REFINER
    except Exception as e:  # noqa: BLE001
        print(f"[refine] LLM unavailable: {e}", file=sys.stderr)
        _REFINER = False
        return None


_ANALYZER = None


def _get_analyzer():
    """Cached dspy predictor that summarises a connector subgraph into a report,
    or None if the LLM stack is unavailable."""
    global _ANALYZER
    if _ANALYZER is not None:
        return _ANALYZER if _ANALYZER is not False else None
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=str(ROOT.parent / ".env"), override=False)
        import dspy

        class AnalyseConnections(dspy.Signature):
            """You are an OSINT analyst. You are given a focused network selected
            from a larger investigation: SELECTED entities, the CONNECTOR
            (intermediary) entities that link them, the relationships (edges)
            between all of them, and supporting evidence.

            Your report MUST focus on the RELATIONSHIPS and CONNECTIONS between
            the entities -- NOT on summarising each entity in isolation:
            - Use the supplied CONNECTION PATHS as the backbone: each is the
              pre-computed shortest route linking a pair of selected entities.
              Explain every path -- direct, or indirectly through which connector
              entities -- naming the chain (e.g. "A -> X -> D").
            - Characterise each relationship -- its type, direction, and what it
              implies. Call out any connector entity that acts as a bridge or hub
              linking several others.
            - Where visible, describe the larger structure (hubs, clusters,
              chains) the links form.
            - Use the evidence ONLY to substantiate and characterise the links;
              do not list standalone facts that don't bear on a connection.
            - Reflect corroboration strength when stating how well-established a
              link is. Ground every statement in the supplied data; never invent
              links or facts.

            Output GitHub-flavoured Markdown:
            ## Summary  -- 2-3 sentences on the overall shape of the network and
            the main connection(s).
            ## Connections  -- one bullet per link or chain, each phrased as a
            relationship (e.g. "**A** is linked to **D** via **X**, who ...").
            Be specific and concise; no preamble.
            """
            network: str = dspy.InputField(desc="Selected/connector entities, relationships and evidence")
            report: str = dspy.OutputField()

        lm = dspy.LM("openai/gpt-4.1", temperature=0.2, max_tokens=1400)
        predictor = dspy.Predict(AnalyseConnections)

        def _run(network: str) -> str:
            with dspy.context(lm=lm):
                out = predictor(network=network)
            return (out.report or "").strip()

        _ANALYZER = _run
        return _ANALYZER
    except Exception as e:  # noqa: BLE001
        print(f"[analyze] LLM unavailable: {e}", file=sys.stderr)
        _ANALYZER = False
        return None


def _describe_connection_network(result: dict) -> str:
    """Render the CONNECTED part of a connector subgraph (nodes that take part
    in at least one edge) as text for the analyzer. Isolated/unreachable
    selections are omitted."""
    def _txt(v, limit: int = 0) -> str:
        # Graph fields can be str / None / list (e.g. an event's description),
        # so coerce before any string op.
        if isinstance(v, (list, tuple)):
            s = " ".join(_txt(x) for x in v)
        elif v is None:
            s = ""
        else:
            s = str(v)
        s = s.strip()
        return s[:limit] if limit else s

    edges = result.get("edges") or []
    connected = {x for e in edges for x in (e.get("source"), e.get("target")) if x}
    nodes = [n for n in (result.get("nodes") or []) if n.get("id") in connected]
    actors = [n for n in nodes if n.get("type") != "event"]
    events = [n for n in nodes if n.get("type") == "event"]
    selected = [n["id"] for n in nodes if n.get("role") == "selected"]
    connectors = [n["id"] for n in actors if n.get("role") == "connector"]

    # Roster first so the model frames the report around the connection
    # structure (which nodes to connect, which are intermediaries).
    lines: list[str] = [
        f"SELECTED ENTITIES (connect these): {', '.join(selected) or '(none)'}",
        f"CONNECTOR (intermediary) ENTITIES: {', '.join(connectors) or '(none)'}",
    ]
    # Pre-computed shortest path per selected pair -- the backbone of the report.
    pathlist = result.get("paths") or []
    if pathlist:
        lines += ["", "CONNECTION PATHS (pre-computed shortest route per selected pair):"]
        for p in pathlist:
            chain = " -> ".join(p.get("path") or [])
            hops = p.get("hops")
            label = "direct" if hops == 1 else f"{hops} hops"
            lines.append(f"- {chain}  ({label})")
    lines += ["", "RELATIONSHIPS (the connections -- source --[type]--> target : context):"]
    for e in edges:
        rel = e.get("rtype") or e.get("type") or "related"
        ctx = _txt(e.get("context"), 240)
        lines.append(f"- {e.get('source')} --[{rel}]--> {e.get('target')}" + (f" : {ctx}" if ctx else ""))
    if events:
        lines += ["", "EVENTS in this network:"]
        for n in events:
            d = n.get("data") or {}
            date = _txt(d.get("date"))
            desc = _txt(d.get("description"), 200)
            lines.append(f"- {n['id']}" + (f" ({date})" if date else "") + (f": {desc}" if desc else ""))
    # Evidence is secondary -- only to characterise the links, capped tight.
    lines += ["", "SUPPORTING EVIDENCE per entity (use only to characterise the links above):"]
    for n in actors:
        evs = sorted(
            n.get("evidence") or [],
            key=lambda e: (-(e.get("corroborationSources") or 0), -(e.get("strength") or 0)),
        )[:3]
        if not evs:
            continue
        corr = n.get("corroboration")
        lines.append(f"- {n['id']}" + (f" [{corr} corroboration]" if corr else "") + ":")
        for ev in evs:
            txt = _txt(ev.get("reasoning"), 220)
            if txt:
                pol = "supports" if ev.get("supports") else "contradicts"
                lines.append(f"    - ({pol}) {txt}")
    return "\n".join(lines)


ARTIFACTS_DIR = (ROOT.parent / "news_investigations" / "cross_event").resolve()
UPLOADS_DIR = (ROOT.parent / "news_investigations" / "uploads").resolve()
SCHEMA_VERSION = "1"

app = Flask(__name__)


# ---------------------------------------------------------------------------
# CORS (development convenience; tighten before production)
# ---------------------------------------------------------------------------

@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Idempotency-Key"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    resp.headers["X-OSINTGraph-Schema"] = SCHEMA_VERSION
    return resp


@app.route("/api/<path:_>", methods=["OPTIONS"])
def _preflight(_):
    return ("", 204)


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------

def _err(status: int, code: str, message: str, *, field: str | None = None):
    body = {"code": code, "message": message}
    if field:
        body["field"] = field
    return jsonify(body), status


# ---------------------------------------------------------------------------
# Investigation discovery
# ---------------------------------------------------------------------------

def _file_to_id(path: Path) -> str:
    """Deterministic short id from artifact filename. Stable across server
    restarts so URLs the UI bookmarks remain valid."""
    digest = hashlib.sha256(path.name.encode()).hexdigest()[:10]
    return f"inv_{digest}"


def _list_artifacts() -> dict[str, Path]:
    """Return id -> json-artifact-path map for every saved investigation."""
    out: dict[str, Path] = {}
    if not ARTIFACTS_DIR.exists():
        return out
    for p in sorted(ARTIFACTS_DIR.glob("cross_*.json")):
        out[_file_to_id(p)] = p
    return out


def _meta_from_artifact(inv_id: str, path: Path, *, deep: bool = False) -> dict:
    """Return the Investigation record for the docs/UI_API.md contract.

    deep=True loads the full graph to compute summary stats; deep=False
    returns the list-row shape (used by GET /investigations)."""
    stat = path.stat()
    created = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    if not deep:
        return {
            "id": inv_id,
            "title": _title_for(path),
            "createdAt": created,
            "status": "succeeded",
            "kind": "multi",
            "summary": _summary_shallow(path),
        }
    d = json.loads(path.read_text())
    # final_merged_graph is {} when an investigation found no articles; treat a
    # missing/empty graph as zero nodes/edges rather than crashing (-> HTTP 500).
    final = d.get("final_merged_graph") or {}
    nodes = final.get("nodes") or []
    edges = final.get("edges") or []
    bridges = final.get("bridging_entities", []) or []
    leads = final.get("cross_event_leads", []) or []
    themes = final.get("themes", []) or []
    cross_themes = [t for t in themes if t.get("is_cross_investigation")]
    per_event_states = d.get("per_event_states") or []
    fetched = sum(len(b) for s in per_event_states for b in s.get("article_batches", []))
    body_ok = sum(1 for s in per_event_states for b in s.get("article_batches", [])
                  for a in b if a.get("text"))
    headline_only = sum(1 for s in per_event_states for b in s.get("article_batches", [])
                        for a in b if not a.get("text") and (a.get("title") or "").strip())
    n_runs = len(d.get("events", []))
    all_threads = [b for b in bridges if len(b.get("runs") or []) >= n_runs]
    nodes_per_run: dict[str, int] = defaultdict(int)
    for n in nodes:
        for r in (n.get("runs") or []):
            nodes_per_run[r] += 1
    sparse = [r for r, c in nodes_per_run.items() if c <= 5]
    rich = [r for r, c in nodes_per_run.items() if c >= 20]
    asymmetric = bool(sparse and rich)
    params = d.get("params") or {}
    threads = []
    for ev in d.get("events", []):
        if isinstance(ev, dict):
            threads.append({"name": ev.get("name"), "query": ev.get("query")})
        else:
            threads.append({"name": str(ev), "query": ""})
    return {
        "id": inv_id,
        "title": _title_for(path),
        "kind": "single" if len(threads) <= 1 else "multi",
        "status": "succeeded",
        "domain": params.get("domain") or "general",
        "period": params.get("period") or "30d",
        "threads": threads,
        "params": {
            "stage1_articles": params.get("stage1_articles"),
            "stage2_articles_per_entity": params.get("stage2_articles_per_entity"),
            "top_n_entities": params.get("top_n_entities"),
            "relevance_threshold": params.get("relevance_threshold"),
        },
        "createdAt": created,
        "finishedAt": created,
        "summary": {
            "fetched": fetched,
            "extracted_full_body": body_ok,
            "extracted_headline_only": headline_only,
            "nodes": len(nodes),
            "edges": len(edges),
            "bridges": len(bridges),
            "bridges_all_threads": len(all_threads),
            "themes": len(themes),
            "cross_event_themes": len(cross_themes),
            "leads": len(leads),
            "asymmetric_corpus": asymmetric,
            "sparse_threads": sparse,
        },
        "artifacts": _artifact_index(inv_id, path),
    }


def _title_for(path: Path) -> str:
    """Pretty title derived from the artifact filename."""
    name = path.stem.removeprefix("cross_")
    # Strip the trailing YYYYMMDD_HHMMSS timestamp
    name = re.sub(r"_\d{8}_\d{6}$", "", name)
    parts = name.split("_")
    return " · ".join(p.replace("_", " ").title() for p in parts)


def _summary_shallow(path: Path) -> dict:
    """Cheap summary read from the artifact without parsing the full graph
    (still does a JSON parse -- but the artifact is bounded to ~30 MB)."""
    try:
        d = json.loads(path.read_text())
    except Exception:
        return {}
    final = d.get("final_merged_graph") or {}
    bridges = final.get("bridging_entities") or []
    n_runs = len(d.get("events") or [])
    return {
        "nodes": len(final.get("nodes") or []),
        "edges": len(final.get("edges") or []),
        "bridges": len(bridges),
        "bridges_all_threads": sum(1 for b in bridges if len(b.get("runs") or []) >= n_runs),
        "threads": n_runs,
    }


def _artifact_index(inv_id: str, json_path: Path) -> dict:
    """Map each known artifact to its download URL."""
    base = f"/api/investigations/{inv_id}/artifacts"
    return {
        "raw_json":           f"{base}/raw.json",
        "customer_report":    f"{base}/customer_report.md"   if json_path.with_suffix(".customer_report.md").exists() else None,
        "analyst_review":     f"{base}/analyst_review.md"    if json_path.with_suffix(".analyst_review.md").exists() else None,
        "graph_prototype":    f"{base}/graph.html"           if json_path.with_suffix(".graph_prototype.html").exists() else None,
        "tmfg_prototype":     f"{base}/tmfg.html"            if json_path.with_suffix(".tmfg_prototype.html").exists() else None,
        "full_ui":            f"{base}/full_ui.html"         if json_path.with_suffix(".full_ui.html").exists() else None,
    }


# ---------------------------------------------------------------------------
# Job tracking + persistence + worker pool
# ---------------------------------------------------------------------------
#
# Each investigation that gets POSTed becomes a Job placed on JOB_QUEUE.
# A single worker thread (cap: MAX_CONCURRENT, default 1) pulls jobs off
# the queue and runs the subprocess. State transitions are persisted to
# JOBS_DIR/<id>.json so cancelled/failed jobs are visible after a server
# restart. Stdout is mirrored to JOBS_DIR/<id>.log for post-mortem.
#
# Concurrency cap = 1 by default because each investigation consumes the
# OSINTGraph server's LLM rate budget; running two in parallel just slows
# both down without parallel wall-clock benefit.

JOBS_DIR = (ARTIFACTS_DIR.parent / "jobs").resolve()
JOBS_DIR.mkdir(parents=True, exist_ok=True)

MAX_CONCURRENT = int(os.environ.get("INVESTIGATOR_UI_MAX_CONCURRENT", "1"))

# Idempotency: client may send Idempotency-Key header. Within IDEMP_TTL_SECS
# we return the existing job; after the TTL the key is reusable.
IDEMP_TTL_SECS = 24 * 3600
IDEMP_INDEX: dict[str, tuple[str, float]] = {}      # key -> (inv_id, expiry_epoch)


class Job:
    __slots__ = ("id", "process", "queue", "subscribers", "status", "started",
                 "finished", "stdout_lines", "artifact_path", "spec", "lock",
                 "log_path", "state_path")

    def __init__(self, inv_id: str, spec: dict):
        self.id = inv_id
        self.spec = spec
        self.process: subprocess.Popen | None = None
        self.queue: queue.Queue = queue.Queue()
        self.subscribers: list[queue.Queue] = []
        self.status = "queued"
        self.started = datetime.now(timezone.utc).isoformat()
        self.finished: str | None = None
        self.stdout_lines: list[str] = []
        self.artifact_path: Path | None = None
        self.lock = threading.Lock()
        self.log_path: Path = JOBS_DIR / f"{inv_id}.log"
        self.state_path: Path = JOBS_DIR / f"{inv_id}.json"

    def emit(self, event: dict) -> None:
        with self.lock:
            for q in self.subscribers:
                q.put(event)
            self.queue.put(event)

    def persist(self) -> None:
        """Write a minimal recovery snapshot to disk."""
        try:
            payload = {
                "id": self.id,
                "spec": self.spec,
                "status": self.status,
                "started": self.started,
                "finished": self.finished,
                "artifact_path": str(self.artifact_path) if self.artifact_path else None,
            }
            self.state_path.write_text(json.dumps(payload, indent=2))
        except Exception as e:
            print(f"[job {self.id}] persist failed: {e}", file=sys.stderr)


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
JOB_QUEUE: queue.Queue[Job] = queue.Queue()


def _load_persisted_jobs() -> None:
    """On startup, recover job records from disk. In-flight jobs from a
    previous run get marked 'failed' (the subprocess is dead). Allows the
    /investigations list to include historical job rows even if their
    artifacts are not on disk."""
    for state_file in JOBS_DIR.glob("inv_*.json"):
        try:
            payload = json.loads(state_file.read_text())
            inv_id = payload["id"]
            job = Job(inv_id, payload.get("spec") or {})
            job.status = payload.get("status") or "failed"
            if job.status in ("queued", "running"):
                job.status = "failed"  # process died with the previous server
            job.started = payload.get("started") or job.started
            job.finished = payload.get("finished")
            ap = payload.get("artifact_path")
            job.artifact_path = Path(ap) if ap else None
            JOBS[inv_id] = job
        except Exception as e:
            print(f"[load] could not restore {state_file.name}: {e}", file=sys.stderr)


_load_persisted_jobs()


# ---------------------------------------------------------------------------
# Subprocess runner + stdout parser
# ---------------------------------------------------------------------------

_PHASE_PATTERNS = [
    (re.compile(r"^==+\s*EVENT '(?P<thread>[^']+)'\s+query='[^']+'\s*==+$"),
     lambda m: {"event": "thread_started", "data": {"thread": m["thread"]}}),
    (re.compile(r"^\[(?P<stage>S\d+)\] Fetching (?P<total>\d+) articles for the event$"),
     lambda m: {"event": "thread_progress",
                "data": {"stage": m["stage"], "phase": "fetch", "total": int(m["total"])}}),
    (re.compile(r"^\s+extracted (?P<current>\d+)/(?P<total>\d+) articles \(([\d,]+) chars\)$"),
     lambda m: {"event": "thread_progress",
                "data": {"phase": "extract",
                         "current": int(m["current"]), "total": int(m["total"])}}),
    (re.compile(r"^\[(?P<stage>S\d+)\] POST\s+session=\S+\s+run='(?P<thread>[^']+)'"),
     lambda m: {"event": "thread_progress",
                "data": {"stage": m["stage"], "phase": "post", "thread": m["thread"]}}),
    (re.compile(r"^\s+POST -> status=(?P<status>\w+)\s+nodes=(?P<nodes>\d+)\s+edges=(?P<edges>\d+)\s+themes=(?P<themes>\d+)"),
     lambda m: {"event": "post_result",
                "data": {"status": m["status"], "nodes": int(m["nodes"]),
                         "edges": int(m["edges"]), "themes": int(m["themes"])}}),
    (re.compile(r"^\s+\[(?P<i>\d+)/(?P<n>\d+)\] (?P<entity>.+)$"),
     lambda m: {"event": "thread_progress",
                "data": {"phase": "stage2_entity",
                         "current": int(m["i"]), "total": int(m["n"]),
                         "entity": m["entity"]}}),
    (re.compile(r"^Bridging entities \(server-derived, in >= 2 runs\): (?P<n>\d+)"),
     lambda m: {"event": "cross_event_analytics", "data": {"bridges": int(m["n"])}}),
    (re.compile(r"^Cross-event themes \(runs_spanned >= 2\): (?P<n>\d+) of (?P<total>\d+)"),
     lambda m: {"event": "cross_event_analytics",
                "data": {"crossThemes": int(m["n"]), "themesTotal": int(m["total"])}}),
    (re.compile(r"^Saved: (?P<path>.+\.json)\s+\(.*\)$"),
     lambda m: {"event": "artifacts_ready", "data": {"raw_json_path": m["path"]}}),
]


def _parse_line(line: str) -> dict | None:
    for pattern, builder in _PHASE_PATTERNS:
        m = pattern.match(line)
        if m:
            return builder(m)
    return None


def _drive_subprocess(job: Job, cmd: list[str], cwd: Path) -> None:
    """Run the cross_event_investigation.py subprocess, parse stdout, emit
    events, persist state on every transition, mirror stdout to a log file."""
    env = os.environ.copy()
    extra_paths = [str(ROOT.parent), str(ROOT.parent / "src")]
    env["PYTHONPATH"] = os.pathsep.join(extra_paths + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONUNBUFFERED"] = "1"

    job.emit({"event": "started", "data": {"investigationId": job.id, "threads": [t["name"] for t in job.spec["threads"]]}})
    try:
        log_fp = job.log_path.open("w", buffering=1)
    except Exception as e:
        with job.lock:
            job.status = "failed"
            job.finished = datetime.now(timezone.utc).isoformat()
        job.persist()
        job.emit({"event": "failed", "data": {"investigationId": job.id, "reason": f"log: {e}"}})
        return

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, universal_newlines=True, cwd=str(cwd), env=env,
        )
    except Exception as e:
        with job.lock:
            job.status = "failed"
            job.finished = datetime.now(timezone.utc).isoformat()
        log_fp.close()
        job.persist()
        job.emit({"event": "failed", "data": {"investigationId": job.id, "reason": f"spawn: {e}"}})
        return

    job.process = proc
    with job.lock:
        job.status = "running"
    job.persist()

    for raw in proc.stdout:
        line = raw.rstrip("\n")
        job.stdout_lines.append(line)
        try:
            log_fp.write(line + "\n")
        except Exception:
            pass
        evt = _parse_line(line)
        if evt:
            job.emit(evt)

    rc = proc.wait()
    log_fp.close()
    with job.lock:
        job.finished = datetime.now(timezone.utc).isoformat()
        if rc == 0:
            job.status = "succeeded"
        elif job.status != "cancelled":
            job.status = "failed"

    for line in reversed(job.stdout_lines):
        m = re.match(r"^Saved: (.+\.json)\s+\(.*\)$", line)
        if m:
            p = Path(m.group(1))
            if not p.is_absolute():
                p = (cwd / p).resolve()
            job.artifact_path = p
            break

    job.persist()
    summary_payload = {"investigationId": job.id, "status": job.status,
                       "artifactPath": str(job.artifact_path) if job.artifact_path else None}
    job.emit({"event": job.status, "data": summary_payload})


# ---------------------------------------------------------------------------
# Worker pool: pulls jobs off JOB_QUEUE one at a time
# ---------------------------------------------------------------------------

def _worker_loop(worker_id: int) -> None:
    while True:
        job = JOB_QUEUE.get()
        if job is None:
            return
        cwd = ROOT.parent
        # Re-check status: client may have cancelled before we picked it up
        with job.lock:
            if job.status == "cancelled":
                job.emit({"event": "cancelled", "data": {"investigationId": job.id, "reason": "cancelled before start"}})
                JOB_QUEUE.task_done()
                continue
        cmd = job.spec.get("cmd") or []
        if not cmd:
            with job.lock:
                job.status = "failed"
            job.persist()
            job.emit({"event": "failed", "data": {"investigationId": job.id, "reason": "no command"}})
            JOB_QUEUE.task_done()
            continue
        try:
            _drive_subprocess(job, cmd, cwd)
        except Exception as e:
            with job.lock:
                job.status = "failed"
                job.finished = datetime.now(timezone.utc).isoformat()
            job.persist()
            job.emit({"event": "failed", "data": {"investigationId": job.id, "reason": str(e)}})
        finally:
            JOB_QUEUE.task_done()


_WORKERS_STARTED = False
_WORKERS_LOCK = threading.Lock()


def _ensure_workers_started() -> None:
    global _WORKERS_STARTED
    with _WORKERS_LOCK:
        if _WORKERS_STARTED:
            return
        for i in range(MAX_CONCURRENT):
            threading.Thread(target=_worker_loop, args=(i,),
                             daemon=True, name=f"job-worker-{i}").start()
        _WORKERS_STARTED = True


def _slugify_thread_name(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", (s or "").strip().lower()).strip("_")
    return s or "thread"


# ---------------------------------------------------------------------------
# Routes: health + domains
# ---------------------------------------------------------------------------

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "schema": SCHEMA_VERSION, "artifactsDir": str(ARTIFACTS_DIR)})


# ---------------------------------------------------------------------------
# Integrations: OpenRegistry login (one-time browser OAuth, local/desktop).
# The Connect flow spawns research/enrichment.py --openregistry-login as a
# subprocess; it opens the user's browser and catches the OAuth callback on
# localhost, persisting auto-refreshing tokens. Works because the UI server and
# browser are on the same machine -- a hosted deployment would need the UI
# server itself to be the OAuth client.
# ---------------------------------------------------------------------------

_OR_LOGIN_PROC: subprocess.Popen | None = None
_OR_LOGIN_LOG = Path("/tmp/investigator_openregistry_login.log")
_AUTH_URL_RE = re.compile(r"https?://\S+")


def _openregistry_status() -> dict:
    import enrichment
    static = bool(os.environ.get("INVESTIGATOR_OPENREGISTRY_TOKEN"))
    has_file = enrichment._OAUTH_FILE.exists()
    running = _OR_LOGIN_PROC is not None and _OR_LOGIN_PROC.poll() is None
    authorize_url = ""
    if running and _OR_LOGIN_LOG.exists():
        for line in _OR_LOGIN_LOG.read_text(errors="ignore").splitlines():
            if "Authorize" in line or "/authorize" in line:
                m = _AUTH_URL_RE.search(line)
                if m:
                    authorize_url = m.group(0)
    return {
        "provider": "openregistry",
        "url": enrichment._OPENREGISTRY_URL,
        "connected": static or has_file,
        "method": "static_token" if static else ("oauth" if has_file else "none"),
        "loginInProgress": running,
        "authorizeUrl": authorize_url,
    }


@app.route("/api/integrations/openregistry", methods=["GET"])
def openregistry_status():
    return jsonify(_openregistry_status())


@app.route("/api/integrations/openregistry/login", methods=["POST"])
def openregistry_login_start():
    global _OR_LOGIN_PROC
    if os.environ.get("INVESTIGATOR_OPENREGISTRY_TOKEN"):
        return jsonify({**_openregistry_status(),
                        "message": "A static INVESTIGATOR_OPENREGISTRY_TOKEN is set; no login needed."})
    if _OR_LOGIN_PROC is not None and _OR_LOGIN_PROC.poll() is None:
        return jsonify({**_openregistry_status(), "message": "Login already in progress."})
    cmd = [sys.executable, str(REPO / "research" / "enrichment.py"), "--openregistry-login"]
    env = {**os.environ, "PYTHONPATH": f"{REPO}{os.pathsep}{REPO / 'src'}"}
    fh = open(_OR_LOGIN_LOG, "w")
    _OR_LOGIN_PROC = subprocess.Popen(cmd, cwd=str(REPO), env=env, stdout=fh, stderr=subprocess.STDOUT)
    return jsonify({**_openregistry_status(),
                    "message": "Authorize OpenRegistry in the browser window that just opened."})


@app.route("/api/integrations/openregistry/logout", methods=["POST"])
def openregistry_logout():
    import enrichment
    removed = False
    if enrichment._OAUTH_FILE.exists():
        try:
            enrichment._OAUTH_FILE.unlink()
            removed = True
        except OSError as e:
            return _err(500, "logout_failed", str(e))
    return jsonify({**_openregistry_status(), "removed": removed})


@app.route("/api/domains", methods=["GET"])
def list_domains():
    out = []
    for name, preset in dp.PRESETS.items():
        out.append({
            "id": f"dom_{name}",
            "name": name.replace("_", " ").title(),
            "key": name,
            "isPreset": True,
            "hypothesis": preset.hypothesis,
            "threshold": preset.relevance_threshold,
            "description": preset.description,
        })
    return jsonify({"items": out, "total": len(out)})


@app.route("/api/refine-query", methods=["POST"])
def refine_query():
    """Rewrite a broad query into a domain-focused one via a single LLM
    call. The frontend shows the result as an editable suggestion -- it is
    never auto-applied. Body: {query, domain}."""
    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    domain_id = body.get("domain") or "general"
    domain_key = domain_id.removeprefix("dom_") if isinstance(domain_id, str) else "general"
    if not query:
        return _err(400, "validation_failed", "query is required", field="query")
    preset = dp.PRESETS.get(domain_key)
    hypothesis = body.get("hypothesisOverride") or (preset.hypothesis if preset else "")
    if not hypothesis:
        return _err(400, "domain_not_found",
                    f"Unknown domain {domain_id!r} and no hypothesisOverride given.",
                    field="domain")

    refiner = _get_refiner()
    if refiner is None:
        return _err(503, "llm_unavailable",
                    "Query-refinement LLM is not available on this server.")
    try:
        refined = refiner(query, domain_key.replace("_", " "), hypothesis)
    except Exception as e:  # noqa: BLE001
        return _err(502, "llm_error", f"Refinement failed: {e}")
    if not refined:
        refined = query
    return jsonify({"query": query, "domain": domain_key, "refined": refined})


@app.route("/api/domains/<dom_id>", methods=["GET"])
def get_domain(dom_id):
    key = dom_id.removeprefix("dom_")
    preset = dp.PRESETS.get(key)
    if not preset:
        return _err(404, "domain_not_found", f"No domain with id {dom_id!r}")
    return jsonify({
        "id": dom_id, "key": key, "name": key.replace("_", " ").title(),
        "isPreset": True, "hypothesis": preset.hypothesis,
        "threshold": preset.relevance_threshold, "description": preset.description,
    })


# ---------------------------------------------------------------------------
# Routes: investigations - list + get
# ---------------------------------------------------------------------------

@app.route("/api/investigations", methods=["GET"])
def list_investigations():
    artifacts = _list_artifacts()
    items = [_meta_from_artifact(inv_id, p, deep=False) for inv_id, p in artifacts.items()]
    # Paths already represented by an artifact row, so a finished job that
    # produced one of them is not listed twice (the job id and the
    # filename-derived artifact id differ, so an id comparison alone misses
    # the duplicate).
    listed_paths = {str(p.resolve()) for p in artifacts.values()}
    with JOBS_LOCK:
        for j in JOBS.values():
            if any(it["id"] == j.id for it in items):
                continue
            # If this job's artifact is already on disk and listed, skip it.
            if j.artifact_path and str(Path(j.artifact_path).resolve()) in listed_paths:
                continue
            items.append({
                "id": j.id, "title": j.spec.get("title") or "(running)",
                "kind": j.spec.get("kind") or "multi",
                "status": j.status, "createdAt": j.started,
                "finishedAt": j.finished, "summary": {},
            })
    items.sort(key=lambda x: x.get("createdAt") or "", reverse=True)
    return jsonify({"items": items, "total": len(items)})


def _resolve_inv(inv_id: str) -> tuple[Path | None, Job | None]:
    artifacts = _list_artifacts()
    path = artifacts.get(inv_id)
    job = JOBS.get(inv_id)
    if path is None and job is not None and job.artifact_path:
        path = job.artifact_path
    return path, job


@app.route("/api/investigations/<inv_id>", methods=["GET"])
def get_investigation(inv_id):
    path, job = _resolve_inv(inv_id)
    if path is None and job is None:
        return _err(404, "investigation_not_found", f"No investigation with id {inv_id!r}")
    if path and path.exists():
        return jsonify(_meta_from_artifact(inv_id, path, deep=True))
    # Job-only (still running): include threads/domain/period so the UI's
    # InvestigationView header + Overview have the fields they read
    # (inv.threads.length, inv.domain, inv.period) before any artifact exists.
    return jsonify({
        "id": job.id, "title": job.spec.get("title") or "(running)",
        "kind": job.spec.get("kind") or "multi",
        "status": job.status, "createdAt": job.started,
        "finishedAt": job.finished,
        "threads": job.spec.get("threads") or [],
        "domain": job.spec.get("domain") or "general",
        "period": job.spec.get("period") or "30d",
        "params": {},
        "artifacts": {},
        "summary": {},
    })


_ARTIFACT_SUFFIXES = (
    ".json",
    ".customer_report.md",
    ".analyst_review.md",
    ".graph_prototype.html",
    ".tmfg_prototype.html",
    ".full_ui.html",
)


def _stop_job(job: Job) -> bool:
    """Terminate a running/queued job's subprocess. Returns True if it was
    active. Escalates to kill if terminate doesn't take."""
    active = job.status in ("running", "queued")
    with job.lock:
        if job.status in ("running", "queued"):
            job.status = "cancelled"
    proc = job.process
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        except Exception:
            pass
    job.emit({"event": "cancelled",
              "data": {"investigationId": job.id, "reason": "stopped by user"}})
    return active


@app.route("/api/investigations/<inv_id>/stop", methods=["POST"])
def stop_investigation(inv_id):
    """Stop a running/queued investigation without deleting anything."""
    job = JOBS.get(inv_id)
    if not job:
        return _err(404, "investigation_not_found", "No active job for this id.")
    if job.status not in ("running", "queued"):
        return _err(409, "not_running", f"Investigation is {job.status}, not running.")
    _stop_job(job)
    return jsonify({"id": inv_id, "status": "cancelled"})


@app.route("/api/investigations/<inv_id>", methods=["DELETE"])
def delete_investigation(inv_id):
    """Delete an investigation. If it is running/queued it is stopped first.
    Then its on-disk artifacts (raw json + all derivative reports/HTML) and
    job records (state json + log) are removed.

    Pass ?keep_running=1 to only stop without deleting (used by the Stop
    control); default behaviour deletes."""
    path, job = _resolve_inv(inv_id)
    keep_running = request.args.get("keep_running") in ("1", "true")

    if job and job.status in ("running", "queued"):
        _stop_job(job)
        if keep_running:
            return jsonify({"id": inv_id, "status": "cancelled", "deleted": False})

    if path is None and job is None:
        return _err(404, "investigation_not_found", f"No investigation with id {inv_id!r}")

    removed = []
    # Remove the artifact + all derivative files that share its stem.
    if path is not None:
        base = path.with_suffix("")  # strip .json -> stem path
        # path.with_suffix("") only strips the last suffix; derivatives use
        # compound suffixes, so reconstruct from the stem the JSON name minus .json
        stem_path = path.parent / path.name[:-len(".json")] if path.name.endswith(".json") else base
        for suf in _ARTIFACT_SUFFIXES:
            f = Path(str(stem_path) + suf)
            if f.exists():
                try:
                    f.unlink()
                    removed.append(f.name)
                except Exception as e:  # noqa: BLE001
                    print(f"[delete] could not remove {f}: {e}", file=sys.stderr)
    # Remove job state + log.
    for jf in (JOBS_DIR / f"{inv_id}.json", JOBS_DIR / f"{inv_id}.log"):
        if jf.exists():
            try:
                jf.unlink(); removed.append(jf.name)
            except Exception:
                pass
    JOBS.pop(inv_id, None)

    return jsonify({"id": inv_id, "deleted": True, "removed": removed})


# ---------------------------------------------------------------------------
# Routes: payloads (Graph, TMFG, themes, sources, entities, events, edges)
# ---------------------------------------------------------------------------

@app.route("/api/investigations/<inv_id>/graph", methods=["GET"])
def get_graph(inv_id):
    path, _ = _resolve_inv(inv_id)
    if not path or not path.exists():
        return _err(404, "investigation_not_found", "No artifact for this id.")
    return jsonify(_graph_payload(path))


@app.route("/api/investigations/<inv_id>/connect", methods=["POST"])
def connect_entities(inv_id):
    """Connector subgraph between a chosen set of entities/events: shortest-path
    union over relationship edges, surfacing intermediary connector nodes."""
    path, _ = _resolve_inv(inv_id)
    if not path or not path.exists():
        return _err(404, "investigation_not_found", "No artifact for this id.")
    body = request.get_json(silent=True) or {}
    selected = body.get("entities") or []
    if not isinstance(selected, list) or len(selected) < 2:
        return _err(400, "bad_request", "Provide at least 2 entity ids in 'entities'.")
    mode = body.get("mode") or "shortest_path"
    if mode not in ("shortest_path", "induced"):
        return _err(400, "bad_request", f"Unknown mode {mode!r}.")
    try:
        max_hops = int(body.get("maxHops") or 4)
    except (TypeError, ValueError):
        max_hops = 4
    payload = _graph_payload(path)
    result = connector_subgraph(
        payload["nodes"], payload["edges"], [str(s) for s in selected],
        mode=mode, max_hops=max_hops,
    )
    return jsonify(result)


@app.route("/api/investigations/<inv_id>/connect/analyze", methods=["POST"])
def analyze_connections(inv_id):
    """LLM summary of how the selected entities interconnect. Only the connected
    part of the connector subgraph (nodes with edges) is submitted."""
    path, _ = _resolve_inv(inv_id)
    if not path or not path.exists():
        return _err(404, "investigation_not_found", "No artifact for this id.")
    body = request.get_json(silent=True) or {}
    selected = body.get("entities") or []
    if not isinstance(selected, list) or len(selected) < 2:
        return _err(400, "bad_request", "Provide at least 2 entity ids in 'entities'.")
    mode = body.get("mode") or "shortest_path"
    if mode not in ("shortest_path", "induced"):
        return _err(400, "bad_request", f"Unknown mode {mode!r}.")
    payload = _graph_payload(path)
    result = connector_subgraph(
        payload["nodes"], payload["edges"], [str(s) for s in selected], mode=mode,
    )
    if not result["edges"]:
        return jsonify({
            "report": "", "connected": 0,
            "message": "The selected entities are not interconnected, so there is nothing to summarise.",
        })
    analyzer = _get_analyzer()
    if analyzer is None:
        return _err(503, "llm_unavailable", "Analysis model unavailable (check OPENAI_API_KEY).")
    try:
        network = _describe_connection_network(result)
        report = analyzer(network)
    except Exception as e:  # noqa: BLE001
        return _err(502, "llm_error", f"Analysis failed: {type(e).__name__}: {e}")
    connected = len({x for e in result["edges"] for x in (e["source"], e["target"])})
    return jsonify({"report": report, "connected": connected, "stats": result["stats"]})


@app.route("/api/investigations/<inv_id>/tmfg", methods=["GET"])
def get_tmfg(inv_id):
    path, _ = _resolve_inv(inv_id)
    if not path or not path.exists():
        return _err(404, "investigation_not_found", "No artifact for this id.")
    d = json.loads(path.read_text())
    return jsonify(bt._payload(d))


@app.route("/api/investigations/<inv_id>/themes", methods=["GET"])
def get_themes(inv_id):
    path, _ = _resolve_inv(inv_id)
    if not path or not path.exists():
        return _err(404, "investigation_not_found", "No artifact for this id.")
    d = json.loads(path.read_text())
    payload = bt._payload(d)
    return jsonify({"items": payload["themes"], "total": len(payload["themes"])})


@app.route("/api/investigations/<inv_id>/entities", methods=["GET"])
def get_entities(inv_id):
    path, _ = _resolve_inv(inv_id)
    if not path or not path.exists():
        return _err(404, "investigation_not_found", "No artifact for this id.")
    payload = _graph_payload(path)
    rows = [n for n in payload["nodes"] if n["type"] == "entity"]
    return _paginate(rows)


@app.route("/api/investigations/<inv_id>/events", methods=["GET"])
def get_events(inv_id):
    path, _ = _resolve_inv(inv_id)
    if not path or not path.exists():
        return _err(404, "investigation_not_found", "No artifact for this id.")
    payload = _graph_payload(path)
    rows = [n for n in payload["nodes"] if n["type"] == "event"]
    return _paginate(rows)


@app.route("/api/investigations/<inv_id>/relationships", methods=["GET"])
def get_relationships(inv_id):
    path, _ = _resolve_inv(inv_id)
    if not path or not path.exists():
        return _err(404, "investigation_not_found", "No artifact for this id.")
    payload = _graph_payload(path)
    return _paginate(payload["edges"])


@app.route("/api/investigations/<inv_id>/sources", methods=["GET"])
def get_sources(inv_id):
    path, _ = _resolve_inv(inv_id)
    if not path or not path.exists():
        return _err(404, "investigation_not_found", "No artifact for this id.")
    payload = _graph_payload(path)
    # Group edges' source urls by publisher
    groups: dict[str, list[dict]] = defaultdict(list)
    for e in payload["edges"]:
        if not e.get("url"):
            continue
        try:
            host = e["url"].split("/")[2].lstrip("www.")
        except Exception:
            host = "unknown"
        groups[host].append({"url": e["url"], "backsEntity": e["source"], "backsEdgeType": e["type"]})
    publishers = sorted(
        ({"publisher": p, "count": len(rs), "urls": rs[:25]} for p, rs in groups.items()),
        key=lambda x: -x["count"],
    )
    total = sum(g["count"] for g in publishers)
    top3 = sum(g["count"] for g in publishers[:3])
    return jsonify({
        "publisherCount": len(publishers),
        "topConcentration": round(top3 / total, 3) if total else 0.0,
        "publishers": publishers,
    })


def _paginate(rows: list) -> Response:
    page = max(1, int(request.args.get("page", 1)))
    page_size = min(500, max(1, int(request.args.get("pageSize", 50))))
    start = (page - 1) * page_size
    end = start + page_size
    return jsonify({"page": page, "pageSize": page_size, "total": len(rows), "rows": rows[start:end]})


# ---------------------------------------------------------------------------
# Routes: artifact downloads
# ---------------------------------------------------------------------------

_ARTIFACT_FILENAMES = {
    "raw.json":             ".json",
    "customer_report.md":   ".customer_report.md",
    "analyst_review.md":    ".analyst_review.md",
    "graph.html":           ".graph_prototype.html",
    "tmfg.html":            ".tmfg_prototype.html",
    "full_ui.html":         ".full_ui.html",
}


@app.route("/api/investigations/<inv_id>/log", methods=["GET"])
def get_job_log(inv_id):
    """Download the captured stdout for a job. Available for any
    investigation that ran on this server -- including failed and
    cancelled ones, which is the main reason this endpoint exists."""
    job = JOBS.get(inv_id)
    log_path = JOBS_DIR / f"{inv_id}.log"
    if not log_path.exists():
        return _err(404, "log_not_found", "No log captured for this id.")
    return send_file(str(log_path), mimetype="text/plain")


@app.route("/api/investigations/<inv_id>/artifacts/<name>", methods=["GET"])
def get_artifact(inv_id, name):
    path, _ = _resolve_inv(inv_id)
    if not path or not path.exists():
        return _err(404, "investigation_not_found", "No artifact for this id.")
    suffix = _ARTIFACT_FILENAMES.get(name)
    if not suffix:
        return _err(404, "artifact_not_found", f"Unknown artifact name {name!r}")
    target = path if name == "raw.json" else path.with_suffix(suffix)
    if not target.exists():
        # On-demand generation for the three derivative artifacts
        if name == "customer_report.md":
            bcr.build_report(path)
        elif name == "graph.html":
            bg.build_prototype(path)
        elif name == "tmfg.html":
            bt.build_prototype(path)
        elif name == "full_ui.html":
            try:
                import build_full_ui_prototype as bf  # noqa: E402
                bf.build_prototype(path)
            except Exception as e:
                return _err(500, "internal", f"build_full_ui failed: {e}")
    if not target.exists():
        return _err(404, "artifact_not_found", f"{name!r} not available for this investigation.")
    return send_file(str(target))


# ---------------------------------------------------------------------------
# Routes: manual-source uploads (PDF)
# ---------------------------------------------------------------------------

def _safe_upload_path(upload_id: str) -> Path | None:
    """Resolve a client-supplied upload id ('<token>/<filename>') to an
    absolute path, rejecting anything that escapes UPLOADS_DIR."""
    if not upload_id or not isinstance(upload_id, str):
        return None
    candidate = (UPLOADS_DIR / upload_id).resolve()
    try:
        candidate.relative_to(UPLOADS_DIR)
    except ValueError:
        return None  # path traversal attempt
    return candidate if candidate.exists() else None


@app.route("/api/uploads", methods=["POST"])
def upload_sources():
    """Accept one or more uploaded PDFs (multipart field 'files'). Each is
    stored under news_investigations/uploads/<token>/<filename>; the returned
    `id` ('<token>/<filename>') is what the client passes back in
    `extraSources.pdfs` when creating an investigation."""
    from werkzeug.utils import secure_filename

    files = request.files.getlist("files")
    if not files:
        return _err(400, "validation_failed", "no files in 'files' field", field="files")
    items = []
    for f in files:
        name = secure_filename(f.filename or "")
        if not name.lower().endswith(".pdf"):
            return _err(400, "validation_failed",
                        f"only .pdf uploads are supported (got {f.filename!r})", field="files")
        token = uuid.uuid4().hex[:12]
        dest_dir = UPLOADS_DIR / token
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        f.save(str(dest))
        items.append({"id": f"{token}/{name}", "name": name,
                      "bytes": dest.stat().st_size})
    return jsonify({"items": items}), 201


# ---------------------------------------------------------------------------
# Routes: POST /investigations -- launch a new run
# ---------------------------------------------------------------------------

@app.route("/api/investigations", methods=["POST"])
def create_investigation():
    body = request.get_json(silent=True) or {}
    kind = body.get("kind", "multi")
    if kind not in ("single", "multi"):
        return _err(400, "validation_failed", "kind must be 'single' or 'multi'", field="kind")
    threads_in = body.get("threads") or []
    if not isinstance(threads_in, list) or not threads_in:
        return _err(400, "thread_validation_failed", "threads must be a non-empty list", field="threads")
    if kind == "single" and len(threads_in) != 1:
        return _err(400, "thread_validation_failed", "single-query investigations must have exactly one thread")
    threads: list[dict] = []
    for i, t in enumerate(threads_in):
        if not isinstance(t, dict):
            return _err(400, "thread_validation_failed", "thread must be an object", field=f"threads[{i}]")
        name = _slugify_thread_name(t.get("name") or "")
        query = (t.get("query") or "").strip()
        if not name or not query:
            return _err(400, "thread_validation_failed",
                        "thread must carry both name and query", field=f"threads[{i}]")
        threads.append({"name": name, "query": query})
    if len({t["name"] for t in threads}) != len(threads):
        return _err(400, "thread_validation_failed", "thread names must be unique")

    domain_id = body.get("domain") or "general"
    domain_key = domain_id.removeprefix("dom_") if isinstance(domain_id, str) else "general"
    if domain_key not in dp.PRESETS and not body.get("hypothesisOverride"):
        return _err(400, "domain_not_found",
                    f"Unknown domain {domain_id!r}; supply hypothesisOverride to use a custom prompt.",
                    field="domain")

    period = body.get("period") or "30d"
    adv = body.get("advanced") or {}

    # Manual sources (PDF uploads + URLs) and the GNews toggle.
    gnews_enabled = body.get("gnewsEnabled", True)
    src_in = body.get("extraSources") or {}
    extra_urls = [u.strip() for u in (src_in.get("urls") or []) if isinstance(u, str) and u.strip()]
    extra_pdf_ids = [p for p in (src_in.get("pdfs") or []) if isinstance(p, str) and p]
    extra_pdf_paths: list[str] = []
    for pid in extra_pdf_ids:
        resolved = _safe_upload_path(pid)
        if resolved is None:
            return _err(400, "validation_failed",
                        f"unknown or invalid upload id {pid!r}", field="extraSources.pdfs")
        extra_pdf_paths.append(str(resolved))
    if not gnews_enabled and not extra_urls and not extra_pdf_paths:
        return _err(400, "validation_failed",
                    "with GNews disabled you must supply at least one URL or PDF source",
                    field="extraSources")

    # Idempotency: a client retrying the same key within IDEMP_TTL_SECS gets
    # back the existing job rather than starting a new run.
    idem_key = request.headers.get("Idempotency-Key")
    if idem_key:
        cached = IDEMP_INDEX.get(idem_key)
        if cached and cached[1] > time.time():
            existing = JOBS.get(cached[0])
            if existing:
                return jsonify({
                    "id": existing.id, "status": existing.status,
                    "title": existing.spec.get("title"),
                    "createdAt": existing.started,
                    "stream": f"/api/investigations/{existing.id}/stream",
                    "idempotent_replay": True,
                }), 200

    inv_id = f"inv_{uuid.uuid4().hex[:10]}"
    spec = {"title": " · ".join(t["name"] for t in threads), "kind": kind,
            "threads": threads, "domain": domain_key, "period": period, "advanced": adv,
            "gnewsEnabled": gnews_enabled,
            "extraSources": {"urls": extra_urls, "pdfs": extra_pdf_ids}}

    # Build the subprocess command line that drives cross_event_investigation.py
    cmd = [sys.executable, str(REPO / "research" / "cross_event_investigation.py"),
           "--domain", domain_key, "--period", period,
           "--stage1-articles", str(adv.get("stage1Articles", 50)),
           "--stage2-articles-per-entity", str(adv.get("stage2ArticlesPerEntity", 20)),
           "--top-n-entities", str(adv.get("topNEntities", 8)),
           "--output-dir", str(ARTIFACTS_DIR)]
    for t in threads:
        cmd += ["--event", f"{t['name']}:{t['query']}"]
    if body.get("hypothesisOverride"):
        cmd += ["--hypothesis", body["hypothesisOverride"]]
    if body.get("thresholdOverride") is not None:
        cmd += ["--relevance-threshold", str(body["thresholdOverride"])]
    # Opt-in enhanced retrieval (LLM expansion + title rerank + entity-driven depth).
    if adv.get("enhancedRetrieval"):
        cmd += ["--enhanced-retrieval",
                "--retrieval-depth", str(adv.get("retrievalDepth", 1)),
                "--retrieval-expansions", str(adv.get("retrievalExpansions", 4))]
    # Manual sources + GNews toggle.
    for u in extra_urls:
        cmd += ["--extra-url", u]
    for pth in extra_pdf_paths:
        cmd += ["--extra-pdf", pth]
    if not gnews_enabled:
        cmd += ["--no-gnews"]
    spec["cmd"] = cmd

    job = Job(inv_id, spec)
    job.persist()
    with JOBS_LOCK:
        JOBS[inv_id] = job
    if idem_key:
        IDEMP_INDEX[idem_key] = (inv_id, time.time() + IDEMP_TTL_SECS)

    _ensure_workers_started()
    JOB_QUEUE.put(job)
    queue_position = JOB_QUEUE.qsize()

    return jsonify({
        "id": inv_id, "status": "queued", "title": spec["title"],
        "createdAt": job.started, "spec": {k: v for k, v in spec.items() if k != "cmd"},
        "queuePosition": queue_position,
        "stream": f"/api/investigations/{inv_id}/stream",
    }), 202


# ---------------------------------------------------------------------------
# Routes: SSE progress stream
# ---------------------------------------------------------------------------

@app.route("/api/investigations/<inv_id>/stream", methods=["GET"])
def stream(inv_id):
    job = JOBS.get(inv_id)
    if not job:
        return _err(404, "investigation_not_found", "No active job for this id.")

    sub: queue.Queue = queue.Queue()
    with job.lock:
        job.subscribers.append(sub)
        # Replay the events seen so far so the client doesn't miss anything
        backlog = list(job.queue.queue)

    def gen():
        # Replay backlog first
        for evt in backlog:
            yield _sse(evt)
        # Then live updates
        while True:
            try:
                evt = sub.get(timeout=30)
            except queue.Empty:
                yield ": keep-alive\n\n"
                with job.lock:
                    if job.status in ("succeeded", "failed", "cancelled"):
                        return
                continue
            yield _sse(evt)
            with job.lock:
                if job.status in ("succeeded", "failed", "cancelled") and evt["event"] in ("succeeded", "failed", "cancelled"):
                    return

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(evt: dict) -> str:
    name = evt.get("event", "message")
    data = json.dumps(evt.get("data") or {}, ensure_ascii=False)
    return f"event: {name}\ndata: {data}\n\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="OSINTGraph UI backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    print(f"OSINTGraph UI backend serving on http://{args.host}:{args.port}")
    print(f"  artifacts directory: {ARTIFACTS_DIR}")
    print(f"  presets:            {len(dp.PRESETS)} domain(s)")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
