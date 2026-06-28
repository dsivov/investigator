"""CLI for the standing monitor.

    # one-shot daily run (cron target); engine must be running on :5003
    PYTHONPATH=.:src python -m investigator.monitor --once --period 1d --k 8

    # manage the watchlist
    python -m investigator.monitor --add "SAMIDOUN" --add "HAMAS"
    python -m investigator.monitor --show
"""
from __future__ import annotations

import argparse
import json
import sys

from investigator.monitor.watchlist import load_watchlist
from investigator.monitor.digest import run_once


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="investigator.monitor", description=__doc__)
    p.add_argument("--once", action="store_true", help="run the daily monitor loop now")
    p.add_argument("--k", type=int, default=8, help="top-k news per watched subject")
    p.add_argument("--period", default="1d", help="news recency window (e.g. 1d, 7d)")
    p.add_argument("--domain", default="general", help="extraction domain preset")
    p.add_argument("--today", default=None, help="override 'today' (YYYY-MM-DD)")
    p.add_argument("--base-url", default=None, help="engine base url (default :5003/api/v1)")
    p.add_argument("--add", action="append", default=[], metavar="NAME", help="add a watched entity")
    p.add_argument("--remove", action="append", default=[], metavar="NAME", help="remove a watched entity")
    p.add_argument("--domain-query", default=None, help="set the watchlist's domain query")
    p.add_argument("--show", action="store_true", help="print the watchlist")
    args = p.parse_args(argv)

    wl = load_watchlist()
    dirty = False
    for n in args.add:
        dirty |= wl.add(n)
    for n in args.remove:
        dirty |= wl.remove(n)
    if args.domain_query is not None:
        wl.domain = args.domain_query
        dirty = True
    if dirty:
        wl.save()
        print(f"watchlist saved: {len(wl.entities)} entities, domain={wl.domain!r}")

    if args.show:
        print(json.dumps(wl.to_dict(), indent=1))

    if args.once:
        if not wl.subjects():
            print("watchlist is empty -- add entities first (--add NAME)", file=sys.stderr)
            return 2
        digest = run_once(wl, k=args.k, period=args.period, today=args.today,
                          domain=args.domain, base_url=args.base_url)
        c = digest["counts"]
        print(f"\n=== MONITOR DIGEST {digest['date']} ===")
        print(f"intake: {digest['intake']}")
        print(f"events: {c['events']}  alerts: {c['alerts']}  -> {digest.get('savedTo')}")
        for e in digest["events"][:10]:
            ev = e["event"]
            flag = "🔔" if e["alert"] else "  "
            print(f"{flag} [{e['topScore']:.3f}] {ev.get('date') or '—':10} {ev['id'][:55]}")
            for imp in e["impacted"][:4]:
                mark = "★" if imp["watched"] else " "
                print(f"      {mark} {imp['score']:.3f} Δ{imp['delta']:+.2f} h{imp['hops']} {imp['entity'][:45]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
