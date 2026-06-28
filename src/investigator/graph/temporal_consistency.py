"""Temporal consistency / conflicting-date detection (read-time postprocessing).

The temporal layer keeps dates as SETS (event ``dates``, edge ``observed_dates``,
per-actor timelines), never collapsed -- precisely so we can later detect when
those dates DISAGREE. A disagreement is a high-value signal: sources contradict
each other, an extraction erred, or two real-world things were merged into one
canonical node. This module flags such conflicts; it never resolves them.

Like :mod:`graph.corroboration` this is a pure, read-time pass over data we
already store -- no pipeline re-run, works on any artifact / KB store. It reuses
the date-precision primitives in :mod:`graph.dedup`.

Two detectors (MVP):
  1. ``date_spread_conflict`` -- one event whose date set cannot be reconciled
     within a tolerance window (e.g. ["2024-05-10", "2024-09-20"]).
  2. ``ordering_conflicts`` -- an ``event_followed_by`` (earlier -> later) edge
     whose endpoints' actual dates say the opposite.

Dates are treated with their precision: year-only is too imprecise to flag (we
skip it, avoiding false positives), month-only expands to the whole month, and
day-precise is a single point -- mirroring ``dedup._dates_compatible``.
"""
from __future__ import annotations

import calendar
import datetime
import os

from investigator.graph.dedup import _parse_iso_date

DATE_CONFLICT_DAYS = int(os.getenv("INVESTIGATOR_DATE_CONFLICT_DAYS", "30"))


def _interval(s: str) -> tuple[datetime.date, datetime.date] | None:
    """A date string -> the [earliest, latest] day it could mean, or None when
    too imprecise (year-only) or unparseable. Month-only -> the whole month."""
    p = _parse_iso_date(s)
    if p is None:
        return None
    y, m, d = p
    if m == 0:
        return None  # year-only: too coarse to call a conflict
    try:
        if d == 0:
            return datetime.date(y, m, 1), datetime.date(y, m, calendar.monthrange(y, m)[1])
        return datetime.date(y, m, d), datetime.date(y, m, d)
    except ValueError:
        return None


def _intervals(dates) -> list[tuple[datetime.date, datetime.date]]:
    out = []
    for s in (dates or []):
        iv = _interval(str(s))
        if iv:
            out.append(iv)
    return out


def date_spread_conflict(dates, *, tol_days: int = DATE_CONFLICT_DAYS) -> dict | None:
    """Flag a date set that cannot all fall within ``tol_days``.

    Conservative: uses the latest lower-bound and earliest upper-bound across the
    (precision-aware) date intervals, so imprecise dates can't manufacture a
    conflict. Returns ``{"min", "max", "daysApart"}`` or None.
    """
    ivs = _intervals(dates)
    if len(ivs) < 2:
        return None
    latest_lo = max(lo for lo, _ in ivs)
    earliest_hi = min(hi for _, hi in ivs)
    gap = (latest_lo - earliest_hi).days
    if gap > tol_days:
        return {
            "min": min(lo for lo, _ in ivs).isoformat(),
            "max": max(hi for _, hi in ivs).isoformat(),
            "daysApart": gap,
        }
    return None


def _edge_ends(e: dict) -> tuple[str | None, str | None]:
    """Endpoints of an ordering edge, tolerant of the sidecar (src/dst) and
    payload/raw (source/target, src_identifier/dst_identifier) key names."""
    src = e.get("src") or e.get("source") or e.get("src_identifier")
    dst = e.get("dst") or e.get("target") or e.get("dst_identifier")
    return src, dst


def ordering_conflicts(event_dates: dict, ordering_edges: list,
                       *, tol_days: int = DATE_CONFLICT_DAYS) -> list[dict]:
    """Flag ``event_followed_by`` edges (src = earlier, dst = later) whose dates
    contradict the ordering: the earliest src can be is still later than the
    latest dst can be, by more than ``tol_days``."""
    out = []
    for e in (ordering_edges or []):
        if (e.get("type") or "") != "event_followed_by":
            continue
        src, dst = _edge_ends(e)
        sd = _intervals(event_dates.get(src, []))
        dd = _intervals(event_dates.get(dst, []))
        if not sd or not dd:
            continue
        src_lo = min(lo for lo, _ in sd)
        dst_hi = max(hi for _, hi in dd)
        gap = (src_lo - dst_hi).days
        if gap > tol_days:
            out.append({
                "src": src, "dst": dst,
                "srcDate": src_lo.isoformat(), "dstDate": dst_hi.isoformat(),
                "daysApart": gap,
            })
    return out


def scan(event_dates: dict, ordering_edges: list,
         *, tol_days: int = DATE_CONFLICT_DAYS) -> dict:
    """Run both detectors over an ``{event_id: [date,...]}`` map + ordering edges.
    Returns ``{"events": {id: conflict}, "orderings": [conflict, ...]}``."""
    events = {}
    for eid, dates in (event_dates or {}).items():
        c = date_spread_conflict(dates, tol_days=tol_days)
        if c:
            events[eid] = c
    return {"events": events,
            "orderings": ordering_conflicts(event_dates, ordering_edges, tol_days=tol_days)}
