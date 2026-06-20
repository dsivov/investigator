"""Post-run entity enrichment (opt-in, decoupled from the engine).

Walks a saved investigation artifact, picks the top-N ORG entities by `score`,
and enriches each via domain-routed providers, attaching the result to the node
as `node["enrichment"][<provider>]`. Writes `<artifact>.enriched.json`.

Providers (first cut):
  * edgar        — SEC EDGAR (free, no key): US public-company filings/identity.
  * openregistry — 30 national company registries via the hosted OpenRegistry
                   MCP (beneficial owners / officers / shareholders). Auth is
                   OAuth 2.1 + DCR with token auto-refresh (tokens persisted to
                   a file). Bootstrap once with `--openregistry-login`; no-ops
                   cleanly until then. A static INVESTIGATOR_OPENREGISTRY_TOKEN
                   bearer also works as an override.

Design notes:
  - Decoupled: runs on the artifact AFTER the (offline, deterministic) engine,
    so external/rate-limited calls never touch the triangulation core.
  - Opt-in + opsec: this sends extracted entity NAMES to external services.
    Each provider is individually disableable; EDGAR is a public read API,
    OpenRegistry is hosted (queries go off-box).
  - Fail-soft + cached: any provider error skips that entity; results are
    cached by (name, provider) so re-runs and repeated names don't re-hit.

Usage:
    PYTHONPATH=.:src python research/enrichment.py --openregistry-login   # one-time
    PYTHONPATH=.:src python research/enrichment.py <artifact.json> [--top-n 12]
        [--no-edgar] [--no-openregistry]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# Domains where ORG enrichment is investigatively useful.
_ORG_DOMAINS = {
    "sanctions_evasion", "corporate_misconduct", "terror_financing",
    "supply_chain_human_rights", "criminal_investigation", "general",
}

_GENERIC_SUFFIXES = {
    "CORP", "CORPORATION", "INC", "INCORPORATED", "LTD", "LIMITED", "LLC",
    "PLC", "CO", "COMPANY", "GROUP", "HOLDINGS", "THE", "AG", "SA", "NV", "GMBH",
}


def _norm_company(name: str) -> set[str]:
    toks = re.findall(r"[A-Z0-9]+", (name or "").upper())
    return {t for t in toks if t not in _GENERIC_SUFFIXES and len(t) > 1}


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class _Cache:
    def __init__(self, path: Path):
        self.path = path
        self.d: dict = json.loads(path.read_text()) if path.exists() else {}

    def get(self, key: str):
        return self.d.get(key)

    def put(self, key: str, value):
        self.d[key] = value

    def save(self):
        self.path.write_text(json.dumps(self.d, ensure_ascii=False))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Provider: SEC EDGAR (free, no key) -- US public-company identity + filings
# ---------------------------------------------------------------------------

_EDGAR_UA = os.environ.get("INVESTIGATOR_EDGAR_UA", "investigator-osint osint@example.com")
_edgar_tickers: dict | None = None


def _edgar_load_tickers() -> dict:
    global _edgar_tickers
    if _edgar_tickers is None:
        r = requests.get("https://www.sec.gov/files/company_tickers.json",
                         headers={"User-Agent": _EDGAR_UA}, timeout=20)
        r.raise_for_status()
        # title -> {cik, ticker, title}
        _edgar_tickers = {}
        for row in r.json().values():
            _edgar_tickers[row["title"].upper()] = {
                "cik": int(row["cik_str"]), "ticker": row["ticker"], "title": row["title"],
            }
    return _edgar_tickers


def _edgar_match(name: str) -> dict | None:
    """Match an entity name to a US SEC filer. Conservative: the entity's
    significant tokens must be a subset of the filer title's tokens (or vice
    versa) so 'Bezeq' doesn't match an unrelated 'Bezeq-ish' filer."""
    want = _norm_company(name)
    if not want:
        return None
    best = None
    for title, meta in _edgar_load_tickers().items():
        have = _norm_company(title)
        if not have:
            continue
        # Subset match is only safe when the smaller set has >= 2 significant
        # tokens; a single generic token ("NEWS CORP" -> {NEWS}) would otherwise
        # match anything containing it ("WALLA! NEWS"). Single-token names must
        # match exactly.
        smaller = want if len(want) <= len(have) else have
        ok = (want == have) if len(smaller) < 2 else (want <= have or have <= want)
        if ok and (best is None or abs(len(have) - len(want)) < best[0]):
            best = (abs(len(have) - len(want)), meta)
    return best[1] if best else None


def edgar_enrich(node: dict) -> dict | None:
    meta = _edgar_match(node["identifier"])
    if not meta:
        return None
    cik10 = f"{meta['cik']:010d}"
    try:
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik10}.json",
                         headers={"User-Agent": _EDGAR_UA}, timeout=20)
        r.raise_for_status()
        sub = r.json()
    except Exception:  # noqa: BLE001
        sub = {}
    recent = (sub.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    filings = [{"form": f, "date": d} for f, d in list(zip(forms, dates))[:5]]
    return {
        "matched_name": meta["title"],
        "ticker": meta["ticker"],
        "cik": meta["cik"],
        "sic_description": sub.get("sicDescription"),
        "state_of_incorporation": sub.get("stateOfIncorporation"),
        "recent_filings": filings,
        "_provenance": {
            "provider": "edgar",
            "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik10}",
            "retrieved_at": _now(),
        },
    }


def edgar_applies(node: dict, domain: str) -> bool:
    return (node.get("data") or {}).get("type") == "ORG" and domain in _ORG_DOMAINS


# ---------------------------------------------------------------------------
# Provider: OpenRegistry (hosted MCP, OAuth 2.1 + DCR with auto-refresh) --
# 30 national registries. Tokens are persisted to a file so the access token
# refreshes itself across runs; a one-time interactive `--openregistry-login`
# bootstraps the OAuth grant. A static INVESTIGATOR_OPENREGISTRY_TOKEN bearer
# still works as an override (e.g. for testing).
# ---------------------------------------------------------------------------

_OPENREGISTRY_URL = os.environ.get(
    "INVESTIGATOR_OPENREGISTRY_URL", "https://openregistry.sophymarine.com/mcp")
# Free tier is 30 rpm; keep a margin.
_OPENREGISTRY_MIN_INTERVAL = 2.2
_OAUTH_DIR = Path(os.environ.get(
    "INVESTIGATOR_OAUTH_DIR", str(Path.home() / ".config" / "investigator")))
_OAUTH_FILE = _OAUTH_DIR / "openregistry_oauth.json"
_OAUTH_CALLBACK_PORT = int(os.environ.get("INVESTIGATOR_OAUTH_CALLBACK_PORT", "8765"))


class _FileTokenStorage:
    """mcp TokenStorage backed by a JSON file: persists the OAuth tokens and
    the dynamic client registration so the access token auto-refreshes across
    runs without re-authorising."""

    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self, d: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(d))

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        raw = self._load().get("tokens")
        return OAuthToken(**raw) if raw else None

    async def set_tokens(self, tokens):
        d = self._load(); d["tokens"] = tokens.model_dump(mode="json"); self._save(d)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        raw = self._load().get("client_info")
        return OAuthClientInformationFull(**raw) if raw else None

    async def set_client_info(self, info):
        d = self._load(); d["client_info"] = info.model_dump(mode="json"); self._save(d)


def _interactive_oauth_handlers():
    """Browser-redirect + local-callback handlers for the one-time login."""
    import urllib.parse
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer

    captured: dict = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            print(f"[openregistry] callback hit: {self.path}", flush=True)
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = (qs.get("code") or [None])[0]
            if code:
                captured["code"] = code
                captured["state"] = (qs.get("state") or [None])[0]
            self.send_response(200)
            self.end_headers()
            msg = (b"OpenRegistry authorized. You can close this tab."
                   if code else b"Waiting for the authorization code (no code in this request).")
            self.wfile.write(msg)

        def log_message(self, *a):  # silence the dev server
            pass

    async def redirect_handler(url: str):
        print(f"\n[openregistry] Authorize access in your browser:\n  {url}\n", flush=True)
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 -- headless: just print the URL
            pass

    async def callback_handler():
        import asyncio
        # 0.0.0.0 so the redirect reaches us whether the browser uses localhost
        # or 127.0.0.1. Loop until a request carrying the `code` arrives, so a
        # stray request (favicon, a probe, a manual-completion ping) doesn't
        # consume the one callback and abort the flow.
        srv = HTTPServer(("0.0.0.0", _OAUTH_CALLBACK_PORT), _Handler)
        print(f"[openregistry] waiting for OAuth callback on :{_OAUTH_CALLBACK_PORT}/callback", flush=True)
        loop = asyncio.get_event_loop()
        while not captured.get("code"):
            await loop.run_in_executor(None, srv.handle_request)
        srv.server_close()
        print("[openregistry] callback received (code=yes)", flush=True)
        return captured.get("code"), captured.get("state")

    return redirect_handler, callback_handler


def _build_openregistry_auth(interactive: bool):
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata
    metadata = OAuthClientMetadata(
        client_name="Investigator OSINT",
        redirect_uris=[f"http://localhost:{_OAUTH_CALLBACK_PORT}/callback"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope=os.environ.get("INVESTIGATOR_OPENREGISTRY_SCOPE", "openregistry:read"),
    )
    if interactive:
        redirect_handler, callback_handler = _interactive_oauth_handlers()
    else:
        async def redirect_handler(url):  # noqa: ARG001
            raise RuntimeError(
                "OpenRegistry OAuth not bootstrapped. Run once: "
                "python research/enrichment.py --openregistry-login")

        async def callback_handler():
            raise RuntimeError("OpenRegistry OAuth not bootstrapped.")
    return OAuthClientProvider(
        server_url=_OPENREGISTRY_URL,
        client_metadata=metadata,
        storage=_FileTokenStorage(_OAUTH_FILE),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )


def _openregistry_available() -> bool:
    """OpenRegistry is usable when either a static bearer override is set or the
    OAuth grant has been bootstrapped to the token file."""
    return bool(os.environ.get("INVESTIGATOR_OPENREGISTRY_TOKEN")) or _OAUTH_FILE.exists()


_OR_ACCESS_CACHE: dict = {"token": None, "exp": 0.0}


def _openregistry_token_endpoint() -> str:
    import urllib.request
    base = _OPENREGISTRY_URL.rsplit("/mcp", 1)[0].rstrip("/")
    req = urllib.request.Request(
        base + "/.well-known/oauth-authorization-server",
        headers={"User-Agent": "investigator", "Accept": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())["token_endpoint"]


def _refresh_openregistry_access_token() -> str | None:
    """Refresh the access token via the stored (rotating) refresh token and
    persist the new tokens. The MCP client's own auto-refresh is unreliable and
    the access token lives only ~15 min, so we do it ourselves. Returns the
    fresh access token, or None if there is nothing to refresh / it fails."""
    import urllib.error
    import urllib.parse
    import urllib.request
    d = json.loads(_OAUTH_FILE.read_text()) if _OAUTH_FILE.exists() else {}
    tok, ci = d.get("tokens") or {}, d.get("client_info") or {}
    refresh_token, client_id = tok.get("refresh_token"), ci.get("client_id")
    if not (refresh_token and client_id):
        return None
    try:
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }).encode()
        req = urllib.request.Request(
            _openregistry_token_endpoint(), data=body,
            headers={"User-Agent": "investigator", "Accept": "application/json",
                     "Content-Type": "application/x-www-form-urlencoded"})
        new = json.loads(urllib.request.urlopen(req, timeout=15).read())
    except Exception as e:  # noqa: BLE001
        print(f"[openregistry] token refresh failed: {e}", file=sys.stderr)
        return None
    if not new.get("access_token"):
        return None
    d["tokens"] = {**tok, **new}   # rotated refresh_token is persisted
    _OAUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OAUTH_FILE.write_text(json.dumps(d))
    return new["access_token"]


def _openregistry_access_token() -> str | None:
    now = time.time()
    if _OR_ACCESS_CACHE["token"] and now < _OR_ACCESS_CACHE["exp"]:
        return _OR_ACCESS_CACHE["token"]
    access = _refresh_openregistry_access_token()
    if access:
        _OR_ACCESS_CACHE.update(token=access, exp=now + 800)  # refresh before the ~900s expiry
    return access


def _openregistry_client_kwargs() -> dict | None:
    """Bearer header for the OpenRegistry MCP. A static
    INVESTIGATOR_OPENREGISTRY_TOKEN wins; otherwise refresh the OAuth access
    token ourselves (reliable, unlike the MCP client's auto-refresh). Returns
    None when no usable token can be obtained -- the caller then SKIPS
    OpenRegistry rather than triggering an (impossible, headless) interactive
    re-auth. Reconnect via the UI/CLI to restore it."""
    token = os.environ.get("INVESTIGATOR_OPENREGISTRY_TOKEN")
    if token:
        return {"headers": {"Authorization": f"Bearer {token}"}}
    access = _openregistry_access_token()
    if access:
        return {"headers": {"Authorization": f"Bearer {access}"}}
    return None


def openregistry_login() -> None:
    """One-time interactive OAuth bootstrap: authorise in the browser and store
    the tokens (incl. refresh token) so later runs are hands-off."""
    import asyncio

    async def _run():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(
                _OPENREGISTRY_URL, auth=_build_openregistry_auth(interactive=True)) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                print(f"[openregistry] authorized; {len(tools.tools)} tools available.")

    asyncio.run(_run())
    print(f"Tokens stored at {_OAUTH_FILE}. OpenRegistry enrichment is now active "
          "(access token auto-refreshes).")


def openregistry_enrich(node: dict) -> dict | None:
    """Beneficial owners / officers / shareholders for a company via the
    OpenRegistry MCP. No-ops until OAuth is bootstrapped (or a static token is
    set)."""
    if not _openregistry_available():
        return None
    client_kwargs = _openregistry_client_kwargs()
    if client_kwargs is None:
        return None   # token expired and could not refresh -> skip cleanly
    import asyncio

    name = node["identifier"]
    _loc = (node.get("data") or {}).get("location")
    if isinstance(_loc, (list, tuple)):
        _loc = _loc[0] if _loc else ""
    juris = str(_loc or "").strip() or None

    async def _run():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(_OPENREGISTRY_URL, **client_kwargs) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                def _content(res):
                    for c in (res.content or []):
                        txt = getattr(c, "text", None)
                        if txt:
                            try:
                                return json.loads(txt)
                            except ValueError:
                                return txt
                    return None

                args = {"name": name}
                if juris:
                    args["jurisdiction"] = juris
                hits = _content(await session.call_tool("search_companies", args)) or {}
                items = hits.get("items") or hits.get("results") or []
                if not items:
                    return None
                top = items[0]
                cid = top.get("company_id") or top.get("company_number")
                cj = top.get("jurisdiction") or juris
                out = {
                    "matched_name": top.get("company_name") or top.get("name"),
                    "company_id": cid, "jurisdiction": cj,
                    "status": (top.get("data") or {}).get("company_status") or top.get("status"),
                }
                if cid and cj:
                    pscr = await session.call_tool(
                        "get_persons_with_significant_control",
                        {"jurisdiction": cj, "company_id": cid})
                    out["beneficial_owners"] = _content(pscr)
                    offr = await session.call_tool(
                        "get_officers", {"jurisdiction": cj, "company_id": cid})
                    out["officers"] = _content(offr)
                out["_provenance"] = {
                    "provider": "openregistry", "url": _OPENREGISTRY_URL,
                    "retrieved_at": _now(),
                }
                return out

    try:
        return asyncio.run(_run())
    except Exception as e:  # noqa: BLE001
        print(f"  [openregistry] {name}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def openregistry_applies(node: dict, domain: str) -> bool:
    return (node.get("data") or {}).get("type") == "ORG"


# ---------------------------------------------------------------------------
# Router + stage
# ---------------------------------------------------------------------------

_PROVIDERS = {
    "edgar": (edgar_applies, edgar_enrich, 0.2),
    "openregistry": (openregistry_applies, openregistry_enrich, _OPENREGISTRY_MIN_INTERVAL),
}


def enrich_artifact(path: Path, *, top_n: int = 12,
                    enabled: set[str] | None = None) -> Path:
    enabled = enabled if enabled is not None else set(_PROVIDERS)
    d = json.loads(path.read_text())
    final = d["final_merged_graph"]
    domain = (d.get("params") or {}).get("domain") or "general"
    nodes = final["nodes"]
    by_id = {n["identifier"]: n for n in nodes}

    orgs = sorted(
        [n for n in nodes if (n.get("data") or {}).get("type") == "ORG"],
        key=lambda n: -(n.get("score") or 0.0),
    )[:top_n]
    print(f"enriching top {len(orgs)} ORG entities (domain={domain}); "
          f"providers={sorted(enabled)}")

    cache = _Cache(path.parent / ".enrichment_cache.json")
    last_call: dict[str, float] = {}
    enriched_count = 0

    for node in orgs:
        ident = node["identifier"]
        for pname, (applies, enrich, interval) in _PROVIDERS.items():
            if pname not in enabled or not applies(node, domain):
                continue
            ck = f"{pname}::{ident}"
            cached = cache.get(ck)
            if cached is not None:
                result = cached or None
            else:
                # throttle per provider
                wait = interval - (time.time() - last_call.get(pname, 0))
                if wait > 0:
                    time.sleep(wait)
                try:
                    result = enrich(node)
                except Exception as e:  # noqa: BLE001 -- never break the artifact
                    print(f"  [{pname}] {ident}: {type(e).__name__}: {e}", file=sys.stderr)
                    result = None
                last_call[pname] = time.time()
                cache.put(ck, result or {})
            if result:
                by_id[ident].setdefault("enrichment", {})[pname] = result
                enriched_count += 1
                print(f"  [{pname}] {ident} -> {result.get('matched_name') or 'matched'}")
    cache.save()

    out = path.with_suffix(".enriched.json")
    out.write_text(json.dumps(d, ensure_ascii=False))
    print(f"Wrote: {out}  ({enriched_count} enrichment record(s))")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("artifact", nargs="?", help="path to cross_*.json investigation artifact")
    ap.add_argument("--top-n", type=int, default=12)
    ap.add_argument("--no-edgar", action="store_true")
    ap.add_argument("--no-openregistry", action="store_true")
    ap.add_argument("--openregistry-login", action="store_true",
                    help="one-time interactive OAuth bootstrap for OpenRegistry, then exit")
    a = ap.parse_args()
    if a.openregistry_login:
        openregistry_login()
        return 0
    if not a.artifact:
        ap.error("artifact is required (or pass --openregistry-login)")
    enabled = set(_PROVIDERS)
    if a.no_edgar:
        enabled.discard("edgar")
    if a.no_openregistry:
        enabled.discard("openregistry")
    enrich_artifact(Path(a.artifact), top_n=a.top_n, enabled=enabled)
    return 0


if __name__ == "__main__":
    sys.exit(main())
