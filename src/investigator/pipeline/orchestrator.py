"""Investigation pipeline orchestrator.

Contains everything that used to live in the route handler of
``investigator_server.py``:

* DSPy LLM configuration (one-time, at import).
* Static embedding/similarity model loaded once (``semhash_model``).
* Async helpers for entity / evidence / edge extraction that fan out
  per-chunk and gather.
* ``InvestigationPipeline`` — the class the Flask route delegates to.

The class is thin: it parses the request and runs the seven-step standard
pipeline
(extract → dedup → graph build → triangulate → enrich → evidence → save).
Each step is its own method so they can be tested in isolation in Phase 3.

DSPy + heavy model loads happen at module import. Caller must export
``OPENAI_API_KEY`` (via ``SecretLoader`` or the environment) before
importing this module.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import traceback
import uuid

import dspy
import networkx as nx
from model2vec import StaticModel

from investigator.graph import (
    attach_relations_to_nodes,
    build_graph,
    cluster_identifiers,
    construct_tmfg,
    dedup_events_by_signature,
    deduplicate_entities,
    infer_event_temporal_edges,
    to_iso_date,
    validate_entity_canonicals,
    evidence_probability,
    filter_by_corroboration,
    group_edges_by_chunk,
    junction_tree_propagate,
    merge_run_into_saved,
    preprocess_text,
    resolve_edge_endpoints,
    score_graph_by_connectivity,
    tetrahedron_weight,
    visualize_graph,
)
from investigator.llm import (
    CausalClaimsExtraction,
    Edge,
    Entity,
    EventsRecognition,
    Evidence,
    ExtractEvidenceFromJSONText,
    ExtractInvestigationSubject,
    GraphEdgesEnrichment,
    InvestigateEvidenceFromJSONText,
    MostRepresentativeIdentifier,
    NamedEntitiesRecognition,
)
from investigator.logging import get_logger
from investigator.pipeline.chunking import json_chunker, text_chunker
from investigator.pipeline.response_builder import build_network_analysis_payload
from investigator.pipeline.hypothesis import return_hypothesis_for_domain
from investigator.state import EdgeRecord, EntityRecord, InvestigationState
from investigator.utils import find_value_in_nested_dict, flatten_and_clean_dict, remove_empty_fields

log = get_logger()


# --- Heavy model singletons ----------------------------------------------

semhash_model = StaticModel.from_pretrained("minishlab/potion-multilingual-128M")


# --- DSPy LM configuration ------------------------------------------------

lm = dspy.LM("openai/gpt-4.1", temperature=0.0, max_tokens=32000)
extraction_lm = dspy.LM("openai/gpt-4.1", temperature=0.0, max_tokens=32000)
# Concurrency of the NER/extraction fan-out. Each worker holds a payload chunk
# plus a (large) gpt-4.1 response in flight, so this is the main driver of the
# transient memory spike during Stage-1/2. Lower it on memory-constrained hosts
# (INVESTIGATOR_ASYNC_WORKERS) to trade throughput for a smaller peak.
_ASYNC_WORKERS = int(os.environ.get("INVESTIGATOR_ASYNC_WORKERS", "16"))
dspy.configure(lm=lm, async_max_workers=_ASYNC_WORKERS)
# Memory cache speeds up repeated identical-prompt calls within one server
# process; default ON. Set INVESTIGATOR_DISABLE_CACHE=1 to force every LLM call to
# go to the model -- needed during research runs (e.g. capture_golden) where
# stale cached responses would mask prompt changes we want to validate.
_cache_on = os.environ.get("INVESTIGATOR_DISABLE_CACHE") != "1"
dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=_cache_on)


# --- Small helpers --------------------------------------------------------


def is_json(myjson: str) -> bool:
    try:
        json.loads(myjson)
    except ValueError:
        return False
    return True


# --- DSPy single-call wrappers -------------------------------------------


async def extract_evidences(hypothesis: str, investigation_subject: str, text: str, entities: list[dict]) -> list[Evidence]:
    extract = dspy.asyncify(dspy.Predict(ExtractEvidenceFromJSONText))
    result = await extract(hypothesis=hypothesis, entities=entities, text=text)
    return result.evidences


async def check_investigation_subject(hypothesis: str, investigation_subject: str, text: str, entities: list[dict]) -> list[Evidence]:
    extract = dspy.asyncify(dspy.Predict(InvestigateEvidenceFromJSONText))
    result = await extract(investigation_subject=investigation_subject, entities=entities, text=text)
    return result.evidences


async def edges_enrichment(entities: list[tuple], context: str) -> list[Edge]:
    predictor = dspy.asyncify(dspy.Predict(GraphEdgesEnrichment))
    result = await predictor(nodes_pairs=entities, context=context)
    return result.edges


async def get_entities(input_data: str, investigation_query: str = "") -> tuple[list[Entity], list]:
    extract = dspy.Predict(NamedEntitiesRecognition)
    with dspy.context(lm=extraction_lm):
        extract = dspy.asyncify(extract)
    result = await extract(source_text=input_data, investigation_query=investigation_query)
    return result.entities, result.affiliations


async def get_events(input_data: str, investigation_query: str = "") -> list:
    """Run the Event NER signature on a chunk; returns the list of Event
    records the LLM extracted. Mirrors `get_entities` so the orchestrator
    can fire both in parallel (asyncio.gather) per chunk.
    """
    extract = dspy.Predict(EventsRecognition)
    with dspy.context(lm=extraction_lm):
        extract = dspy.asyncify(extract)
    result = await extract(source_text=input_data, investigation_query=investigation_query)
    return result.events


async def get_causal_claims(input_data: str, investigation_query: str = "") -> list:
    """Run the CausalClaimsExtraction signature on a chunk; returns the
    list of CausalClaim records the LLM extracted. Per Pearl's ladder of
    causation, these are Level-1 evidence -- claims the SOURCE TEXT makes
    about causation, NOT claims the system endorses. The downstream graph
    represents them as `claimed_caused_by` edges with explicit weight,
    strength, confidence, attestation count, and source URLs so the
    analyst can rank and verify.
    """
    extract = dspy.Predict(CausalClaimsExtraction)
    with dspy.context(lm=extraction_lm):
        extract = dspy.asyncify(extract)
    result = await extract(source_text=input_data, investigation_query=investigation_query)
    return result.claims


async def extract_query_subject(query: str) -> str:
    """NER on the query: distil the free-text investigation query down to the
    single subject entity name (the root of the triangulation)."""
    extract = dspy.asyncify(dspy.Predict(ExtractInvestigationSubject))
    result = await extract(query=query)
    return (result.subject or "").strip()


async def get_representative_identifiers(identifiers: list[str]) -> list[str]:
    extract = dspy.asyncify(dspy.Predict(MostRepresentativeIdentifier))
    with dspy.context(lm=dspy.LM("openai/gpt-4.1", temperature=0.0, max_tokens=32000)):
        result = await extract(identifiers=identifiers)
    return result.representative_identifiers


# --- Per-chunk extraction -------------------------------------------------


async def extract_evidence_from_chunk(json_chunk, hypothesis, investigation_subject, identifiers, task_id=None):
    return await extract_evidences(hypothesis=hypothesis, investigation_subject=investigation_subject, text=json_chunk, entities=identifiers)


async def investigate_evidence_from_chunk(json_chunk, hypothesis, investigation_subject, identifiers, task_id=None):
    return await check_investigation_subject(hypothesis=hypothesis, investigation_subject=investigation_subject, text=json_chunk, entities=identifiers)


async def extract_entities_from_chunk(json_chunk, original_chunk, working_state, task_id=None, investigation_query=""):
    chunks_dicts = []
    chunk_dict: dict = {}
    entities_group: list = []
    random_uuid = uuid.uuid4()
    chunk_to_entities_map: list = []
    chunk_to_location_map: list = []
    affiliation_dicts: list = []
    all_entities_dicts: list = []
    entities_group_by_chunk: list = []
    chunk_id = str(random_uuid)
    json_chunk = preprocess_text(json_chunk)
    try:
        # Parallel: NER (entities + affiliations), Event NER, and Causal-
        # Claims extraction. All three are independent over the same chunk;
        # running them in parallel keeps per-chunk latency near
        # max(NER, EventNER, CausalClaims) instead of the sum. Each is
        # tolerated as a failure: missing extractions just contribute
        # nothing to the merged graph.
        ner_task = get_entities(json_chunk, investigation_query=investigation_query)
        event_task = get_events(json_chunk, investigation_query=investigation_query)
        claims_task = get_causal_claims(json_chunk, investigation_query=investigation_query)
        (entities, affiliations), events, claims = await asyncio.gather(
            ner_task, event_task, claims_task,
        )
        for entity in entities:
            if entity.name is None or entity.name in ("", "None", "N/A"):
                continue
            if entity.name.upper() not in chunk_to_entities_map:
                chunk_to_entities_map.append(entity.name.upper())
            entity_dict: dict = {}
            entity_dict["identifier"] = entity.name.upper()
            entity_dict["unique_identifier"] = str(uuid.uuid4())
            entity_dict["type"] = "entity"
            entity_dict["data"] = entity.model_dump()
            entity_dict["chunk_uuid"] = chunk_id
            if entity.search_url:
                entity_dict["source"] = entity.search_url
            elif entity.search_source:
                entity_dict["source"] = entity.search_source
            elif entity_dict.get("data", {}).get("search_url"):
                entity_dict["source"] = entity_dict.get("data", {}).get("search_url")
            else:
                entity_dict["source"] = entity_dict.get("data", {}).get("search_source", "unknown")
            if "relevant_entities" in entity_dict["data"]:
                if entity.name.upper() not in entity_dict["data"]["relevant_entities"]:
                    entity_dict["data"]["relevant_entities"].append(entity.name.upper())
            else:
                entity_dict["data"]["relevant_entities"] = list(dict.fromkeys(chunk_to_entities_map))

            if entity_dict not in all_entities_dicts:
                entities_group.append(entity_dict)
                all_entities_dicts.append(entity_dict)
        for affiliation in affiliations:
            affiliation = affiliation.model_dump()
            affiliation["chunk_id"] = chunk_id
            affiliation_dicts.append(affiliation)

        # Event-NER results: each Event becomes a graph node with type="event"
        # whose `identifier` is the upper-cased event name and `data` carries
        # the full Event pydantic dump (date, location, event_type,
        # participants, description, confidence, source_url). Participants
        # are kept on the event's data for now -- participant -> event edges
        # are synthesised in a later step (Phase 2b).
        #
        # Events are deliberately NOT added to chunk_to_entities_map: that
        # list flows into get_representative_identifiers_task, which asks
        # the LLM to canonicalise entity names. The LLM has been observed
        # to map a long event-name like "ISRAELI STRIKE KILLS HAMAS LEADER
        # IZZ AL-DIN AL-HADDAD" to the same representative as the entity
        # "IZZ AL-DIN AL-HADDAD" -- then SemHash dedup clusters them and
        # the resulting record is type=entity with event-data fields mixed
        # in. Keeping events out of that pool preserves the type partition.
        for event in events:
            if not event.name or event.name in ("", "None", "N/A"):
                continue
            event_id = event.name.upper()
            event_dict = {
                "identifier": event_id,
                "unique_identifier": str(uuid.uuid4()),
                "type": "event",
                "data": event.model_dump(),
                "chunk_uuid": chunk_id,
                "source": event.source_url or "unknown",
            }
            entities_group.append(event_dict)
            all_entities_dicts.append(event_dict)

        chunk_dict["entities"] = list(dict.fromkeys(chunk_to_entities_map))
        chunk_dict["locations"] = list(dict.fromkeys(chunk_to_location_map))
        chunk_dict["affiliations"] = affiliation_dicts
        chunk_dict["chunk_text"] = json_chunk
        # Carry causal claims with the chunk so the downstream aggregation
        # step (in _standard_pipeline) can group them across chunks by
        # (cause_id, effect_id), resolve to existing nodes, and emit
        # weighted claimed_caused_by edges.
        chunk_dict["causal_claims"] = [c.model_dump() for c in (claims or [])]
        chunk_dict["query"] = original_chunk["query"] if "query" in original_chunk.keys() else f"chunk_{task_id}"
        chunk_dict["search"] = original_chunk["search"] if "search" in original_chunk.keys() else None

        url_value = find_value_in_nested_dict(original_chunk, "url")
        if url_value is not None:
            chunk_dict["url"] = url_value
        published_date = find_value_in_nested_dict(original_chunk, "published_date")
        if published_date is not None:
            chunk_dict["published_date"] = published_date
        chunk_dict["uuid"] = str(random_uuid)
        chunks_dicts.append(chunk_dict)
        entities_group_by_chunk.append({"chunk_uuid": chunk_id, "entities": entities_group})

        if "chunks" not in working_state:
            working_state["chunks"] = chunks_dicts
        else:
            for cd in chunks_dicts:
                working_state["chunks"].append(cd)

        dirty_node_names = [ent["identifier"] for ent in all_entities_dicts]
        working_state["nodes"] = working_state.get("nodes", []) + all_entities_dicts
        if "dirty_node_names" not in working_state:
            working_state["dirty_node_names"] = [list(dict.fromkeys(dirty_node_names))]
        else:
            working_state["dirty_node_names"].append(list(dict.fromkeys(dirty_node_names)))
    except Exception as e:
        log.error(f"Error during entity extraction from chunk: {e}")
        return [], [], []
    return entities_group_by_chunk, all_entities_dicts, affiliation_dicts


# --- Task-level fan-out helpers ------------------------------------------


async def get_representative_identifiers_task(identifiers: list[list[str]]) -> list[str]:
    async_tasks: list = []
    representative_identifiers: list = []
    identifiers_flat = [item for sublist in identifiers for item in sublist]
    identifiers_flat = list(dict.fromkeys(identifiers_flat))
    identifiers_flat = sorted(identifiers_flat)
    grouped = cluster_identifiers(identifiers_flat)

    for batch in grouped:
        if len(batch) > 1:
            async_tasks.append(get_representative_identifiers(batch))
        else:
            log.debug("Single identifier in group, adding directly to representative identifiers")
            if len(batch) == 1 and batch[0] != "":
                if batch[0] not in representative_identifiers:
                    representative_identifiers.append({"identifier": batch[0], "relevant_identifiers": [batch[0]]})
    processed_data = await asyncio.gather(*async_tasks)
    for task_results in processed_data:
        for rep_id in task_results:
            if rep_id not in representative_identifiers:
                representative_identifiers.append(rep_id.model_dump())
    return representative_identifiers


async def named_entities_extractor_task(investigation_id: str, text: str, working_state, investigation_query: str = ""):
    all_entities_dicts: list = []
    entities_group_by_chunk: list = []
    affiliation_dicts: list = []
    is_json_input = is_json(text)
    json_chunks: list = []
    dict_chunks: list = []
    async_tasks: list = []
    if is_json_input:
        chunks_ = json.loads(text)
        chunks_ = flatten_and_clean_dict(chunks_)
        if isinstance(chunks_, list):
            json_chunks = chunks_
        if isinstance(chunks_, dict):
            dict_chunks = json_chunker(chunks_, 2048)
            for chunk in dict_chunks:
                preprocessed_chunk = preprocess_text(json.dumps(chunk))
                json_chunks.append(preprocessed_chunk)
    else:
        text_chunks = text_chunker(text, chunk_size=4000)
        for text_chunk in text_chunks:
            dict_chunks.append({"text": text_chunk})
            json_chunks.append(json.dumps({"text": text_chunk}))

    log.info(f"Number of original chunks: {len(json_chunks)}")
    if len(json_chunks) == 0:
        log.warning("No chunks to process for entity extraction")
        return all_entities_dicts, affiliation_dicts, False
    task_id = 0
    for json_chunk, dict_chunk in zip(json_chunks, dict_chunks):
        async_tasks.append(extract_entities_from_chunk(json_chunk, dict_chunk, working_state, task_id=task_id, investigation_query=investigation_query))
        task_id += 1
    processed_data = await asyncio.gather(*async_tasks)
    for entities_group_by_chunk_, all_entities_dicts_, affiliation_dicts_ in processed_data:
        entities_group_by_chunk = entities_group_by_chunk + entities_group_by_chunk_
        all_entities_dicts = all_entities_dicts + all_entities_dicts_
        affiliation_dicts.append(affiliation_dicts_)
    return all_entities_dicts, affiliation_dicts, True


async def edges_enrichment_task(edges_grouped_by_chunk, working_state, coarse_grained_edges) -> list[Edge]:
    async_tasks: list = []
    edges_enrichment_results: list = []
    for chunk_id in edges_grouped_by_chunk:
        for investigation_chunk in working_state.get("chunks", []):
            if investigation_chunk["uuid"] == chunk_id:
                async_tasks.append(
                    edges_enrichment(entities=edges_grouped_by_chunk[chunk_id], context=investigation_chunk["chunk_text"])
                )
    processed_data = await asyncio.gather(*async_tasks)
    for edges_enrichment_results_ in processed_data:
        edges_enrichment_results = edges_enrichment_results + [ch.model_dump() for ch in edges_enrichment_results_]
    return edges_enrichment_results


async def investigate_evidences_task(working_state, representative_identifiers, hypothesis, investigation_subject, task_id=None):
    async_tasks: list = []
    for investigation_chunk in working_state.get("chunks", []):
        json_chunk = investigation_chunk["chunk_text"]
        identifiers = investigation_chunk.get("entities", [])
        async_tasks.append(investigate_evidence_from_chunk(json_chunk, hypothesis, investigation_subject, identifiers, task_id=task_id))
    processed_data = await asyncio.gather(*async_tasks)
    all_evidences: list = []
    for evidences_ in processed_data:
        chunk_evidences = []
        for evidence in evidences_:
            if evidence.hypothesis:
                chunk_evidences.append(evidence.model_dump())
        all_evidences = all_evidences + chunk_evidences
    for evidence in all_evidences:
        for representative in representative_identifiers:
            if evidence["related_node"] in representative.get("relevant_identifiers", []):
                evidence["related_node"] = representative["identifier"]
                break
    return all_evidences


async def extract_evidence_from_chunk_task(working_state, representative_identifiers, hypothesis, investigation_subject, task_id=None):
    async_tasks: list = []
    for investigation_chunk in working_state.get("chunks", []):
        json_chunk = investigation_chunk["chunk_text"]
        identifiers = investigation_chunk.get("entities", [])
        async_tasks.append(extract_evidence_from_chunk(json_chunk, hypothesis, investigation_subject, identifiers, task_id=task_id))
    processed_data = await asyncio.gather(*async_tasks)
    all_evidences: list = []
    for evidences_ in processed_data:
        chunk_evidences = []
        for evidence in evidences_:
            if evidence.hypothesis:
                chunk_evidences.append(evidence.model_dump())
        all_evidences = all_evidences + chunk_evidences
    for evidence in all_evidences:
        for representative in representative_identifiers:
            if evidence["related_node"] in representative.get("relevant_identifiers", []):
                evidence["related_node"] = representative["identifier"]
                break
    return all_evidences


def source_date_index(working_state) -> dict:
    """Map each source URL to its article's publication date, from the chunks.

    The publication date is the cheapest, most universal time signal we have
    ("observed time" — when a relationship was *asserted*). Threading it through
    on a single url->date map lets any downstream edge resolve its observed time
    by URL, without touching each edge-creation site. Last non-empty date per URL
    wins. Returns ``{url: "YYYY-MM-DD..."}``.
    """
    out: dict = {}
    for chunk in (working_state.get("chunks") or []):
        url = chunk.get("url")
        iso = to_iso_date(chunk.get("published_date"))
        if url and iso:
            out[url] = iso
    return out


async def node_and_evidence_consolidator(working_state, merged_entities, root, representative_identifiers, fully_connected_graph, hypothesis, investigation_subject):
    """Attach extracted evidence to entities and orient it toward ``root``.

    Evidence's ``related_node`` is canonicalized via the representative groups and
    resolved to its owning entity through a precomputed alias index (was two O(N)
    linear scans per evidence). Supporting evidence on an entity that has an
    affiliation path to ``root`` is stamped with that path
    ("Evidence through affiliations A->…->ROOT") as provenance — this is the
    relevance chain to the investigation subject. The path lookup is one BFS from
    root (``single_source_shortest_path``) instead of the old O(top_degrees ×
    evidence) repeated ``shortest_path`` calls. Both polarities are attached so
    the signed ``prob`` (final loop) can net support vs. contradiction.

    Connectivity to root (G8) for evidenced-but-unaffiliated survivors is enforced
    downstream in ``score_graph_by_connectivity`` (evidence edge to root), so this
    no longer mutates the graph.
    """
    chunk_evidences = await extract_evidence_from_chunk_task(working_state, representative_identifiers, hypothesis, investigation_subject, task_id="test_task")
    chunk_investigations = await investigate_evidences_task(working_state, representative_identifiers, hypothesis, investigation_subject, task_id="test_task")
    chunk_evidences = chunk_evidences + chunk_investigations

    # url -> publication date, so each evidence row can carry the "as reported on"
    # date of the article it came from (observed-time signal for the temporal layer).
    source_dates = source_date_index(working_state)

    # alias -> canonical (representative) id; first representative wins
    alias_to_canonical: dict = {}
    for rep in representative_identifiers:
        for alias in rep.get("relevant_identifiers", []):
            alias_to_canonical.setdefault(alias, rep["identifier"])
    # canonical/identifier/label -> owning entity; first entity (in order) wins,
    # matching the original first-break linear scan
    entity_by_key: dict = {}
    for node in merged_entities:
        for key in (node["identifier"], node.get("representative_identifier", ""), *node.get("labels", [])):
            if key:
                entity_by_key.setdefault(key, node)

    # affiliation paths to root in a single BFS (undirected = reachability)
    undirected = fully_connected_graph.to_undirected()
    paths_to_root = nx.single_source_shortest_path(undirected, root) if root is not None and undirected.has_node(root) else {}

    proved_hypothesis_counter = 0
    routed_to_root_counter = 0
    seen_keys: set = set()
    for evidence in chunk_evidences:
        canonical = evidence.get("related_node", None)
        canonical = alias_to_canonical.get(canonical, canonical)
        node = entity_by_key.get(canonical)
        if node is None:
            continue
        is_proved = evidence.get("hypothesis", False)
        kind = "support" if is_proved else "contradict"
        key = f"{canonical}_{evidence.get('metadata', {}).get('source', '')}_{kind}_evidence"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        evidences_list = [f"* {item} \n" for item in evidence.get("evidence", [])]
        reasoning = evidence.get("reasoning", "") + "\n" + "\n".join(evidences_list)
        # Provenance toward root: a supporting entity's chain of affiliations to
        # the investigation subject is why it is relevant. (Path is entity->…->root;
        # entities with no affiliation path to root rely on score_graph's G8 edge.)
        # path is from-root (single_source_shortest_path); reverse so the
        # annotation reads ENTITY->…->ROOT (provenance flowing to the subject).
        path = paths_to_root.get(node["identifier"])
        if is_proved and path and len(path) > 1:
            reasoning = f"Evidence through affiliations {'->'.join(reversed(path))}.\n" + reasoning
            routed_to_root_counter += 1
        source_url = evidence.get("metadata", {}).get("source", "")
        evidence_node = {
            "identifier": key,
            "doc_id": source_url,
            "reasoning": reasoning,
            "evidence": evidence.get("evidence", []),
            "hypothesis": is_proved,
            "strength": evidence.get("strength", 0),
            "confidence": evidence.get("confidence", 0),
            "metadata": evidence.get("metadata", {}),
            "related_node": evidence.get("related_node", None),
            "relations": list(node.get("data", {}).get("relations", [])),
            "published_date": source_dates.get(source_url, ""),
        }
        node.setdefault("evidence", []).append(evidence_node)
        if is_proved:
            proved_hypothesis_counter += 1
            node["self_evidence"] = node.get("self_evidence") or evidence_node

    # Evidence-gated scoring (TRIANGULATION_REVIEW §4): survival = credible
    # evidence. Every evidenced node gets a real prob from the signed,
    # confidence-weighted scorer (works for a single evidence too — no count
    # gate); no-evidence nodes get prob 0 / leaf False and are dropped in
    # score_graph. hypothesis reflects net support (prob >= 0.5).
    for node in merged_entities:
        evidence = node.get("evidence", [])
        if evidence:
            # prob still rewards breadth of attestation (assess_evidence's
            # source boost); claim-level corroboration for the UI is computed as
            # read-time postprocessing (graph.corroboration), not stored here.
            node["prob"] = evidence_probability(evidence)
            node["leaf"] = node["prob"] > 0
            node["hypothesis"] = node["prob"] >= 0.5
        else:
            node["prob"] = 0.0
            node["leaf"] = False

    return merged_entities, routed_to_root_counter, proved_hypothesis_counter, fully_connected_graph


def _name_tokens(name: str | None) -> list[str]:
    """Alphanumeric word tokens, uppercased: 'Globalaid, Inc.' -> ['GLOBALAID', 'INC']."""
    return re.findall(r"[A-Z0-9]+", (name or "").upper())


def _is_token_run(short: list[str], long: list[str]) -> bool:
    """True if `short` appears as a contiguous run of whole tokens inside `long`."""
    n = len(short)
    return n > 0 and any(long[i:i + n] == short for i in range(len(long) - n + 1))


def match_query_to_entity(subject, entities, representatives, graph, *, allow_name_in_query=True):
    """Match a subject name to a canonical entity id, preferring one that is
    actually in the affiliation graph. Returns ``None`` when nothing matches
    (the caller decides the fallback).

    Matching is on whole-word tokens (so "UN" never matches inside "UNITED" and
    "A" never matches inside "EXAMPLEORG"): the subject's tokens are a contiguous
    run inside the entity name's tokens ("Globalaid" -> "GLOBALAID, INC."), or
    vice-versa. The reverse direction (entity tokens inside the subject) is gated
    by ``allow_name_in_query``: safe for a short distilled subject, but unwanted
    for a raw sentence query where a real word ("all", "fund") could be a short
    entity name — so the raw-query fast-path passes ``allow_name_in_query=False``.
    """
    if not subject:
        return None
    q = _name_tokens(subject)
    if not q:
        return None

    def matches(name: str | None) -> bool:
        nu = _name_tokens(name)
        return bool(nu) and (_is_token_run(q, nu) or (allow_name_in_query and _is_token_run(nu, q)))

    candidates: list[str] = []
    for e in entities:
        if any(matches(n) for n in [e.get("identifier"), e.get("representative_identifier"), *e.get("labels", [])]):
            candidates.append((e.get("representative_identifier") or e.get("identifier") or "").upper())
    for r in representatives:
        if any(matches(n) for n in [r.get("identifier"), *r.get("relevant_identifiers", [])]):
            candidates.append((r.get("identifier") or "").upper())

    graph_nodes = set(graph.nodes())
    in_graph = [c for c in candidates if c in graph_nodes]
    if in_graph:
        return in_graph[0]
    return candidates[0] if candidates else None


# --- Top-level orchestrator ----------------------------------------------


class InvestigationPipeline:
    """Per-request entry point. Construct once at app startup, call
    ``await pipeline.run(payload)`` from the Flask route.
    """

    def __init__(
        self,
        state_repo,
        cumulative_kg=None,
        *,
        analytics_enabled: bool = True,
        debug_mode: bool = False,
    ) -> None:
        self.state_repo = state_repo
        self.cumulative_kg = cumulative_kg
        self.analytics_enabled = analytics_enabled
        self.debug_mode = debug_mode

    async def run(self, payload: dict) -> dict:
        """Top-level dispatch: parse + branch + error handling."""
        investigation_id = payload.get("session_id")
        try:
            investigation_query = payload.get("query")
            investigation_subject = payload.get("hypotests")
            domain = payload.get("domain", "general")
            text = payload.get("text")
            # Optional cross-run provenance label. When present, every entity
            # + edge introduced or re-attested by THIS POST gets stamped with
            # `runs=[run]`; the cross-stage merge then unions across POSTs so
            # a long-lived session ends up with per-record runs lists that
            # name every run-label that touched the record. Absent => legacy
            # single-run behaviour (runs field stays None, omitted from
            # response). Named `run` (not `event`) to avoid collision with
            # first-class graph nodes of type="event" that the Event NER may
            # introduce.
            run = payload.get("run")

            if not investigation_id:
                return {"status": "error", "message": "'session_id' is required"}

            return await self._standard_pipeline(
                investigation_id=investigation_id,
                investigation_query=investigation_query,
                investigation_subject=investigation_subject,
                domain=domain,
                text=text,
                run=run,
            )
        except Exception as e:
            if self.debug_mode:
                raise
            # Report the failure honestly. We still return the last-known
            # graph so callers degrade gracefully, but status="error" + the
            # message means a failed run is no longer indistinguishable from
            # a clean one (was the C1 silent-success bug).
            log.error(f"Error during graph nodes extraction: {str(e)}")
            log.error(traceback.format_exc())
            saved_nodes = self.state_repo.get_field(investigation_id, "nodes", [])
            saved_edges = self.state_repo.get_field(investigation_id, "edges", [])
            log.info(
                f"Returning previous graph for session: {investigation_id} after error"
            )
            return {
                "status": "error",
                "message": str(e),
                "session_id": str(investigation_id),
                "nodes": saved_nodes,
                "edges": saved_edges,
            }

    async def _standard_pipeline(
        self,
        *,
        investigation_id: str,
        investigation_query: str,
        investigation_subject: str,
        domain: str,
        text: str,
        run: str | None = None,
    ) -> dict:
        start_time = time.time()
        working_state: dict = {"nodes": [], "edges": []}

        # --- Load or initialize persisted state -----------------------------
        # One InvestigationState replaces the read-only current_* snapshot plus
        # the scattered repo find/add/update/get_field juggling: load once here,
        # save once at the end. working_state stays the per-request
        # working dict (the step fns are threaded onto `state` in later steps).
        existing_record = self.state_repo.find(investigation_id)
        is_first_run = not (existing_record and len(existing_record) > 0)
        state = InvestigationState.load(self.state_repo, investigation_id)
        if is_first_run:
            log.info(f"Starting new investigation session: {investigation_id}")
        else:
            log.info(f"Resuming investigation session: {investigation_id}")

        # --- Prior cross-investigation context (read-only pre-seed) ---------
        # What the cumulative KG already knows about this subject, retrieved
        # BEFORE this run is merged in, so the response/report can reference
        # prior findings. Guarded: never blocks or breaks the run.
        prior_context = await self._kg_prior_context(investigation_subject or investigation_query)

        # --- Preprocess input ----------------------------------------------
        is_json_input = is_json(text)
        if is_json_input:
            log.debug("Input text is JSON, preprocessing")
            text = preprocess_text(text)
        else:
            if is_first_run:
                log.info("Input text is plain text")
                return {"status": "error", "message": "Input text must be in JSON format"}
            text = '{"data": "Previous investigation state, no new text provided."}'

        if not text:
            log.warning("No text provided, returning current state")
            return self._return_current_state(state)

        # --- Step 1: Named entities ----------------------------------------
        log.info("Starting named entities extraction process")
        nodes, affiliations, is_input = await named_entities_extractor_task(
            investigation_id, text, working_state, investigation_query=investigation_query
        )
        if not is_input:
            log.warning("No entities extracted from input, returning current state")
            return self._return_current_state(state)

        # --- Step 2: Representative identifiers ----------------------------
        # NOTE: events are NOT in `dirty_node_names` -- extract_entities_from_chunk
        # only adds entity identifiers to chunk_to_entities_map, so MRI never
        # sees event names. Events stay independent of any canonicalisation
        # step that might over-cluster their (long, descriptive) identifiers.
        # The bypass below only affects ENTITY canonicalisation.
        log.info("Starting representative identifiers extraction process")
        dirty_nodes_names = working_state.get("dirty_node_names", []) + state.dirty_node_names
        state.dirty_node_names = dirty_nodes_names
        if os.environ.get("INVESTIGATOR_SKIP_MRI"):
            # Diagnostic bypass: each entity is its own representative. Used
            # to isolate whether MostRepresentativeIdentifier is collapsing
            # distinct entities under a bad canonical name (the smoke run
            # found Israel + Hamas + Haddad grouped under a headline string).
            flat_names = []
            for sub in dirty_nodes_names:
                if isinstance(sub, list):
                    flat_names.extend(sub)
                elif isinstance(sub, str):
                    flat_names.append(sub)
            flat_names = list(dict.fromkeys(n for n in flat_names if n))
            representative_identifiers = [
                {"identifier": n, "relevant_identifiers": [n]} for n in flat_names
            ]
            log.info(
                f"INVESTIGATOR_SKIP_MRI=1 -- skipping MostRepresentativeIdentifier; "
                f"{len(representative_identifiers)} entities map to themselves"
            )
        else:
            representative_identifiers = await get_representative_identifiers_task(dirty_nodes_names)
        log.debug(f"Representative identifiers: {json.dumps(representative_identifiers, indent=4)}")
        working_state["representative_identifiers"] = representative_identifiers

        # --- Step 3: Duplicate detection -----------------------------------
        # Partition by type before SemHash dedup. Events go through with
        # exact-id-only collapsing (within-chunk dups merged via dict-key
        # uniqueness during extraction). The entity dedup pool must NOT
        # see events -- otherwise a long event-name token-overlaps a
        # related entity (e.g. "ISRAELI STRIKE KILLS HAMAS LEADER X" vs
        # "X"), they cluster, and the resulting record is half-event
        # half-entity with type=entity. Strategy C: cross-event dedup of
        # events is deferred to a follow-up pass informed by observed
        # paraphrase patterns.
        log.info("Starting duplicate detection and merging process")
        entity_nodes = [n for n in nodes if n.get("type") != "event"]
        event_nodes = [n for n in nodes if n.get("type") == "event"]
        merged_entities, _all_identifiers, _dedup_records = deduplicate_entities(
            entity_nodes, representative_identifiers, semhash_model=semhash_model
        )
        # Post-hoc canonical validation: when NER or MRI admits a headline-
        # shaped string as an entity, SemHash may promote it as the cluster
        # representative -- so the merged record's `identifier` ends up as
        # e.g. "BOEING FORCED TO DISASSEMBLE...", with the real actors
        # ("Boeing", "FAA") buried in `labels`. Swap any such headline-
        # identifier with the shortest valid label on the same record so
        # the canonical name is a noun phrase. Old identifier becomes a
        # label for provenance.
        n_fixed = validate_entity_canonicals(merged_entities)
        if n_fixed:
            log.info(f"Post-hoc canonical-validation rewrote {n_fixed} headline-shaped entity identifiers")
        # Event paraphrase-dedup: collapse event-records that refer to the
        # same real-world incident under (event_type, date +/- 7 days,
        # participant Jaccard >= 0.5). The first occurrence wins as
        # canonical; later paraphrases merge their descriptions /
        # source_urls / participants / dates onto it and their identifier
        # becomes a label. Strategy C calibrated this rule against the
        # big-run observation (paraphrases of the May-16 Haddad strike all
        # agreed on those three fields).
        deduplicated_events = dedup_events_by_signature(event_nodes)
        # Stamp representative_identifier on events (mirrors what
        # deduplicate_entities does for entity records at the start of its
        # loop). Downstream `build_graph` reads this field on every record.
        for ev_rec in deduplicated_events:
            ev_rec["representative_identifier"] = ev_rec["identifier"].upper()
        log.info(
            f"Type partition: {len(entity_nodes)} entity-type, {len(event_nodes)} event-type "
            f"({len(deduplicated_events)} events after exact-id collapse)"
        )
        merged_entities = merged_entities + deduplicated_events
        if not merged_entities:
            log.warning("No deduplicated nodes found after duplicate detection, returning current state")
            return self._return_current_state(state)

        # Cross-run provenance: stamp every entity surfaced by THIS POST
        # with its run label. The cross-stage merge later unions this with
        # any pre-existing runs on a matched saved record.
        if run:
            for n in merged_entities:
                existing = n.get("runs") or []
                if run not in existing:
                    n["runs"] = existing + [run]

        n_dedup = len(merged_entities)

        # --- Step 4: Graph building ----------------------------------------
        log.info("Starting graph building process")
        coarse_grained_edges, _most_connected_node, top_degrees, _lowest_degrees, fully_connected_graph = build_graph(
            merged_entities, working_state, representative_identifiers
        )

        # --- Step 5: Edges enrichment --------------------------------------
        if coarse_grained_edges and len(coarse_grained_edges) > 0:
            log.info(f"Coarse grained edges extracted: {len(coarse_grained_edges)}")
            edges_grouped_by_chunk = group_edges_by_chunk(coarse_grained_edges)
            log.info("Starting edges enrichment process")
            edges_enrichment_results = await edges_enrichment_task(
                edges_grouped_by_chunk, working_state, coarse_grained_edges
            )
            merged_entities = attach_relations_to_nodes(merged_entities, edges_enrichment_results)
            edges_enrichment_results = resolve_edge_endpoints(
                merged_entities, edges_enrichment_results
            )
            # Cross-run provenance: stamp every enriched edge surfaced by
            # THIS POST. Matches the entity-side stamp above; the cross-stage
            # merge unions on (src,dst) pair-match.
            if run:
                for e_ in edges_enrichment_results:
                    existing = e_.get("runs") or []
                    if run not in existing:
                        e_["runs"] = existing + [run]
        else:
            edges_enrichment_results = []
            log.info("No coarse grained edges extracted, skipping edges enrichment process")

        # --- Step 5b: Event-participant edge synthesis ---------------------
        # For each event-record's participants list, look up the matching
        # entity in merged_entities (case-insensitive on identifier / labels /
        # representative_identifier) and synthesise a participates_in edge
        # event -> participant. Edges are already endpoint-resolved (we read
        # both UUIDs from the merged_entities records), so they bypass the
        # GraphEdgesEnrichment LLM call and flow directly into the response.
        # Participants the NER didn't extract are dropped (no dangling
        # endpoints).
        entity_lookup: dict = {}
        for n in merged_entities:
            if n.get("type") == "event":
                continue
            for k in {
                (n.get("identifier") or "").upper(),
                (n.get("representative_identifier") or "").upper(),
                *((lab or "").upper() for lab in (n.get("labels") or [])),
            }:
                if k:
                    entity_lookup.setdefault(k, n)
        participant_edges: list = []
        seen_participant_pairs: set = set()
        n_events = 0
        for ev_record in merged_entities:
            if ev_record.get("type") != "event":
                continue
            n_events += 1
            event_id = ev_record["identifier"]
            event_uid = ev_record.get("unique_identifier", "")
            event_src = ev_record.get("source", "") or ""
            # Within-run dedup may have concatenated participant lists from
            # multiple attestations into one list with repeats; dedup by
            # (name) within this event so we don't fan out duplicate edges.
            participants_raw = (ev_record.get("data") or {}).get("participants") or []
            seen_names: set = set()
            for p in participants_raw:
                if not isinstance(p, dict):
                    continue
                pname = (p.get("name") or "").strip()
                prole = (p.get("role") or "").strip()
                if not pname or pname.upper() in seen_names:
                    continue
                seen_names.add(pname.upper())
                target = entity_lookup.get(pname.upper())
                if target is None:
                    continue
                # Self-loop guard: when the NER mis-extracts the event's own
                # headline as an entity (long descriptive phrase mistaken for
                # an ORG), the entity_lookup may resolve participant `name`
                # to that same-identifier record via labels, producing a
                # self-edge event -> event. Skip such resolutions.
                if target["identifier"] == event_id:
                    continue
                pair_key = (event_id, target["identifier"])
                if pair_key in seen_participant_pairs:
                    continue
                seen_participant_pairs.add(pair_key)
                participant_edges.append({
                    "unique_identifier": str(uuid.uuid4()),
                    "src_identifier": event_id,
                    "dst_identifier": target["identifier"],
                    "src_unique_identifier": event_uid,
                    "dst_unique_identifier": target.get("unique_identifier", ""),
                    "type": "event_participation",
                    "relations": json.dumps({"type": "participates_in", "context": prole}),
                    "attributes": {},
                    "source": event_src,
                    "search_url": event_src,
                })
        if participant_edges:
            if run:
                for pe in participant_edges:
                    pe["runs"] = [run]
            edges_enrichment_results.extend(participant_edges)
            log.info(
                f"Synthesised {len(participant_edges)} participant edges from {n_events} event records"
            )

        # --- Step 5c: Programmatic event-event temporal/coincident edges -
        # For each pair of event records: if they share at least one
        # participant AND their dates are within 60 days, emit either an
        # `event_followed_by` (directed, earlier->later) or
        # `event_coincident` (within 3 days) edge. No LLM call; the signal
        # is entirely from data already attested per event.
        event_records = [n for n in merged_entities if n.get("type") == "event"]
        if event_records:
            temporal_edges = infer_event_temporal_edges(event_records)
            if temporal_edges and run:
                for te in temporal_edges:
                    te["runs"] = [run]
            edges_enrichment_results.extend(temporal_edges)
            log.info(
                f"Synthesised {len(temporal_edges)} event-event temporal edges from "
                f"{len(event_records)} event records"
            )

        # --- Step 5d: Source-claimed causation edges (Level-1 evidence) ---
        # Walk every chunk's causal_claims, resolve each (cause, effect)
        # pair to existing merged-entities by case-insensitive name lookup
        # (over identifier + labels + representative_identifier), aggregate
        # claims per resolved pair, and emit `claimed_caused_by` edges with
        # an explicit aggregate weight. Claims whose endpoints don't
        # resolve to existing nodes are dropped (no dangling endpoints).
        causal_edges = self._synthesise_causal_claim_edges(
            working_state.get("chunks", []), merged_entities, run=run
        )
        if causal_edges:
            edges_enrichment_results.extend(causal_edges)
            log.info(
                f"Synthesised {len(causal_edges)} source-claimed causation edges"
            )

        merged_entities = remove_empty_fields(merged_entities)
        for node in merged_entities:
            node["identifier"] = node.get("representative_identifier", node["identifier"])
        n_enriched = len(edges_enrichment_results)

        # --- Step 6: Evidence mapping + triangulation ----------------------
        hypothesis = return_hypothesis_for_domain(domain)
        investigator = fully_connected_graph.copy(as_view=False)

        # Triangulation root = the investigation subject (hops-to-root is the
        # relevance metric, and the consolidator orients evidence provenance +
        # connectivity toward it). Resolved BEFORE consolidation so it can be
        # threaded through. Fast-path: query may already be a clean entity name
        # (skip the LLM). Otherwise run NER on the query to distil the subject.
        # Fall back to the most-connected node only when the subject is absent.
        root = match_query_to_entity(
            investigation_query, merged_entities, representative_identifiers, investigator,
            allow_name_in_query=False,
        )
        if not root and investigation_query:
            try:
                subject = await extract_query_subject(investigation_query)
            except Exception as e:  # noqa: BLE001
                subject = None
                log.warning(f"Query-subject extraction failed ({type(e).__name__}); using most-connected root")
            if subject:
                root = match_query_to_entity(subject, merged_entities, representative_identifiers, investigator)
                if root:
                    log.info(f"Triangulation root via query-NER: '{investigation_query}' -> subject '{subject}' -> '{root}'")
        if root:
            log.info(f"Triangulation root = '{root}' (investigation subject)")
        else:
            root = top_degrees[0] if top_degrees else None
            log.warning(
                f"Investigation subject from query '{investigation_query}' NOT found among extracted "
                f"entities; falling back to most-connected node '{root}' as root (subject may be absent from the data)"
            )

        # Optional TMFG construction (Phase-1 research prototype). Builds a
        # chordal+planar maximally-filtered graph from `investigator`, decomposing
        # it into (p-3) tetrahedra glued by triangular separators -- the cliques
        # are candidate investigation themes. Opt-in via INVESTIGATOR_TMFG=1; does
        # not replace `investigator` or affect the standard pipeline. The result is
        # held for Phase-2 belief propagation (which fires after the consolidator
        # has computed per-entity `prob`).
        tmfg_result = None
        if os.environ.get("INVESTIGATOR_TMFG"):
            tmfg_result = construct_tmfg(investigator)
            log.info(
                f"TMFG: {investigator.number_of_edges()} -> {tmfg_result.graph.number_of_edges()} edges "
                f"({len(tmfg_result.tetrahedra)} tetrahedra, {len(tmfg_result.fill_in_edges)} hypothesis fill-ins)"
            )
            ranked = sorted(
                ((i, m, tetrahedron_weight(tmfg_result.graph, m))
                 for i, m in enumerate(tmfg_result.tetrahedra)),
                key=lambda t: -t[2],
            )
            for idx, members, w in ranked[:5]:
                log.info(f"  TMFG theme [{idx}] w={w:.2f}: {sorted(members)}")

        # Optional corroboration filter (Direction-1 research prototype). Drops
        # affiliation edges attested by fewer than INVESTIGATOR_EDGE_FILTER_MIN
        # chunks (default 2), preserving every node's path to root via
        # below-threshold bridges. Opt-in via env flag; not on by default.
        if os.environ.get("INVESTIGATOR_EDGE_FILTER"):
            min_count = int(os.environ.get("INVESTIGATOR_EDGE_FILTER_MIN", "2"))
            before = investigator.number_of_edges()
            investigator = filter_by_corroboration(investigator, root, min_count=min_count)
            log.info(
                f"Corroboration filter: {before} -> {investigator.number_of_edges()} edges "
                f"(min_count={min_count}, root={root})"
            )

        log.info("Starting node and evidence consolidation process")
        merged_entities, _mec, _phc, fully_connected_graph = await node_and_evidence_consolidator(
            working_state,
            merged_entities,
            root,
            representative_identifiers,
            fully_connected_graph,
            hypothesis,
            investigation_subject,
        )
        # Research mode: INVESTIGATOR_BP_KEEP_DROPPED keeps low-prob-evidenced
        # entities through score_graph so Phase-2 BP can see the "cleared" pole
        # (entities with evidence but prob <= 0 are normally dropped). Off by
        # default; production drops them.
        merged_entities, edges_enrichment_results, _final_identifiers = score_graph_by_connectivity(
            investigator=investigator,
            edges_enrichment_results=edges_enrichment_results,
            merged_entities=merged_entities,
            root=root,
            keep_low_prob_evidenced=bool(os.environ.get("INVESTIGATOR_BP_KEEP_DROPPED")),
        )

        # Phase-2: junction-tree belief propagation over the TMFG. Runs only if
        # TMFG was built (INVESTIGATOR_TMFG=1). Fuses each entity's evidence prior
        # with affiliation-weighted Ising coupling across clique-mates.
        if tmfg_result is not None:
            priors = {n["identifier"]: float(n.get("prob") or 0.5) for n in merged_entities}
            try:
                bp_result = junction_tree_propagate(tmfg_result, priors, beta=1.0)
                for n in merged_entities:
                    ident = n["identifier"]
                    n["posterior_prob"] = bp_result.posterior.get(ident, n.get("prob", 0.5))
                    n["posterior_delta"] = bp_result.delta.get(ident, 0.0)
                moved = sum(1 for v in bp_result.delta.values() if abs(v) > 0.01)
                log.info(
                    f"Phase-2 BP: {moved}/{len(bp_result.delta)} entities moved >0.01 (beta=1.0)"
                )
                self._render_phase2_visualizations(
                    investigation_id, tmfg_result, bp_result, merged_entities, edges_enrichment_results,
                    investigation_query,
                )
            except Exception as e:  # noqa: BLE001 -- BP must never break a run
                log.warning(f"Phase-2 BP failed: {type(e).__name__}: {e}")

        await self._merge_into_cumulative_kg(
            merged_entities, edges_enrichment_results, f"inv::{run or investigation_id}",
            source_dates=source_date_index(working_state),
        )
        self._render_debug_graph(investigation_id, merged_entities, edges_enrichment_results, investigation_query)

        evidence_edges = sum(1 for e in edges_enrichment_results if e.get("type") == "evidence")
        log.info(
            f"Pipeline funnel: {len(nodes)} extracted entities ({len(affiliations)} chunks) "
            f"-> {n_dedup} deduplicated -> {n_enriched} enriched edges "
            f"-> triangulated to {len(merged_entities)} entities / {len(edges_enrichment_results)} edges "
            f"({evidence_edges} of them evidence edges to root)"
        )
        log.info(f"Time taken for graph generation: {time.time() - start_time:.1f}s")

        # --- Merge with saved state ----------------------------------------
        # state.nodes holds EntityRecords; merge_run_into_saved still works on dicts, so
        # convert out here and back to records when we re-assign (step 4 boundary;
        # the dicts round-trip losslessly).
        saved_nodes = [n.to_dict() for n in state.nodes]
        saved_edges = [e.to_dict() for e in state.edges]
        edges_enrichment_results, merged_entities, saved_edges, saved_nodes = merge_run_into_saved(
            edges_enrichment_results, merged_entities, saved_edges, saved_nodes
        )

        # --- Step 7: Persist final graph state -----------------------------
        run_number = state.runs_number
        state.nodes = [EntityRecord.from_dict(n) for n in saved_nodes + merged_entities]
        state.reindex()
        state.edges = [EdgeRecord.from_dict(e) for e in saved_edges + edges_enrichment_results]
        state.representative_identifiers = working_state["representative_identifiers"]
        state.runs_number = run_number + 1
        state.investigation_query = investigation_query
        state.investigation_subject = investigation_subject
        state.save(self.state_repo)

        # XXX(Phase 2): legacy CSV append-on-every-request to /tmp; remove
        # once dashboards / proper logging are in place.
        self._append_csv_audit(investigation_id, merged_entities, working_state)

        log.info(f"Graph state saved for session: {investigation_id}")
        log.info(f"Total RUNS for session: {investigation_id} is {run_number + 1}")
        log.info(
            f"Returning total of {len(saved_nodes + merged_entities)} nodes and {len(saved_edges + edges_enrichment_results)} edges for session: {investigation_id}"
        )
        response_nodes = [n.to_dict() for n in state.nodes]
        response_edges = [e.to_dict() for e in state.edges]

        # Phase 3: compute the network-analysis payload over the FINAL merged
        # state -- so themes / promoted_entities / hypothesis_edges reference
        # the same entity set the client sees in `nodes`. Opt-in via the same
        # INVESTIGATOR_TMFG flag that gates Phase 1 + Phase 2. Backwards-compatible:
        # when the flag is off, the response is the plain {nodes, edges} the
        # investigator app has consumed all along.
        network_analysis: dict = {}
        if os.environ.get("INVESTIGATOR_TMFG"):
            try:
                network_analysis = self._build_network_analysis(response_nodes, response_edges)
            except Exception as e:  # noqa: BLE001 -- never break the response
                log.warning(f"[Phase 3] network-analysis payload failed: {type(e).__name__}: {e}")

        return {
            "status": "success",
            "session_id": str(investigation_id),
            "nodes": response_nodes,
            "edges": response_edges,
            **({"prior_context": prior_context} if prior_context else {}),
            **network_analysis,
        }

    # --- Small helpers used only by the standard pipeline ----------------

    def _build_network_analysis(self, response_nodes: list, response_edges: list) -> dict:
        """Run TMFG + BP over the response's (merged) graph and produce the
        network-analysis sections of the response.

        Operates on dicts (the response shape) and mutates them in place to add
        per-entity ``posterior_prob`` / ``posterior_delta`` / ``themes`` and
        per-edge ``is_hypothesis``. Returns the three top-level sections.
        """
        # Evidence-aware edge weights for the TMFG.
        #
        # Historically every edge weighed 1.0 (0.5 for root-wiring), so theme
        # formation + theme weight were pure topology -- the densest 4-clique
        # won, regardless of evidence. Two refinements, validated by an offline
        # A/B over real runs:
        #
        #   1. Type-aware base. Attested actor relationships (affiliation,
        #      claimed_caused_by) weigh most; event->participant edges less;
        #      event<->event temporal edges least. Without this, clusters of
        #      near-duplicate event headlines (linked by event_followed_by /
        #      event_coincident) dominate the top themes -- pushing them from
        #      ~50% to ~88% event-members and burying the actor cliques.
        #   2. Endpoint evidence x run corroboration. Each node's `prob` is the
        #      signed, confidence-weighted evidence score; a multi-run edge
        #      (attested in 2+ investigations) gets a corroboration bump. The
        #      merged-graph edges carry no per-edge source_count, so endpoint
        #      evidence + run-count are the available corroboration signals.
        #
        # Net effect: themes rank by well-corroborated ACTOR structure rather
        # than raw connectivity (top-10 themes go to ~70% entity-members and
        # surface clean actor cliques). Set INVESTIGATOR_TMFG_UNIFORM_WEIGHTS=1 to
        # restore the old topology-only behaviour.
        evidence_aware = os.environ.get("INVESTIGATOR_TMFG_UNIFORM_WEIGHTS") != "1"
        node_prob = {n["identifier"]: float(n.get("prob") or 0.5) for n in response_nodes}
        _TYPE_BASE = {
            "affiliation": 1.0,
            "claimed_caused_by": 1.0,
            "event_participation": 0.5,
            "event_followed_by": 0.15,
            "event_coincident": 0.15,
            "evidence": 0.3,   # synthetic root-wiring
        }

        def _edge_weight(e: dict) -> float:
            etype = e.get("type")
            if etype == "evidence":
                return 0.3
            if not evidence_aware:
                return 1.0
            base = _TYPE_BASE.get(etype, 0.5)
            s, t = e.get("src_identifier"), e.get("dst_identifier")
            ps = max(node_prob.get(s, 0.5), 0.0)
            pt = max(node_prob.get(t, 0.5), 0.0)
            ev = (ps * pt) ** 0.5   # geom-mean of endpoint evidence
            n_runs = len(e.get("runs") or [])
            corroboration = 1.0 + 0.25 * max(0, n_runs - 1)
            return max(0.05, base * ev * corroboration)

        g = nx.Graph()
        for n in response_nodes:
            g.add_node(n["identifier"])
        for e in response_edges:
            s, t = e.get("src_identifier"), e.get("dst_identifier")
            if not (s and t):
                continue
            w = _edge_weight(e)
            if g.has_edge(s, t):
                g[s][t]["weight"] = max(g[s][t].get("weight", 0.0), w)
            else:
                g.add_edge(s, t, weight=w)

        if g.number_of_nodes() < 4:
            return {}

        tmfg = construct_tmfg(g)
        priors = {n["identifier"]: float(n.get("prob") or 0.5) for n in response_nodes}
        bp = junction_tree_propagate(tmfg, priors, beta=1.0)

        # Update per-node fields on the response (overwrites the per-stage
        # values set by Phase 2, since this merged BP is the authoritative one).
        for n in response_nodes:
            ident = n["identifier"]
            n["posterior_prob"] = round(bp.posterior.get(ident, n.get("prob", 0.5)), 6)
            n["posterior_delta"] = round(bp.delta.get(ident, 0.0), 6)

        payload = build_network_analysis_payload(tmfg, bp, response_nodes, response_edges)
        log.info(
            f"[Phase 3] network_analysis: {len(payload['themes'])} themes, "
            f"{len(payload['promoted_entities'])} promoted entities, "
            f"{len(payload['hypothesis_edges'])} hypothesis edges"
        )
        return payload

    def _synthesise_causal_claim_edges(
        self, chunks: list, merged_entities: list, *, run: str | None = None,
    ) -> list:
        """Aggregate per-chunk causal claims into weighted `claimed_caused_by`
        edges. Resolves cause / effect names to existing entity / event
        identifiers via the same label-aware lookup used for participant
        edges; pairs with unresolved endpoints are dropped silently.

        Aggregation per resolved (cause_id, effect_id) pair:
            strength          = max strength across attesting claims
            confidence        = max confidence across attesting claims
            attestation_count = distinct source-URL count
            source_urls       = union of source URLs
            hedging_tags      = distinct list of hedging tags seen
            claim_texts       = up to 3 distinct paraphrases for analyst review
            weight            = strength x confidence x multi_source_boost
                                where multi_source_boost = min(2.0, 1 + 0.3*(n-1))

        Output edge shape mirrors the other edges in edges_enrichment_results
        so the downstream merge / response / cross-event analytics treat them
        consistently. The new `attributes` dict surfaces the weighting
        components so an analyst (or downstream client) can filter by them.
        """
        import json as _json
        import uuid as _uuid

        # Build label-aware lookup from merged_entities
        lookup: dict = {}
        for n in merged_entities:
            keys = {
                (n.get("identifier") or "").upper(),
                (n.get("representative_identifier") or "").upper(),
                *((lab or "").upper() for lab in (n.get("labels") or [])),
            }
            for k in keys:
                if k:
                    lookup.setdefault(k, n)

        # Group all claims by (cause_id, effect_id)
        per_pair: dict = {}
        for chunk in chunks:
            for c in (chunk.get("causal_claims") or []):
                cause_name = (c.get("cause") or "").strip()
                effect_name = (c.get("effect") or "").strip()
                if not cause_name or not effect_name:
                    continue
                cause_node = lookup.get(cause_name.upper())
                effect_node = lookup.get(effect_name.upper())
                if cause_node is None or effect_node is None:
                    continue
                if cause_node["identifier"] == effect_node["identifier"]:
                    continue
                pair_key = (cause_node["identifier"], effect_node["identifier"])
                per_pair.setdefault(pair_key, {
                    "cause_node": cause_node, "effect_node": effect_node, "claims": [],
                })["claims"].append(c)

        edges = []
        for pair_key, info in per_pair.items():
            claims = info["claims"]
            cause_node, effect_node = info["cause_node"], info["effect_node"]
            strengths = [float(c.get("strength") or 0) for c in claims]
            confs = [float(c.get("confidence") or 0) for c in claims]
            srcs = list(dict.fromkeys(c.get("source_url") or "" for c in claims if c.get("source_url")))
            hedging = list(dict.fromkeys(c.get("hedging") or "" for c in claims if c.get("hedging")))
            directions = list(dict.fromkeys(c.get("direction") or "" for c in claims if c.get("direction")))
            claim_texts = list(dict.fromkeys((c.get("claim_text") or "").strip() for c in claims))
            claim_texts = [t for t in claim_texts if t][:3]
            max_strength = max(strengths) if strengths else 0.0
            max_conf = max(confs) if confs else 0.0
            n_sources = max(len(srcs), 1)
            multi_boost = min(2.0, 1.0 + 0.3 * (n_sources - 1))
            weight = round(max_strength * max_conf * multi_boost, 4)

            edges.append({
                "unique_identifier": str(_uuid.uuid4()),
                "src_identifier": pair_key[0],
                "dst_identifier": pair_key[1],
                "src_unique_identifier": cause_node.get("unique_identifier", ""),
                "dst_unique_identifier": effect_node.get("unique_identifier", ""),
                "type": "claimed_caused_by",
                "relations": _json.dumps({
                    "type": "claimed_caused_by",
                    "context": claim_texts[0] if claim_texts else "",
                }),
                "attributes": {
                    "weight": weight,
                    "strength": round(max_strength, 4),
                    "confidence": round(max_conf, 4),
                    "attestation_count": n_sources,
                    "source_urls": srcs,
                    "hedging_tags": hedging,
                    "directions": directions,
                    "claim_texts": claim_texts,
                },
                "source": "causal_claims_extraction",
                "search_url": srcs[0] if srcs else "",
                "runs": [run] if run else None,
            })
        # Remove None runs for legacy compat
        for e in edges:
            if e.get("runs") is None:
                e.pop("runs", None)
        # Sort by weight desc so downstream consumers see ranked claims
        edges.sort(key=lambda e: -float(e.get("attributes", {}).get("weight") or 0))
        return edges

    def _return_current_state(self, state: InvestigationState) -> dict:
        return {
            "status": "success",
            "session_id": str(state.session_id),
            "nodes": [n.to_dict() for n in state.nodes],
            "edges": [e.to_dict() for e in state.edges],
        }

    async def _kg_prior_context(self, subject: str | None, top_k: int = 30) -> dict | None:
        """Retrieve what the cumulative KG already knows about ``subject``.

        Uses the structured (no-LLM-synthesis) retrieval endpoint in ``hybrid``
        mode and returns trimmed entities + relationships. Returns None when the
        KG is disabled/empty or on any error -- this is a best-effort pre-seed,
        never a hard dependency.
        """
        if not self.analytics_enabled or self.cumulative_kg is None or not subject:
            return None
        try:
            data = await self.cumulative_kg.retrieve(subject, mode="hybrid")
            d = (data or {}).get("data") or {}
            entities = [
                {"name": e.get("entity_name"), "type": e.get("entity_type"),
                 "description": e.get("description")}
                for e in (d.get("entities") or [])[:top_k]
            ]
            relationships = [
                {"src": r.get("src_id"), "dst": r.get("tgt_id"),
                 "description": r.get("description"), "weight": r.get("weight")}
                for r in (d.get("relationships") or [])[:top_k]
            ]
            if not entities and not relationships:
                return None
            log.info(
                f"Cumulative KG prior context for {subject!r}: "
                f"{len(entities)} entities, {len(relationships)} relationships"
            )
            return {"subject": subject, "entities": entities, "relationships": relationships}
        except Exception as e:  # noqa: BLE001 -- prior context is best-effort
            log.warning(f"Cumulative KG prior-context retrieval failed for {subject!r}: {type(e).__name__}: {e}")
            return None

    async def _merge_into_cumulative_kg(
        self, entities: list[dict], edges: list[dict], source_id: str,
        source_dates: dict | None = None,
    ) -> None:
        """Accumulate this run's graph into the persistent cross-investigation KG.

        The CanonicalRegistry pre-pass + LightRAG merge-by-name happen in-process
        (no server). Guarded so a KG failure never breaks the investigation.
        """
        if not self.analytics_enabled or self.cumulative_kg is None:
            return
        try:
            summary = await self.cumulative_kg.merge_graph(
                {"nodes": entities, "edges": edges}, source_id=source_id,
                source_dates=source_dates,
            )
            log.info(f"Cumulative KG updated from {source_id}: {summary}")
        except Exception as e:  # noqa: BLE001 -- KG accumulation must never break a run
            log.warning(f"Cumulative KG merge failed for {source_id}: {type(e).__name__}: {e}")

    def _render_phase2_visualizations(self, investigation_id, tmfg_result, bp_result,
                                       entities, edges, query) -> None:
        """Three viz HTMLs for Phase-2 belief propagation:
          * <id>_phase2_posterior.html -- entity graph coloured by posterior P(implicated)
          * <id>_phase2_delta.html    -- entity graph coloured by delta (post - prior)
          * <id>_phase2_clique_tree.html -- the clique tree (tetrahedra as nodes,
                                            separators as edges), coloured by mean posterior
        Skipped (with a warning) if rendering fails -- never breaks a run.
        """
        if not (self.debug_mode or os.environ.get("INVESTIGATOR_VIZ")):
            return
        try:
            from investigator.graph import _delta_color, _posterior_color, visualize_clique_tree
            viz_dir = os.environ.get("INVESTIGATOR_VIZ_DIR") or os.path.join(os.getcwd(), "debug_output", "viz")
            os.makedirs(viz_dir, exist_ok=True)

            def _build_entity_graph(color_fn, key):
                g = nx.DiGraph()
                for node in entities:
                    ident = node["identifier"]
                    posterior = float(node.get("posterior_prob", node.get("prob", 0.5)))
                    delta = float(node.get("posterior_delta", 0.0))
                    prior = float(node.get("prob", 0.5))
                    color = color_fn(posterior if key == "posterior" else delta)
                    g.add_node(
                        ident,
                        title=(
                            f"<b>{ident}</b><br>"
                            f"prior (evidence): {prior:.3f}<br>"
                            f"<b>posterior (BP): {posterior:.3f}</b><br>"
                            f"delta: {delta:+.3f}<br>"
                            f"score: {round(node.get('score', 0), 3)}"
                        ),
                        value=node.get("score", 0) or 0.05,
                        color=color,
                    )
                for edge in edges:
                    src, dst = edge.get("src_identifier"), edge.get("dst_identifier")
                    if src and dst:
                        is_ev = edge.get("type") == "evidence"
                        g.add_edge(src, dst, title=edge.get("type", ""),
                                   color="#f39c12" if is_ev else "#95a5a6")
                return g

            # 1) Posterior viz
            g_post = _build_entity_graph(_posterior_color, "posterior")
            p1 = os.path.join(viz_dir, f"{investigation_id}_phase2_posterior.html")
            visualize_graph(g_post, p1, title=f"{query or 'investigation'} — Phase 2 posterior (red=implicated · green=cleared)")

            # 2) Delta viz
            g_delta = _build_entity_graph(_delta_color, "delta")
            p2 = os.path.join(viz_dir, f"{investigation_id}_phase2_delta.html")
            visualize_graph(g_delta, p2, title=f"{query or 'investigation'} — Phase 2 delta (blue=raised · red=lowered)")

            # 3) Clique-tree viz
            posterior_map = {n["identifier"]: float(n.get("posterior_prob", n.get("prob", 0.5))) for n in entities}
            p3 = os.path.join(viz_dir, f"{investigation_id}_phase2_clique_tree.html")
            visualize_clique_tree(tmfg_result, p3, posterior=posterior_map,
                                  title=f"{query or 'investigation'} — TMFG clique tree, {len(tmfg_result.tetrahedra)} tetrahedra")

            log.info(
                f"[Phase 2] viz: posterior={p1}; delta={p2}; clique_tree={p3}"
            )
        except Exception as e:  # noqa: BLE001
            log.warning(f"[Phase 2] viz failed: {type(e).__name__}: {e}")

    def _render_debug_graph(self, investigation_id, entities, edges, query) -> None:
        """Write an interactive HTML view of the triangulated graph for debugging.

        Enabled by ``debug_mode`` or the ``INVESTIGATOR_VIZ`` env var. Nodes are sized
        by ``score`` and coloured by evidence (leaf); ``evidence`` edges to root are
        highlighted. Off by default — never runs in a normal request.
        """
        if not (self.debug_mode or os.environ.get("INVESTIGATOR_VIZ")):
            return
        try:
            graph = nx.DiGraph()
            for node in entities:
                ident = node["identifier"]
                data = node.get("data", {})
                graph.add_node(
                    ident,
                    title=f"score={round(node.get('score', 0), 3)} "
                    f"prob={round(node.get('prob', 0), 3)} "
                    f"relevance={round(data.get('relevance_score', 0), 3)}",
                    value=node.get("score", 0),
                    color="#e74c3c" if node.get("leaf") else "#3498db",
                )
            for edge in edges:
                src, dst = edge.get("src_identifier"), edge.get("dst_identifier")
                if src and dst:
                    is_evidence = edge.get("type") == "evidence"
                    graph.add_edge(src, dst, title=edge.get("type", ""), color="#f39c12" if is_evidence else "#95a5a6")
            viz_dir = os.environ.get("INVESTIGATOR_VIZ_DIR") or os.path.join(os.getcwd(), "debug_output", "viz")
            out_path = os.path.join(viz_dir, f"{investigation_id}.html")
            visualize_graph(graph, out_path, title=f"{query or 'investigation'} — {len(entities)} entities")
            log.info(f"[debug] triangulated-graph visualization written to {out_path}")
        except Exception as e:  # noqa: BLE001 — visualization must never break a run
            log.warning(f"[debug] graph visualization failed: {e}")

    @staticmethod
    def _append_csv_audit(investigation_id: str, nodes: list[dict], working_state: dict) -> None:
        import csv

        with open("/tmp/graph_nodes_log.csv", mode="a") as f:
            writer = csv.writer(f)
            for node in nodes:
                for chunk in working_state.get("chunks", []):
                    if chunk["uuid"] == node.get("chunk_uuid", ""):
                        writer.writerow(
                            [
                                investigation_id,
                                node.get("identifier", ""),
                                chunk.get("source", ""),
                                chunk.get("chunk_text", "").replace("\n", " ")[:1000],
                            ]
                        )
