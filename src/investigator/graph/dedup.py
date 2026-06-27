"""Entity deduplication, record merging, edge registration, and state merging.

The functions here move records between three roles:
  * raw entity dicts (from LLM extraction)
  * deduplicated/merged record groups
  * the persisted investigation state
"""

import json
import math
import os
import re
from collections import Counter
from itertools import groupby
from operator import itemgetter

import numpy as np
from semhash import SemHash
from wordllama import WordLlama

from investigator.logging import get_logger

log = get_logger()

# Module-level model: matches the pre-refactor pattern from utils.py where
# WordLlama is loaded once at import time so clustering doesn't re-pay it on
# every call. Phase 2/3 candidate: lazy-load + share across modules.
_wl = WordLlama.load_m2v("potion_base_8m")


def group_edges_by_chunk(edges: list[dict]) -> dict:
    edges.sort(key=itemgetter("chunk_id"))
    grouped_data = {}
    for key, group in groupby(edges, key=itemgetter("chunk_id")):
        grouped_data[key] = list(group["nodes"] for group in group)
    return grouped_data


def _as_list(value):
    """Normalise a (possibly scalar / None) value to a list for union/merge."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def merge_data_fields(list_of_dicts: list[dict]) -> dict:
    # No relevance filtering here (was: skip dicts with relevance_score < 0.5).
    # That dropped low-relevance entity data during dedup — a relevance filter
    # that belongs to triangulation, not merge (TRIANGULATION_REVIEW F3) — and,
    # because edge `attributes` have no relevance_score, made every edge-attribute
    # merge return {} (F4).
    merged_dict: dict = {}
    for d in list_of_dicts:
        for key, value in d.items():
            if key not in merged_dict:
                merged_dict[key] = value
                continue
            # Single if/elif chain: exactly one branch fires. Previously the
            # type checks were independent `if`s, so a dict+dict pair set the
            # value then *fell through* to the final else, yielding
            # [value, value] (H1 corruption). Each branch's original intent is
            # preserved; they are now mutually exclusive.
            if isinstance(merged_dict[key], dict) and isinstance(value, dict):
                merged_dict[key] = value
            elif isinstance(merged_dict[key], set) and isinstance(value, set):
                merged_dict[key] = merged_dict[key].union(value)
            elif isinstance(merged_dict[key], float) and isinstance(value, float):
                merged_dict[key] = max(merged_dict[key], value)
            elif isinstance(merged_dict[key], int) and isinstance(value, int):
                merged_dict[key] = max(merged_dict[key], value)
            elif isinstance(merged_dict[key], list) and isinstance(value, list):
                merged_dict[key].extend(value)
            elif isinstance(merged_dict[key], list):
                merged_dict[key].append(value)
            elif isinstance(value, list):
                merged_dict[key] = [merged_dict[key]] + value
            else:
                merged_dict[key] = [merged_dict[key], value]
    return merged_dict


# MostRepresentativeIdentifier degrades on long lists -- it returns wrong
# canonical names -- so we ALWAYS split the names into small *similar* groups
# (embedding k-means) and send each group as its own async LLM job. Only a list
# already at/below the target is sent as a single short group.
_MRI_GROUP_SIZE = int(os.environ.get("INVESTIGATOR_MRI_GROUP_SIZE", "40"))


def _kmeans_groups(names: list[str], k: int) -> list[list[str]]:
    labels, _inertia = _wl.cluster(names, k=k, max_iterations=100, tolerance=1e-4, n_init=3)
    buckets: dict = {}
    for i, label in enumerate(labels):
        buckets.setdefault(label, []).append(names[i])
    return list(buckets.values())


def _split_cluster(group: list[str]) -> list[list[str]]:
    """Recursively similarity-split a group until each piece is <= the target
    size. Splitting is always by embedding similarity (never an arbitrary index
    cut), so variants of one entity stay together. If k-means can't subdivide a
    group further -- because the names really are near-identical -- it is kept
    WHOLE rather than sliced apart (those names belong in one canonicalisation
    list even if the list is a little long)."""
    if len(group) <= _MRI_GROUP_SIZE:
        return [group]
    k = max(2, math.ceil(len(group) / _MRI_GROUP_SIZE))
    subs = _kmeans_groups(group, k)
    # No progress: everything landed in one sub-cluster -> cohesive, keep whole.
    if len(subs) <= 1 or max(len(s) for s in subs) == len(group):
        return [group]
    out: list = []
    for sub in subs:
        out.extend(_split_cluster(sub))
    return out


def cluster_identifiers(identifiers: list[str]) -> list:
    n = len(identifiers)
    if n == 0:
        return []
    if n <= _MRI_GROUP_SIZE:
        log.debug(f"{n} identifiers <= group size {_MRI_GROUP_SIZE}; one group.")
        return [identifiers]
    # Group similar names together so variants of one entity land in the same
    # (short) list for the LLM to canonicalise; recursively similarity-split any
    # over-target cluster so we never send a long list NOR break apart variants.
    groups: list = []
    for cluster in _kmeans_groups(identifiers, max(2, math.ceil(n / _MRI_GROUP_SIZE))):
        groups.extend(_split_cluster(cluster))
    log.debug(f"Clustered {n} identifiers into {len(groups)} groups (target {_MRI_GROUP_SIZE}).")
    return groups


# Fields that denote a single fact (prefer the best source's value), vs the
# default of keeping every distinct value (an entity can legitimately have
# several addresses / phones / locations). `position`/role query-relativity
# (M4) is left to the prompt / Stage 4 — here it just unions like other facts.
_SINGLE_VALUED_FIELDS = {"type"}

# Filler the LLM-armed search layer emits for absent attributes. Matched
# case-insensitively, with prefix matching so verbose variants are caught too
# (e.g. "Not specified in provided data", "Not available in the source").
_EMPTY_EXACT = {"", "n/a", "na", "none", "null", "not applicable", "unknown"}
_EMPTY_PREFIXES = ("not specified", "not found", "not available", "not provided",
                   "not mentioned", "not disclosed", "not stated")


def _is_empty_value(value) -> bool:
    if value is None or value == [] or value == {}:
        return True
    if isinstance(value, str):
        s = value.strip().lower()
        return s in _EMPTY_EXACT or s.startswith(_EMPTY_PREFIXES)
    return False


def merge_duplicate_group(dedup):
    """Merge a duplicate group into one record (TRIANGULATION_REVIEW §2 M1-M3):
    keep every entity (no relevance drop), `relevance_score` = max, prefer-best
    for single-valued fields, clean distinct **union** for multi-valued fields.
    """
    duplicates = [dup[0]["data"] for dup in dedup.duplicates if "data" in dup[0]]
    log.debug(f"Merging {len(duplicates)} duplicates for record {dedup.record['identifier']}")
    deduplicated_node = merge_data_fields([dedup.record["data"]] + duplicates)

    names = [n for n in dict.fromkeys(_as_list(deduplicated_node.get("name", []))) if isinstance(n, str)]
    dedup.record["labels"] = [n.upper() for n in names]
    dedup.record["most_significant_labels"] = Counter(
        n.upper() for n in _as_list(deduplicated_node.get("name", [])) if isinstance(n, str)
    ).most_common()

    for key, value in list(deduplicated_node.items()):
        if key == "relevance_score":
            scores = [float(v) for v in _as_list(value) if isinstance(v, (int, float))]
            if scores:
                deduplicated_node[key] = max(scores)  # M1: max over the group, never dropped
            continue
        items = _as_list(value)
        if not (items and all(isinstance(v, (str, int, float)) for v in items)):
            continue  # non-scalar fields (e.g. timeline_events) keep their merged form
        clean = [v for v in dict.fromkeys(items) if not _is_empty_value(v)]
        if not clean:
            deduplicated_node[key] = "Not found"
        elif key in _SINGLE_VALUED_FIELDS:
            deduplicated_node[key] = clean[0]                       # M2: prefer-best
        else:
            deduplicated_node[key] = clean if len(clean) > 1 else clean[0]  # M3: distinct union

    for field in ("location", "email", "phone_number", "address"):
        deduplicated_node.setdefault(field, "Not found")
    return deduplicated_node


def deduplicate_entities(entities_dicts, representative_identifiers, semhash_model=None):
    log.info("Grouping duplicates by name using similarity hashing...")
    # SemHash.from_records raises ValueError("records must not be empty") on an
    # empty list. A run can legitimately yield zero entities to dedup (e.g. a
    # single short/odd source, or a doc that NER finds no named entities in);
    # degrade to an empty result instead of 500-ing the whole POST.
    if not entities_dicts:
        log.warning("No entities to deduplicate; returning empty result.")
        return [], [], []
    columns = ["representative_identifier"]
    all_identifiers = [entity["identifier"].upper() for entity in entities_dicts]
    all_representative_ids = [rep["identifier"].upper() for rep in representative_identifiers]
    log.debug(f"Total identifiers before deduplication: {len(all_identifiers)}")
    all_deduplicated_identifiers = []
    mapped_count = 0
    for entity in entities_dicts:
        entity["representative_identifier"] = entity["identifier"].upper()
        updated = False
        if entity["identifier"].upper() in all_representative_ids:
            log.debug(f"Identifier {entity['identifier']} already mapped to a representative.")
            continue

        for representative in representative_identifiers:
            relevant_ids = [rid.upper() for rid in representative["relevant_identifiers"]]
            if relevant_ids and entity["identifier"].upper() in relevant_ids:
                entity["representative_identifier"] = representative["identifier"].upper()
                log.debug(
                    f"Mapping identifier {entity['identifier']} to representative {representative['identifier']}"
                )
                mapped_count += 1
                updated = True
                break

        if updated:
            continue

    log.debug(f"Total mapped identifiers to representatives: {mapped_count}")
    if mapped_count != len(entities_dicts):
        log.debug("!!!!Some identifiers were not mapped to any representative.!!!!")
    log.debug(
        f"Identifiers after representative mapping: {[entity['identifier'] for entity in entities_dicts]}"
    )

    semhash_record = SemHash.from_records(records=entities_dicts, columns=columns, model=semhash_model)
    deduplicated_records = semhash_record.self_deduplicate().selected_with_duplicates

    entity_groups = []
    for dedup in deduplicated_records:
        if len(dedup.duplicates) < 1:
            log.debug(f"Skipping dedup for record {dedup.record['identifier']} without duplicates")
            entity_groups.append(dedup.record)
            all_deduplicated_identifiers.append(dedup.record["identifier"].upper())
            continue

        if "data" not in dedup.record:
            continue
        deduplicated_node = merge_duplicate_group(dedup)
        entities_type = deduplicated_node.get("type")
        if type(entities_type) is str:
            entities_type = entities_type.upper()
        elif type(entities_type) is list:
            entities_type = [etype.upper() for etype in entities_type][0]
        deduplicated_node["type"] = entities_type
        dedup.record["data"] = deduplicated_node
        if dedup.record["identifier"] != dedup.record["representative_identifier"]:
            log.debug(
                f"Updating identifier {dedup.record['identifier']} to representative {dedup.record['representative_identifier']}"
            )
            dedup.record["identifier"] = dedup.record["representative_identifier"]

        all_deduplicated_identifiers.append(dedup.record["identifier"].upper())
        entity_groups.append(dedup.record)
    log.info(f"Total identifiers after deduplication: {len(all_deduplicated_identifiers)}")
    log.info(f"Total records after deduplication: {len(entity_groups)}")
    return entity_groups, list(dict.fromkeys(all_identifiers)), deduplicated_records


# Cross-stage alias detection -- in-run dedup uses SemHash + WordLlama to
# collapse surface-form variants, but `merge_run_into_saved` historically did
# exact identifier-string match only. The 2-stage Globalaid->Acme experiment
# surfaced real duplicates ("ACME FOUNDATION OF AMERICA" vs "THE ACME
# FOUNDATION OF AMERICA", "HELPING HAND" vs "HELPING HANDS", "MOSQUE
# FOUNDATION" vs "CHICAGO-AREA MOSQUE FOUNDATION", ...). Two-rule alias check,
# calibrated against 8 known pairs (5 true dups + 3 distinct):
#   Rule 1 (structural): smaller token set is a SUBSET of larger AND smaller
#     has >= 2 tokens. Catches THE-prefix and qualifier-prefix variants.
#   Rule 2 (semantic):   WordLlama similarity >= ALIAS_SIM_THRESHOLD AND
#     Jaccard >= ALIAS_JAC_THRESHOLD. Catches singular/plural and other
#     lemma-level variants where token sets differ but meaning matches.
# The HAMAS vs HAMAS-PROXIES-IN-GAZA / HAMAS-LINKED-GROUPS pairs (subset rule
# would naively fire) are excluded by the min-size-2 guard.
_ID_STOPS = {"THE", "A", "AN", "OF", "FOR", "AND", "TO", "IN", "ON"}
ALIAS_SIM_THRESHOLD = 0.90    # WordLlama cosine similarity
ALIAS_JAC_THRESHOLD = 0.50    # Jaccard on normalised token sets


def _id_tokens(identifier: str) -> set:
    """Uppercase + alphanumeric tokens, common stopwords removed.

    Dots are stripped first so dotted acronyms (`U.S.`, `I.B.M.`) tokenize as
    single tokens (`US`, `IBM`) rather than splitting into per-letter
    fragments -- otherwise `US TREASURY` and `U.S. DEPARTMENT OF THE TREASURY`
    would never satisfy the subset rule."""
    s = (identifier or "").upper().replace(".", "")
    return set(re.findall(r"[A-Z0-9]+", s)) - _ID_STOPS


def _find_alias_in_saved(new_id: str, saved_ids: list, saved_token_sets: dict) -> str | None:
    """Return a saved identifier that's an alias of ``new_id``, or None.

    Rule 1: structural subset, min-size 2. Rule 2: WordLlama similarity above
    threshold AND Jaccard overlap above threshold (so high-similarity but
    distinct entities -- e.g. HAMAS vs HAMAS-PROXIES -- are not collapsed)."""
    new_tokens = _id_tokens(new_id)
    if not new_tokens or not saved_ids:
        return None

    # Rule 1: structural subset of normalised token sets (min-size 2 guard)
    for sid in saved_ids:
        stoks = saved_token_sets[sid]
        if not stoks:
            continue
        smaller, larger = (new_tokens, stoks) if len(new_tokens) <= len(stoks) else (stoks, new_tokens)
        if smaller <= larger and len(smaller) >= 2:
            return sid

    # Rule 2: WordLlama similarity + Jaccard overlap.
    # Embed once + cosine vectorised.
    new_emb = np.asarray(_wl.embed([new_id]))[0]
    saved_embs = np.asarray(_wl.embed(saved_ids))
    new_emb = new_emb / (np.linalg.norm(new_emb) + 1e-9)
    saved_embs = saved_embs / (np.linalg.norm(saved_embs, axis=1, keepdims=True) + 1e-9)
    sims = saved_embs @ new_emb
    # rank candidates by similarity; check the best that also passes Jaccard
    for i in np.argsort(-sims):
        sim = float(sims[i])
        if sim < ALIAS_SIM_THRESHOLD:
            return None    # all remaining are below threshold
        stoks = saved_token_sets[saved_ids[i]]
        if not stoks:
            continue
        jac = len(new_tokens & stoks) / len(new_tokens | stoks)
        if jac >= ALIAS_JAC_THRESHOLD:
            return saved_ids[i]
    return None


# ---------------------------------------------------------------------------
# Event paraphrase-dedup
# ---------------------------------------------------------------------------

# A real-world incident is uniquely pinned down by (event_type, participant_set,
# date_bucket). Tolerances:
#   * event_type must match (and not be "" / "other" -- the catch-all bucket
#     doesn't pin down an incident)
#   * dates within EVENT_DEDUP_DATE_WINDOW_DAYS (both sides need at least
#     month-precision; year-only is refused as a wildcard)
#   * participant Jaccard >= EVENT_DEDUP_PARTICIPANT_JACCARD
#
# Known limit (calibrated from the big-run dry-run): when participants are
# IDENTICAL and dates are close, distinct actions on the same actors can
# over-merge (e.g. "US lifts sanctions on Albanese" vs "US returns Albanese
# to sanctions list", or "FAA lifts production cap" vs "FAA expects MAX 7
# certification"). A secondary identifier-token check was prototyped but
# rejected because it lost legitimate paraphrases (different surface wording
# of the same event). Add a description-aware second pass if real-world
# noise exceeds the value of the merge.
EVENT_DEDUP_DATE_WINDOW_DAYS = 7
EVENT_DEDUP_PARTICIPANT_JACCARD = 0.60
# Semantic merge path: the structured signature (event_type/date/participants)
# is extraction-noisy -- especially for a single document re-chunked many ways,
# where one incident is paraphrased into N events with drifting types, dates,
# and placeholder participants ("victim"/"SUS 1"). The event NAME is the
# cleanest signal, but is otherwise only exact-matched. A high WordLlama cosine
# over the name, gated by date-compatibility, collapses those paraphrases
# without folding distinct same-week incidents together. (Description is
# excluded: it varies per chunk and dilutes the name signal -- name-only cosine
# ~0.9 for the same incident vs ~0.4 for a genuinely different one.)
EVENT_DEDUP_COSINE_THRESHOLD = 0.86


def _event_type(ev_record: dict) -> str:
    """The event_type token, lower-cased. Returns '' when unknown."""
    t = (ev_record.get("data") or {}).get("event_type") or ""
    if isinstance(t, list):
        t = next((x for x in t if x), "")
    return str(t or "").strip().lower()


def _event_dates(ev_record: dict) -> list[str]:
    """Return the date value(s) on the event, normalised to lowercase strings."""
    d = (ev_record.get("data") or {}).get("date")
    if d is None:
        return []
    if isinstance(d, list):
        return [str(x).strip() for x in d if x]
    return [str(d).strip()] if str(d).strip() else []


def _parse_iso_date(s: str) -> tuple[int, int, int] | None:
    """Best-effort parse of ISO-8601 dates (YYYY, YYYY-MM, YYYY-MM-DD) into a
    (year, month, day) tuple. Returns None when the string isn't recognisable.
    Missing components are filled with 0 (a sentinel; date comparison treats
    0-month and 0-day as wildcards).
    """
    if not s: return None
    m = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", s)
    if not m: return None
    y = int(m.group(1))
    mo = int(m.group(2) or 0)
    da = int(m.group(3) or 0)
    return (y, mo, da)


def to_iso_date(value) -> str:
    """Normalise a publication-date string to ``YYYY-MM-DD`` (or "" if unparseable).

    Article sources emit dates in several formats: GNews gives RFC-2822
    ("Wed, 15 May 2024 12:00:00 GMT"), GDELT gives compact "20240515T120000Z",
    others give ISO already. This collapses all of them to a plain ISO day so the
    temporal layer can compare them. Time-of-day is dropped (day precision).
    """
    s = str(value or "").strip()
    if not s:
        return ""
    # Already-ISO (or ISO-prefixed) date.
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    if m:
        return m.group(0)
    # GDELT compact: YYYYMMDD[THHMMSSZ].
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # RFC-2822 (GNews) and similar.
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return dt.date().isoformat()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _dates_compatible(a_dates: list[str], b_dates: list[str], *, window_days: int) -> bool:
    """True iff at least one date on each side is within `window_days` of the
    other. Wildcards (missing day, missing month, empty) match liberally."""
    if not a_dates or not b_dates:
        return True   # at least one side has no date attested -> can't disprove
    for a in a_dates:
        pa = _parse_iso_date(a)
        if pa is None: continue
        ya, ma, da = pa
        for b in b_dates:
            pb = _parse_iso_date(b)
            if pb is None: continue
            yb, mb, db = pb
            if ya != yb:
                continue
            # year matches; check month/day windows
            if ma == 0 or mb == 0:
                return True   # one is year-only -> compatible
            if ma != mb:
                # different months -> check actual day-distance
                import datetime
                try:
                    da_x = datetime.date(ya, ma, da or 1)
                    db_x = datetime.date(yb, mb, db or 1)
                    if abs((da_x - db_x).days) <= window_days:
                        return True
                except ValueError:
                    pass
                continue
            # same month
            if da == 0 or db == 0:
                return True
            if abs(da - db) <= window_days:
                return True
    return False


def _participant_names(ev_record: dict) -> set:
    """Uppercased participant names on this event (deduped)."""
    parts = (ev_record.get("data") or {}).get("participants") or []
    names = set()
    for p in parts:
        if isinstance(p, dict):
            n = (p.get("name") or "").strip().upper()
            if n:
                names.add(n)
        elif isinstance(p, str):
            n = p.strip().upper()
            if n:
                names.add(n)
    return names


def _events_match(a: dict, b: dict) -> bool:
    """True iff events a and b refer to the same real-world incident under
    the (event_type, date_window, participant-jaccard) rule.

    Two extra guards calibrated from the big-run dry-run:
      * Refuse merge when either event_type is "" or "other". "other" is a
        catch-all bucket; same-participants + "other" + close date doesn't
        pin down a single real-world incident (the big-run dry-run merged
        three distinct FAA->Boeing actions because they shared Boeing+FAA
        participants and all fell under "other").
      * Refuse merge on year-only dates. If at least one side has only a
        year (no month/day), the date window is too loose for high-frequency
        actor pairs.
    """
    ta, tb = _event_type(a), _event_type(b)
    if not ta or not tb or ta != tb:
        return False  # both must declare the same specific category
    if ta == "other":
        return False  # catch-all; don't dedup across it
    # Both sides need at least month-precision dates (year-only is wildcard
    # for the compatibility check, which is too loose for safe merging).
    def _has_month_precision(dates: list[str]) -> bool:
        for s in dates:
            p = _parse_iso_date(s)
            if p and p[1] != 0:
                return True
        return False
    da, db = _event_dates(a), _event_dates(b)
    if not _has_month_precision(da) or not _has_month_precision(db):
        return False
    if not _dates_compatible(da, db, window_days=EVENT_DEDUP_DATE_WINDOW_DAYS):
        return False
    pa, pb = _participant_names(a), _participant_names(b)
    if not pa or not pb:
        # If one side has zero participants, we can't tell them apart; be
        # conservative and refuse the merge.
        return False
    part_jac = len(pa & pb) / len(pa | pb)
    return part_jac >= EVENT_DEDUP_PARTICIPANT_JACCARD


def _event_text(ev_record: dict) -> str:
    """The event NAME -- the high-signal text for semantic event matching.
    Description is excluded on purpose: it varies per chunk and dilutes the
    name signal (name-only cosine ~0.9 for the same incident vs ~0.4 for a
    genuinely different one)."""
    return (ev_record.get("identifier") or "").strip()


def _events_semantically_match(a: dict, b: dict, *,
                               threshold: float = EVENT_DEDUP_COSINE_THRESHOLD) -> bool:
    """Complementary semantic merge path. When the structured signature is too
    noisy to fire (drifting event_type, scattered/`other` dates, placeholder
    participants), fall back to WordLlama cosine over the event name +
    description. Gated by date-compatibility (compatible-or-missing) so two
    distinct same-week incidents phrased alike are not folded together."""
    ta, tb = _event_text(a), _event_text(b)
    if not ta or not tb:
        return False
    if not _dates_compatible(_event_dates(a), _event_dates(b),
                             window_days=EVENT_DEDUP_DATE_WINDOW_DAYS):
        return False
    try:
        return float(_wl.similarity(ta, tb)) >= threshold
    except Exception:  # noqa: BLE001 -- never let an embedding failure break dedup
        return False


EVENT_TEMPORAL_COINCIDENT_DAYS = 3       # |d| <= this -> coincident (undirected)
EVENT_TEMPORAL_MAX_DAYS = 60             # |d| > this -> no edge (too far)
EVENT_TEMPORAL_MIN_SHARED_PARTICIPANTS = 1  # at least one shared actor


def _day_precise_date(dates: list[str]) -> tuple[int, int, int] | None:
    """Return the most informative (y, m, d) tuple from a list of ISO-date
    strings, requiring DAY-precision (YYYY-MM-DD). Returns None if no
    day-precise date is available.

    Day-precision is necessary for temporal-edge inference: a month-only
    date like ``"2026-05"`` parses to (2026, 5, 0) which we used to treat
    as May 1 -- but if two events both have "2026-05" they'd appear
    "0 days apart" without justification. Requiring day-precision keeps
    the temporal layer honest at the cost of dropping some events that
    don't carry a specific day.
    """
    parsed = []
    for s in dates:
        p = _parse_iso_date(s)
        if p is None:
            continue
        y, m, da = p
        if m == 0 or da == 0:
            continue   # require both month and day
        parsed.append((y, m, da))
    if not parsed:
        return None
    # Take the earliest day-precise date as the canonical one.
    return min(parsed)


def _to_date_obj(t):
    import datetime
    return datetime.date(t[0], t[1], t[2])


def infer_event_temporal_edges(events: list[dict]) -> list[dict]:
    """Programmatically infer event-to-event edges from shared participants
    and date ordering. No LLM call -- the signal is entirely from data
    already attested per event.

    For each pair of events with at least one shared participant:
      * |date_a - date_b| <= EVENT_TEMPORAL_COINCIDENT_DAYS -> emit one
        ``event_coincident`` edge (undirected; serialised with lexicographic
        src/dst for stability).
      * EVENT_TEMPORAL_COINCIDENT_DAYS < |delta| <= EVENT_TEMPORAL_MAX_DAYS
        -> emit one ``event_followed_by`` edge directed from earlier event
        to later one.
      * |delta| > EVENT_TEMPORAL_MAX_DAYS -> no edge (events are too far
        apart to be analytically chained as a sequence).

    Returns edges in the same shape as edges_enrichment_results so they can
    flow through merge_run_into_saved and into the response.
    """
    import uuid as _uuid

    cache = []
    for e in events:
        date_t = _day_precise_date(_event_dates(e))
        parts = _participant_names(e)
        cache.append({"event": e, "date_tuple": date_t, "participants": parts})

    out_edges: list[dict] = []
    for i in range(len(cache)):
        for j in range(i + 1, len(cache)):
            a, b = cache[i], cache[j]
            if not a["date_tuple"] or not b["date_tuple"]:
                continue
            shared = a["participants"] & b["participants"]
            if len(shared) < EVENT_TEMPORAL_MIN_SHARED_PARTICIPANTS:
                continue
            try:
                da = _to_date_obj(a["date_tuple"])
                db = _to_date_obj(b["date_tuple"])
            except Exception:
                continue
            diff_days = (db - da).days
            abs_diff = abs(diff_days)
            if abs_diff > EVENT_TEMPORAL_MAX_DAYS:
                continue

            if abs_diff <= EVENT_TEMPORAL_COINCIDENT_DAYS:
                e1, e2 = sorted([a["event"], b["event"]], key=lambda e_: e_["identifier"])
                edge_type = "event_coincident"
                rel_type = "coincident"
                ctx = (f"Events occurred within {abs_diff} day(s) of each other; "
                       f"shared participants: {', '.join(sorted(shared))}")
            else:
                if diff_days > 0:
                    e1, e2 = a["event"], b["event"]
                else:
                    e1, e2 = b["event"], a["event"]
                edge_type = "event_followed_by"
                rel_type = "followed_by"
                ctx = (f"Earlier event preceded the later by {abs_diff} days; "
                       f"shared participants: {', '.join(sorted(shared))}")

            out_edges.append({
                "unique_identifier": str(_uuid.uuid4()),
                "src_identifier": e1["identifier"],
                "dst_identifier": e2["identifier"],
                "src_unique_identifier": e1.get("unique_identifier", ""),
                "dst_unique_identifier": e2.get("unique_identifier", ""),
                "type": edge_type,
                "relations": json.dumps({"type": rel_type, "context": ctx}),
                "attributes": {
                    "shared_participants": sorted(shared),
                    "days_apart": abs_diff,
                },
                "source": "programmatic_inference",
                "search_url": "",
            })
    return out_edges


def _merge_event_pair(canonical: dict, alias: dict) -> None:
    """Merge `alias` event into `canonical` in place: union descriptions,
    source_urls, participants, and dates; record the alias's identifier as a
    label on canonical."""
    cdata = canonical.setdefault("data", {})
    adata = alias.get("data") or {}

    # Append alias identifier as a label so the surface form is preserved.
    labels = canonical.setdefault("labels", [])
    a_id = alias.get("identifier")
    if a_id and a_id != canonical.get("identifier") and a_id not in labels:
        labels.append(a_id)

    # Union list-able fields
    def _union(field, hashable=True):
        existing = cdata.get(field)
        incoming = adata.get(field)
        if existing is None and incoming is None:
            return
        bag = []
        for v in (existing if isinstance(existing, list) else [existing]):
            if v is None or v == "": continue
            bag.append(v)
        for v in (incoming if isinstance(incoming, list) else [incoming]):
            if v is None or v == "": continue
            bag.append(v)
        if not bag:
            return
        if hashable:
            seen = set(); out = []
            for v in bag:
                key = v if not isinstance(v, (list, dict)) else json.dumps(v, sort_keys=True)
                if key in seen: continue
                seen.add(key); out.append(v)
        else:
            out = bag
        cdata[field] = out[0] if len(out) == 1 else out

    _union("description")
    _union("source_url")
    _union("date")
    _union("location")

    # Participants: union by uppercased name; keep first role we saw per name
    c_parts = cdata.get("participants") or []
    a_parts = adata.get("participants") or []
    merged_parts: list = []
    seen_names: set = set()
    for p in list(c_parts) + list(a_parts):
        if not isinstance(p, dict):
            continue
        n = (p.get("name") or "").strip().upper()
        if not n or n in seen_names:
            continue
        seen_names.add(n)
        merged_parts.append(p)
    if merged_parts:
        cdata["participants"] = merged_parts

    # Max confidence
    def _to_float(x):
        try: return float(x)
        except Exception: return 0.0
    c_conf = cdata.get("confidence", 0.0)
    if isinstance(c_conf, list): c_conf = max((_to_float(x) for x in c_conf), default=0.0)
    a_conf = adata.get("confidence", 0.0)
    if isinstance(a_conf, list): a_conf = max((_to_float(x) for x in a_conf), default=0.0)
    cdata["confidence"] = max(_to_float(c_conf), _to_float(a_conf))

    # Runs / chunk_uuids: union
    c_runs = canonical.get("runs") or []
    a_runs = alias.get("runs") or []
    if c_runs or a_runs:
        canonical["runs"] = list(dict.fromkeys(list(c_runs) + list(a_runs)))


def dedup_events_by_signature(events: list[dict]) -> list[dict]:
    """Collapse event-records that refer to the same real-world incident.

    Matches by (event_type, date_window, participant-overlap). The first
    event in the input list wins as canonical; subsequent matching events
    are merged into it (descriptions / source_urls / participants / dates
    unioned; alias identifier recorded as a label). Singletons pass through
    unchanged.

    Strategy C calibration (from the big-run observation): paraphrases of the
    May-16 strike on Haddad all agreed on event_type=military_action,
    date~2026-05-16, participants={ISRAEL, HAMAS, IZZ AL-DIN AL-HADDAD};
    the (type, date, participant-Jaccard >= 0.5, +/- 7d) rule cleanly
    collapses them without folding distinct same-week incidents together.
    """
    out: list[dict] = []
    for incoming in events:
        merged = False
        for canonical in out:
            if _events_match(canonical, incoming) or _events_semantically_match(canonical, incoming):
                _merge_event_pair(canonical, incoming)
                merged = True
                break
        if not merged:
            out.append(incoming)
    return out


def _merge_runs(saved_runs, new_runs):
    """Union two run-label lists, preserving order, no duplicates.

    Returns None when both inputs are empty/None so legacy records that
    never carried a `runs` field stay legacy (and the to_dict round-trip
    keeps the field absent). Returns a fresh list otherwise -- the caller
    assigns it onto the saved record only if the result is non-None.
    """
    saved_runs = saved_runs or []
    new_runs = new_runs or []
    if not saved_runs and not new_runs:
        return None
    out = list(saved_runs)
    for r in new_runs:
        if r not in out:
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Post-hoc canonical validation (intercepts headline-shaped identifiers)
# ---------------------------------------------------------------------------

# Words that are strongly indicative of an event description rather than an
# entity name. Used as a second-line defence behind the word-count cap.
# Curated to avoid false positives (no "FORCED" since legitimate names
# include "forced labour coalition", no "CAUSES" since "known causes
# campaign" exists, etc.). Add cautiously.
_HEADLINE_VERBS = frozenset({
    "KILLS", "KILLED", "STRIKES", "STRUCK",
    "SANCTIONS", "SANCTIONED", "DESIGNATES", "DESIGNATED",
    "INDICTS", "INDICTED", "ARRESTS", "ARRESTED",
    "APPROVES", "APPROVED", "CLEARS", "CLEARED",
    "LIFTS", "LIFTED", "TRIGGERS", "TRIGGERED",
    "DEPORTS", "DEPORTED", "INTERCEPTS", "INTERCEPTED",
    "REPLACES", "REPLACED", "ORDERS", "ORDERED",
    "DEMANDS", "DEMANDED", "EXPECTS", "EXPECTED",
    "COMPLETES", "COMPLETED", "INCREASES", "INCREASED",
    "REVEALS", "REVEALED", "ANNOUNCES", "ANNOUNCED",
    "ELIMINATED", "ELIMINATES", "DISASSEMBLE",
    "SEIZE", "SEIZED", "BLOCKS", "BLOCKED",
    "HALTS", "HALTED", "ENFORCES", "ENFORCED",
    "RAMPS", "RAMP",
})

# Word-count cap for a valid canonical identifier. Tuned so that
# legitimate long proper-noun names ("Helping Hand for Relief and
# Development", "United Nations Relief and Works Agency") pass while
# typical news headlines fail.
_CANONICAL_MAX_WORDS = 7

# Content-free / boilerplate words. A name made up ENTIRELY of these carries no
# proper-noun content and is not a real entity -- e.g. the NER artefact
# "ALL OTHER UNIQUE IDENTIFIERS" (from KYC/legal text), which otherwise passes
# the verb + word-count checks and, worse, gets picked by MRI as a cluster's
# representative name and becomes a merge sink. A real name always carries at
# least one non-generic token, so the all-generic test is safe.
_GENERIC_NAME_TERMS = frozenset({
    # determiners / quantifiers
    "ALL", "OTHER", "OTHERS", "ANY", "EACH", "EVERY", "SOME", "SUCH", "CERTAIN",
    "VARIOUS", "SEVERAL", "MANY", "FEW", "BOTH", "THESE", "THOSE", "THIS",
    "THAT", "THE", "A", "AN", "NO", "NONE", "SAME", "MORE", "MOST",
    # connectors
    "AND", "OR", "OF", "ETC",
    # generic content nouns
    "UNIQUE", "IDENTIFIER", "IDENTIFIERS", "INFORMATION", "DETAIL", "DETAILS",
    "DATA", "NAME", "NAMES", "NUMBER", "NUMBERS", "RECORD", "RECORDS", "ITEM",
    "ITEMS", "THING", "THINGS", "ENTITY", "ENTITIES", "PERSON", "PERSONS",
    "PEOPLE", "PARTY", "PARTIES", "INDIVIDUAL", "INDIVIDUALS", "COMPANY",
    "COMPANIES", "ORGANIZATION", "ORGANIZATIONS", "ORGANISATION",
    "ORGANISATIONS", "ENTRY", "ENTRIES", "FIELD", "FIELDS", "VALUE", "VALUES",
    "TYPE", "TYPES", "ACCOUNT", "ACCOUNTS", "REFERENCE", "REFERENCES",
})


def _is_valid_canonical(name) -> bool:
    """True iff `name` reads as a noun-phrase entity identifier rather
    than a headline / event description.

    Two checks:
      * Word count <= _CANONICAL_MAX_WORDS (headlines tend to be longer).
      * No token from _HEADLINE_VERBS (action verbs that mark events).

    Non-strings, empty strings, and lists are rejected.
    """
    if not isinstance(name, str):
        return False
    s = name.strip()
    if not s:
        return False
    words = s.split()
    if len(words) > _CANONICAL_MAX_WORDS:
        return False
    upper_tokens = set(re.findall(r"[A-Z]+", s.upper()))
    if upper_tokens & _HEADLINE_VERBS:
        return False
    # Content-free: every alphabetic token is a generic/boilerplate word, so the
    # name carries no proper-noun content (e.g. "ALL OTHER UNIQUE IDENTIFIERS").
    if upper_tokens and upper_tokens <= _GENERIC_NAME_TERMS:
        return False
    return True


def _find_better_canonical(record: dict) -> str | None:
    """Return the shortest valid label on this record, or None if there
    isn't one. Used to replace a record's headline-shaped identifier with
    the most plausible noun-phrase alternative from its surface variants.
    """
    labels = record.get("labels") or []
    valid = [lab for lab in labels if _is_valid_canonical(lab)]
    if not valid:
        return None
    return min(valid, key=len)


def validate_entity_canonicals(entities: list[dict]) -> int:
    """Walk entity records; for any record whose `identifier` is not a
    valid canonical (e.g. a headline string survived MRI + SemHash),
    swap the identifier with the shortest valid label. The old
    identifier becomes a label (so the surface form is preserved).

    Events (type == "event") are deliberately skipped -- their long
    descriptive identifiers are correct by design.

    Returns the count of records actually rewritten.
    """
    n_fixed = 0
    for rec in entities:
        if rec.get("type") == "event":
            continue
        ident = rec.get("identifier") or ""
        if _is_valid_canonical(ident):
            continue
        better = _find_better_canonical(rec)
        if better is None:
            continue
        old_ident = ident
        new_ident = better.upper().strip()
        rec["identifier"] = new_ident
        # Move the old (invalid) identifier into labels for provenance;
        # remove the new canonical from labels (it now lives on identifier).
        labels = list(rec.get("labels") or [])
        labels = [lab for lab in labels if lab.upper() != new_ident]
        if old_ident and old_ident not in labels:
            labels.append(old_ident)
        rec["labels"] = labels
        rec["representative_identifier"] = new_ident
        log.info(f"Canonical-fix: {old_ident[:60]!r} -> {new_ident!r}")
        n_fixed += 1
    return n_fixed


def _relation_informative(relations) -> bool:
    # An edge's relation is informative when it carries a real type (not blank /
    # "unknown") or a non-empty context string. `relations` is a JSON string by
    # the time edges reach the merge (resolve_edge_endpoints json.dumps it), but
    # tolerate a dict too.
    if isinstance(relations, str):
        if not relations:
            return False
        try:
            relations = json.loads(relations)
        except (ValueError, TypeError):
            return False
    if not isinstance(relations, dict):
        return False
    rtype = (relations.get("type") or "").strip().lower()
    context = (relations.get("context") or "").strip()
    return (rtype not in ("", "unknown")) or bool(context)


def merge_run_into_saved(edges_enrichment_results, merged_entities, saved_edges, saved_nodes):
    # Index the persisted (accumulating) side once. saved_nodes / saved_edges
    # grow every run, so the old O(dedup x saved) / O(new x saved) scans
    # degraded over a session's lifetime; these lookups make merge O(dedup+new).
    saved_by_id = {sn["identifier"]: sn for sn in saved_nodes}
    saved_ids = list(saved_by_id)
    # Upper-cased identifier index for the symmetric surface-form match (Rule 4).
    saved_by_id_upper = {sid.upper(): sid for sid in saved_ids}
    # Precompute token sets for the structural alias check (Rule 1).
    saved_token_sets = {sid: _id_tokens(sid) for sid in saved_ids}
    # Rule 3 (label match): upper-cased label -> saved identifier. When an
    # incoming entity's identifier already lives in a saved record's `labels`
    # list, the LLM has previously grouped them as one entity within a POST.
    # That is a high-precision signal Rules 1+2 miss when the surface forms
    # diverge structurally (PUTIN/VLADIMIR PUTIN, HORMUZ/STRAIT OF HORMUZ)
    # -- subset rule fails on size-1 smaller, WordLlama sim sits below 0.90.
    saved_labels_index: dict[str, str] = {}
    for sid, snode in saved_by_id.items():
        for label in (snode.get("labels") or []):
            if not isinstance(label, str):
                continue
            key = label.upper().strip()
            if key and key != sid.upper():
                saved_labels_index.setdefault(key, sid)
    saved_edges_by_src: dict = {}
    saved_edges_by_dst: dict = {}
    for se in saved_edges:
        saved_edges_by_src.setdefault(se["src_identifier"], []).append(se)
        saved_edges_by_dst.setdefault(se["dst_identifier"], []).append(se)
    saved_edge_by_pair: dict = {}
    for se in saved_edges:
        saved_edge_by_pair.setdefault((se["src_identifier"], se["dst_identifier"]), se)

    # Cross-stage alias map: a new node whose identifier didn't exact-match
    # against saved but is detected as an alias of a saved entity gets merged
    # into the saved record. The mapping is also used to rewrite incoming edge
    # endpoints (so the new run's edges that reference the alias point at the
    # canonical saved identifier instead).
    alias_to_canonical: dict = {}

    for node in merged_entities[:]:
        new_id = node["identifier"]
        saved_node = saved_by_id.get(new_id)
        is_alias = False
        # Events skip the alias matcher: their identifiers are long
        # descriptive prose, and the WordLlama+Jaccard rules tuned for
        # ORG/PERSON names over-collapse paraphrased event names (e.g.
        # "ISRAELI STRIKE KILLS HAMAS LEADER X" vs "STRIKE ON HAMAS X").
        # Exact-id match still applies, so identical-name events from
        # different runs merge cleanly. Strategy C: a dedicated
        # name+date+participants matcher for events is a follow-up.
        if saved_node is None and saved_ids and node.get("type") != "event":
            # Rule 3 (label match) first: only honour it when the incoming
            # identifier itself is a plausible noun-phrase canonical, so a
            # stray headline that lingered as a label cannot pull an unrelated
            # incoming entity into the saved record.
            matched_sid = None
            if _is_valid_canonical(new_id):
                matched_sid = saved_labels_index.get(new_id.upper().strip())
                if matched_sid == new_id:
                    matched_sid = None
            # Rule 4 (symmetric surface-form match): the incoming node's OWN
            # alternate names -- its representative_identifier and labels -- are
            # checked against saved identifiers AND saved labels. S1 and S2 pick
            # canonicals independently, so the same entity can end up as S1
            # "INTERNATIONAL CRIMINAL COURT" and S2 "ICC" (carrying the other as
            # a label). Rule 3 only checks the incoming *identifier*, so it
            # misses this; matching the incoming labels too catches it.
            if matched_sid is None:
                for cand in [node.get("representative_identifier"), *(node.get("labels") or [])]:
                    if not isinstance(cand, str):
                        continue
                    cu = cand.upper().strip()
                    if not cu or not _is_valid_canonical(cand):
                        continue
                    sid = saved_by_id_upper.get(cu) or saved_labels_index.get(cu)
                    if sid and sid != new_id:
                        matched_sid = sid
                        break
            if matched_sid is None:
                matched_sid = _find_alias_in_saved(new_id, saved_ids, saved_token_sets)
            if matched_sid is not None:
                saved_node = saved_by_id[matched_sid]
                is_alias = True
                alias_to_canonical[new_id] = matched_sid
                # Preserve the surface variant as a label on the canonical node.
                labels = saved_node.setdefault("labels", [])
                if new_id != matched_sid and new_id not in labels:
                    labels.append(new_id)
                log.info(
                    f"Cross-stage alias merged: {new_id!r} -> {matched_sid!r}"
                )
        if saved_node is None:
            continue
        deduplicated_node_data = merge_data_fields([saved_node["data"], node["data"]])
        if not is_alias:
            # Exact-match path: refresh identity fields from the new node.
            # In the alias path we PRESERVE the saved canonical identifier /
            # unique_identifier / representative_identifier -- the variant is
            # already recorded as a label above.
            saved_node["identifier"] = node["identifier"]
            saved_node["type"] = node.get("type")
            saved_node["unique_identifier"] = node.get("unique_identifier")
        saved_node["labels"] = list(set(saved_node.get("labels", []) + node.get("labels", [])))
        if not is_alias:
            saved_node["representative_identifier"] = node.get(
                "representative_identifier", saved_node.get("representative_identifier")
            )
        saved_node["triangulated"] = saved_node.get("triangulated", False) or node.get("triangulated", False)
        saved_node["hypothesis"] = saved_node.get("hypothesis", False) or node.get("hypothesis", False)
        saved_node["source"] = str(node.get("source", ""))
        saved_node["prob"] = max(saved_node.get("prob", 0.0), node.get("prob", 0.0))
        saved_node["leaf"] = saved_node.get("leaf", False) or node.get("leaf", False)
        saved_node["evidence"] = saved_node.get("evidence", []) + node.get("evidence", [])
        saved_node["evidence_count"] = len(saved_node.get("evidence", []))
        saved_node["self_evidence"] = node.get("self_evidence", saved_node.get("self_evidence", {}))
        # Cross-run provenance: union the runs lists. Saved record carries
        # every run that has ever attested it; the incoming node carries the
        # run(s) attesting it in THIS POST. Legacy records (no runs) stay
        # legacy unless an incoming `run` actually arrives.
        merged_runs = _merge_runs(saved_node.get("runs"), node.get("runs"))
        if merged_runs is not None:
            saved_node["runs"] = merged_runs
        uid = node.get("unique_identifier")
        for edge in saved_edges_by_src.get(node["identifier"], []):
            edge["src_unique_identifier"] = uid
        for edge in saved_edges_by_dst.get(node["identifier"], []):
            edge["dst_unique_identifier"] = uid
        # Same merge policy as dedup (M1-M3): distinct union for multi-valued
        # scalar fields, clean lists (not ":"-joined strings), relevance kept as
        # the max merge_data_fields already computed.
        for key, value in list(deduplicated_node_data.items()):
            if key == "relevance_score":
                continue
            items = _as_list(value)
            if not (items and all(isinstance(v, (str, int, float)) for v in items)):
                continue
            clean = [v for v in dict.fromkeys(items) if not _is_empty_value(v)]
            deduplicated_node_data[key] = (clean if len(clean) > 1 else clean[0]) if clean else "Not found"
        saved_node["data"] = deduplicated_node_data
        merged_entities.remove(node)

    # If any cross-stage aliases were detected, rewrite incoming edges that
    # reference the alias identifier to point at the canonical saved id.
    # Otherwise downstream lookups (graph rendering, response shaping) leave
    # the alias as a phantom endpoint distinct from its canonical entity.
    if alias_to_canonical:
        for edge in edges_enrichment_results:
            if edge.get("src_identifier") in alias_to_canonical:
                edge["src_identifier"] = alias_to_canonical[edge["src_identifier"]]
            if edge.get("dst_identifier") in alias_to_canonical:
                edge["dst_identifier"] = alias_to_canonical[edge["dst_identifier"]]

    for edge in edges_enrichment_results[:]:
        saved_edge = saved_edge_by_pair.get((edge["src_identifier"], edge["dst_identifier"]))
        if saved_edge is not None:
            saved_edge["source"] = str(edge.get("source", ""))
            saved_edge["attributes"] = merge_data_fields(
                [saved_edge.get("attributes", {}), edge.get("attributes", {})]
            )
            # Back-fill the relation label: when the saved edge was first created
            # with a blank/"unknown" relation but a later run characterized the
            # same pair, adopt the incoming label instead of silently keeping the
            # empty one. Never overwrite an already-informative saved relation.
            if not _relation_informative(saved_edge.get("relations")) and _relation_informative(
                edge.get("relations")
            ):
                saved_edge["relations"] = edge.get("relations")
            merged_runs = _merge_runs(saved_edge.get("runs"), edge.get("runs"))
            if merged_runs is not None:
                saved_edge["runs"] = merged_runs
            edges_enrichment_results.remove(edge)

    return edges_enrichment_results, merged_entities, saved_edges, saved_nodes


def resolve_edge_endpoints(
    nodes_grouped_by_name: list[dict],
    edges_enrichment_results: list[dict],
) -> list[dict]:
    import json
    import uuid

    # Index nodes by identifier and representative_identifier. First node to
    # claim a key wins, matching the old first-matching-node-in-iteration-order
    # scan. Replaces the O(nodes x edges) double loop with a single pass.
    by_id: dict = {}
    for node in nodes_grouped_by_name:
        for key in (node["identifier"], node.get("representative_identifier")):
            if key and key not in by_id:
                by_id[key] = node

    registred_edges = []
    for edge in edges_enrichment_results:
        # Assign the edge's UUID exactly ONCE. Previously this lived inside the
        # per-node loop, so it was regenerated N_nodes times per edge.
        edge["unique_identifier"] = str(uuid.uuid4())
        edge["type"] = "affiliation"

        src = by_id.get(edge.get("source_node"))
        if src is not None:
            edge["src_identifier"] = src["identifier"]
            edge["src_unique_identifier"] = src["unique_identifier"]
            edge.pop("source_node", None)
        dst = by_id.get(edge.get("target_node"))
        if dst is not None:
            edge["dst_identifier"] = dst["identifier"]
            edge["dst_unique_identifier"] = dst["unique_identifier"]
            edge.pop("target_node", None)

        edge["relations"] = json.dumps(edge["relations"])
        if "src_identifier" in edge and "dst_identifier" in edge:
            registred_edges.append(edge)

    log.info(
        f"Total FINAL validated edges with connections to existing nodes: {len(registred_edges)}"
    )
    return registred_edges


def attach_relations_to_nodes(nodes_grouped_by_name, edges_enrichment_results):
    # Index nodes by identifier + representative_identifier (first node wins),
    # then sweep edges once instead of scanning every node x every edge.
    for node in nodes_grouped_by_name:
        node["data"]["relations"] = []
    node_by_key: dict = {}
    for node in nodes_grouped_by_name:
        for key in (node["identifier"], node.get("representative_identifier")):
            if key and key not in node_by_key:
                node_by_key[key] = node

    for edge in edges_enrichment_results:
        if not edge.get("relations"):
            continue
        # Resolve source_node's outgoing relation before the target_node block
        # rewrites source_node; related_node is descriptive metadata (raw name
        # or its canonical rep).
        src = node_by_key.get(edge.get("source_node")) if "source_node" in edge else None
        if src is not None:
            src["data"]["relations"].append(
                {
                    "direction": "outgoing",
                    "related_node": edge["target_node"],
                    "relations": edge["relations"],
                    "attributes": edge["attributes"],
                }
            )
            edge["source_node"] = src["representative_identifier"]
        dst = node_by_key.get(edge.get("target_node")) if "target_node" in edge else None
        if dst is not None:
            dst["data"]["relations"].append(
                {
                    "direction": "incoming",
                    "related_node": edge["source_node"],
                    "relations": edge["relations"],
                    "attributes": edge["attributes"],
                }
            )
            edge["target_node"] = dst["representative_identifier"]

    for node in nodes_grouped_by_name:
        node["triangulated"] = len(node["data"]["relations"]) > 0
    return nodes_grouped_by_name
