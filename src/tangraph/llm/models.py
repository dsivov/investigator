"""Pydantic data models for entities, edges, evidence, and related LLM I/O."""


from pydantic import BaseModel


class TimelineEvent(BaseModel):
    date: str
    event: str


class Entity(BaseModel):
    name: str
    type: str
    location: str
    address: str
    email: str
    phone_number: str
    position: str
    timeline_events: list[TimelineEvent]
    financial_restrictions: str
    relevance_score: float
    search_source: str
    search_url: str


class Affiliation(BaseModel):
    entityA: str
    entityB: str
    affiliation_type: str
    # Per-affiliation strength/confidence (parallel to Evidence). Used by the
    # TMFG research branch as the edge weight for clique construction.
    # Default 0.0 keeps the field optional for prompts that don't yet emit it.
    strength: float = 0.0       # [0,1] — how strong/conclusive the relation, per the source text
    confidence: float = 0.0     # [0,1] — how confident you are it is correctly read from the text


class Metadata(BaseModel):
    source: str
    doc_metadata: dict
    related_links: list[str]


class Relation(BaseModel):
    # `name` was removed (PR4-b): the LLM used it as a duplicate of Edge.target_node,
    # and drifted on ~15% of edges (role text, summary sentences, the source
    # entity, or an uncanonicalized alias). Edge.target_node is the canonical
    # destination; role/position/relation summaries belong in `context`;
    # structured details belong in Edge.attributes.
    type: str
    context: str


class Edge(BaseModel):
    source_node: str
    target_node: str
    relations: Relation
    attributes: dict
    # confidence: float
    # strength: float
    # metadata: Metadata
    source: str
    search_url: str = ""   # PR4-a: URL provenance home; empty when no URL in source text


class Evidence(BaseModel):
    related_node: str
    evidence: list[str]
    confidence: float       # how confident the evidence is correctly read from the text [0,1]
    strength: float         # magnitude of the evidence [0,1] — signed by `hypothesis`
    metadata: Metadata
    reasoning: str
    hypothesis: bool        # True = supports / False = contradicts


class Identifier(BaseModel):
    identifier: str
    relevant_identifiers: list[str]


class EventParticipant(BaseModel):
    """A named participant in an event with an optional role label.

    `name` should match an entity the NER also extracts so the orchestrator
    can wire a participates_in edge from the event-node to the entity-node.
    `role` is the part the participant played in this specific event
    (e.g. "perpetrator", "target", "issuer", "subject", "victim"). Empty
    string when the source text doesn't make the role explicit.
    """
    name: str
    role: str = ""


class CausalClaim(BaseModel):
    """A causal assertion the SOURCE TEXT makes between two actors / events.

    This is NOT a causal claim the system endorses -- it is a structured
    capture of what the article asserts. The analyst uses it as Level-1
    evidence (per Pearl's ladder of causation): a claim worth examining,
    not an established fact.

    Strict-absence rule applies: when a field is not stated in the source
    text, return the empty string "" (or 0.0 for floats). Do not invent.
    """
    cause: str                      # actor or event name asserted as the cause
    effect: str                     # actor or event name asserted as the effect
    direction: str                  # "causes" | "responds_to" | "triggers" | "results_in" | "unclear"
    hedging: str                    # "explicit" | "likely" | "speculative" | "weak"
    claim_text: str                 # 1-sentence paraphrase of the source's assertion
    strength: float = 0.0           # [0,1] -- explicitness of the assertion in the text
    confidence: float = 0.0         # [0,1] -- LLM's confidence in the extraction
    source_url: str = ""            # URL of the article making this claim


class Event(BaseModel):
    """A real-world incident or action surfaced from the source text.

    Events become first-class graph nodes with type="event" (parallel to
    type="entity" persons / orgs). They participate in TMFG, BP, and
    cross-event analytics the same way entities do; the only structural
    difference is the edges that connect them (participates_in to people /
    orgs, optionally follows / responds_to to other events).

    Field-by-field strict-absence rule (same as Entity): when a field's
    value is not explicitly stated in the source text, the value MUST be
    the empty string "" (or 0.0 for floats; empty list for lists). Do not
    invent / paraphrase / write "Not specified" prose.
    """
    name: str                           # human-readable event label as written in source
    date: str                           # ISO-8601 (YYYY-MM-DD) when the source gives one; else "" if absent
    location: str                       # where it happened; "" if not stated
    event_type: str                     # one of: military_action, sanctions, indictment, diplomatic, corporate_action, legislative, other
    participants: list[EventParticipant]
    description: str                    # 1-2 sentence summary of what happened
    confidence: float = 0.0             # [0,1] how confident the event is correctly read from the text
    source_url: str = ""                # URL provenance; "" if the chunk doesn't carry it
