"""DSPy signatures: LLM prompts as typed input/output contracts.

Prompt text in docstrings is the working version from the research codebase.
Treat changes here as prompt-engineering decisions, not refactor mechanics.
"""


import dspy

from tangraph.llm.models import Affiliation, CausalClaim, Edge, Entity, Event, Evidence, Identifier


class ExtractEvidenceFromJSONText(dspy.Signature):
    """
    Find evidence in the input text that SUPPORTS or CONTRADICTS the hypothesis,
    for each entity. Extract BOTH kinds — do not omit exonerating / contradicting
    evidence.

    For each evidence set:
    - 'hypothesis': true if the evidence SUPPORTS the hypothesis, false if it
      CONTRADICTS / exonerates the entity.
    - 'strength' (0 to 1): how strong / conclusive the evidence is — the
      magnitude. The supports/contradicts polarity comes from 'hypothesis'; do
      not encode a sign here.
    - 'confidence' (0 to 1): how confident you are it is correctly read from the text.
    - 'reasoning': why the evidence supports or contradicts.
    - 'related_node': the entity name the evidence concerns.
    - 'metadata.source': REQUIRED — the document / source identifier where the
      evidence is attested (a URL, report name, file path, or other locator),
      verbatim from the source text. This is the provenance the investigation
      report cites; never leave it empty when ANY source identifier is available.
    - 'metadata.related_links': list of URLs from the source text directly
      related to this evidence (use [] when none).
    - 'metadata.doc_metadata': any document-level details (reliability notes,
      dates, limitations); use {} when none.

    Ground every claim strictly in the provided text; do not infer beyond it.
    Return a list of evidences mapped to the entity identifier.
    """

    hypothesis: str = dspy.InputField()
    text: str = dspy.InputField()
    entities: list[str] = dspy.InputField()
    evidences: list[Evidence] = dspy.OutputField(desc="Supporting and contradicting evidence, mapped to entity identifier")


class InvestigateEvidenceFromJSONText(dspy.Signature):
    """
    Find evidence in the input text that SUPPORTS or CONTRADICTS the investigation
    subject, for each entity. Extract BOTH kinds — do not omit exonerating /
    contradicting evidence.

    For each evidence set:
    - 'hypothesis': true if the evidence SUPPORTS the investigation subject, false
      if it CONTRADICTS / exonerates the entity.
    - 'strength' (0 to 1): how strong / conclusive the evidence is — the
      magnitude. The supports/contradicts polarity comes from 'hypothesis'; do
      not encode a sign here.
    - 'confidence' (0 to 1): how confident you are it is correctly read from the text.
    - 'reasoning': why the evidence supports or contradicts.
    - 'related_node': the entity name the evidence concerns.
    - 'metadata.source': REQUIRED — the document / source identifier where the
      evidence is attested (a URL, report name, file path, or other locator),
      verbatim from the source text. This is the provenance the investigation
      report cites; never leave it empty when ANY source identifier is available.
    - 'metadata.related_links': list of URLs from the source text directly
      related to this evidence (use [] when none).
    - 'metadata.doc_metadata': any document-level details (reliability notes,
      dates, limitations); use {} when none.

    Ground every claim strictly in the provided text; do not infer beyond it.
    Return a list of evidences mapped to the entity identifier.
    """

    investigation_subject: str = dspy.InputField()
    text: str = dspy.InputField()
    entities: list[str] = dspy.InputField()
    evidences: list[Evidence] = dspy.OutputField(desc="Supporting and contradicting evidence, mapped to entity identifier")


class GraphEdgesEnrichment(dspy.Signature):
    """
    Task: Given pairs of Nodes (source_node, target_node), extract supporting
    evidence about the relationship between them and emit one Edge per input pair.

    For each pair, fill the Edge fields exactly as follows:
      - source_node, target_node: copied from the input pair (the source and
        destination entities). DO NOT restate the destination entity name
        anywhere else in the Edge; target_node already carries it.
      - source: the document or source identifier where the relationship is
        attested, when available (verbatim from the text).
      - search_url: a URL from the source text where the relationship is
        attested, when one is given (empty string if no URL is present). Use
        this field for URLs — do NOT bury them inside `attributes`.
      - Relation has two fields:
          * type: the relation category — one of "affiliation", "partnership",
            "ownership", "non_direct", "donation", "grant_recipient",
            "fiscal sponsorship", "leadership", "financial support", "employment",
            "contractor", or "unknown" when nothing fits.
          * context: a short narrative describing the relationship, including
            roles or positions (e.g. "Executive Director", "West Coast Coordinator"),
            time period, location, and any explanatory text. THIS is the home
            for role/position information — not the destination entity name.
      - attributes: a dict of any additional structured details — role, position,
        amount, dates, location, source_url, source_reliability, etc. Use
        descriptive keys; prefer attributes over context for anything that is
        not a free-form sentence.

    Ground every claim strictly in the provided text; do not infer beyond it.
    Output exactly one Edge per input pair (the count of returned Edges must
    equal the count of input pairs); if the text yields no usable information
    about a pair, emit an Edge with type="unknown" and an empty context.
    """

    # context: str = dspy.InputField(desc="Investigation summary providing context for edge creation")
    nodes_pairs: list[tuple] = dspy.InputField()
    context: str = dspy.InputField(desc="Investigation summary providing context for edge creation")
    edges: list[Edge] = dspy.OutputField(desc="Relation Edges between Nodes")


class NamedEntitiesRecognition(dspy.Signature):
    """
    Task: Perform Named Entity Recognition (NER) and identify explicitly stated relationships between entities, strictly within the scope of the investigation query.

    Extract NAMED entities of types ORG, PERSON, LOC, and GPE exclusively from the source text. Do not extract any other entity types. Anonymous references or implicit/unnamed mentions must not be extracted.

    Entity Relevance Scoring:
    Measure an entity relevance score for each extracted entity in relation to the investigation query, based solely on information explicitly present in the provided context. Do not use LLM internal knowledge, only provided context.

    Score meanings:
    - 1.00: Strongly relevant to the investigation query
    - 0.80: Highly relevant to the investigation query
    - 0.50: Moderately relevant to the investigation query
    - 0.30: Somewhat relevant to the investigation query
    - 0.10: Slightly relevant to the investigation query
    - 0.00: Neutral or strongly irrelevant to the investigation query

    Entity Types and Definitions:
    - PERSON: Extract the full name of individuals, including middle names,
      suffixes, and any directly associated relevant details available in
      the text.
    - ORG: Extract the full name of organizations of the following
      categories only: commercial organizations, financial organizations,
      non-profit organizations, political movements, radical groups,
      companies, schools, or institutions.
    - GPE (Geopolitical entity): named places that have a government,
      sovereignty, or citizens -- countries, states/provinces, capital
      and major cities, dependent administrative regions, supranational
      bodies. Examples: `Iran`, `Israel`, `United States`, `Gaza`,
      `Tehran`, `West Bank`, `European Union`. Use GPE (not ORG) when a
      country / city / region is mentioned as a political actor or
      jurisdiction. Use GPE (not LOC) when sovereignty / citizenship is
      what matters.
    - LOC (Location): named PHYSICAL or GEOGRAPHIC places without a
      distinct political identity -- seas, straits, rivers, mountain
      ranges, named regions defined by geography rather than polity.
      Examples: `Red Sea`, `Strait of Hormuz`, `Gulf of Aden`,
      `Mediterranean Sea`, `Sahel`. Use LOC (not GPE) when the name is
      a venue or geographic feature rather than a political actor.

    Ambiguous cases: prefer GPE for any name that the text uses as a
    political actor or jurisdiction; prefer LOC for any name the text
    uses as a physical setting only.

    Decomposition rule (applies to ALL FOUR types):
    A valid PERSON / ORG / GPE / LOC name is a NOUN PHRASE identifying
    a single actor or place. It is NOT a sentence, news headline, or
    event description.

    Bad (do NOT extract these as single entities):
      "Israeli strike kills Hamas leader Izz al-Din al-Haddad"
      "US Treasury sanctions Gaza flotilla organizers"
      "Houthi attacks on Red Sea shipping"

    Good (decompose into constituent PERSON / ORG / GPE / LOC entities):
      First example  -> `Israel` (GPE), `Hamas` (ORG),
                        `Izz al-Din al-Haddad` (PERSON).
      Second example -> `US Treasury` (ORG), `Gaza` (GPE),
                        `Gaza flotilla organizers` (ORG).
      Third example  -> `Houthi` (ORG), `Red Sea` (LOC).

    Signals that a candidate string is a sentence/description rather
    than a name:
      - contains a finite verb in past or present tense (kills, killed,
        sanctions, sanctioned, designates, designated, strikes, struck,
        indicts, indicted, attacks, attacked, announces, announced, ...)
      - describes an action one party did to another
      - reads as a complete clause or news headline

    Reject such strings as entities. The entities they reference are the
    SUBJECTS and OBJECTS of those verbs; extract those individually.
    Multi-word proper nouns that are themselves names of a single actor /
    place remain valid -- they contain no finite verbs and identify a
    single thing. Examples:
      - ORG: "Helping Hand for Relief and Development",
             "Council on American-Islamic Relations",
             "Washington Institute for Near East Policy"
      - GPE: "United Arab Emirates", "Republic of South Sudan",
             "West Bank", "Gaza Strip"
      - LOC: "Strait of Hormuz", "Gulf of Aden", "Eastern Mediterranean"
      - PERSON: "Mohammed bin Salman", "Recep Tayyip Erdogan"

    JSON wrapping (do NOT extract container keys as entities):
    The source_text is frequently a JSON envelope of the form
        { "<investigation_query>": { "<record_key>": {
            "query": "<...>", "title": "<...>", "publisher": "<...>",
            "url": "<...>", "published_date": "<...>", "text": "<body>"
        }}}
    The OUTER key, the per-record `query` value, and the `title` value
    are routing/metadata strings -- they often describe the article
    itself ("X kills Y", "Z sanctions W"). Treat these strings as
    CONTEXT, not as entity names. Do NOT extract them. Apply the same
    Decomposition rule above: pull out the named PERSON / ORG / GPE /
    LOC entities that appear INSIDE those strings, never the strings
    themselves.

    Source of truth for entities is the article BODY (the `text`
    field's prose content). Use the title / query strings only to bias
    your relevance scoring, not as candidate entity names.

    Extraction Requirements:
    Required fields (always fill):
      - name: the entity's full name as written in the source.
      - type: one of PERSON, ORG, GPE, LOC (per the definitions above).
      - relevance_score: a number 0.00–1.00 per the scale above.
      - timeline_events: list of temporal events present in the text
        (use an empty list when none are described). For GPE/LOC this
        list is typically empty -- places do not have personal
        chronologies; reserve timeline_events for what the source
        actually attributes to the entity in time.

    Optional fields (extract verbatim from the source when explicitly
    present; otherwise emit an empty string ""):
      - location, address, email, phone_number, position,
        financial_restrictions -- these apply primarily to PERSON and
        ORG. For GPE / LOC they are almost always "" (a country's
        "location" is itself; a strait does not have an address).
        Do NOT invent values just because the entity is a place.
      - search_source, search_url — the source identifier / URL where
        this entity is attested (applies to all four types when the
        text gives them).

    Absence rule (STRICT): when an optional field's value is not stated in the
    source text, the field's value MUST be the empty string "". Do not invent
    values. Do not write any natural-language note about absence. Forbidden
    values for an absent optional field include — but are not limited to —
    "Not found", "Not specified", "Not specified in provided data", "Not
    available", "Not provided", "N/A", "NA", "None", "Null", "Unknown",
    "Not applicable", "-", "—", or any prose describing why the value is
    missing. Use "" — nothing else.

    Do NOT extract anonymous references or implicit/unnamed mentions.

    Relations:
    Identify and classify relations between the extracted entities using only
    the evidence contained in the provided context. Relations may connect
    any pair of extracted types -- PERSON-ORG, ORG-GPE, GPE-LOC, etc. --
    when the source attests a meaningful connection. For each relation,
    emit an Affiliation record with these fields:
      - entityA, entityB: the two related entities (canonical names).
      - affiliation_type: one of "affiliation", "partnership", "ownership",
        "non_direct". For relations involving GPE or LOC (e.g. "Hamas
        operates in Gaza", "Iran is the patron of Hezbollah", "Houthis
        launched attacks from Yemen"), use "non_direct" with a descriptive
        context unless an "ownership" / "affiliation" / "partnership"
        framing is explicit in the text.
      - strength (0.00–1.00): how strong / conclusive the relation is in the
        source text. 1.0 = explicitly stated with concrete details (named role,
        dated transaction, signed agreement). 0.5 = clearly implied but not
        spelled out. 0.1 = a single passing mention. 0.0 = relation is named
        but the supporting detail is absent.
      - confidence (0.00–1.00): how confident you are the relation is correctly
        read from the text (vs. plausibly mis-extracted from ambiguous phrasing).
    These two fields are required and must be grounded in the source text;
    do not infer them from prior knowledge.

    Accuracy:
    Be thorough: extract every named PERSON / ORG / GPE / LOC the source
    mentions that meets the type definitions above. Ground every claim
    strictly in the provided text; never use prior knowledge.

    """

    source_text: str = dspy.InputField()
    investigation_query: str = dspy.InputField(desc="The investigation query providing relevance for entity extraction.")
    entities: list[Entity] = dspy.OutputField(desc="THOROUGH list of named entities of type PERSON, ORG, GPE, and LOC extracted from the text")
    affiliations: list[Affiliation] = dspy.OutputField(desc="Affiliations list")


class EventsRecognition(dspy.Signature):
    """
    Extract real-world incidents / actions described in the source text. An
    Event is a concrete thing that happened at a specific time: a military
    strike, a sanctions designation, an indictment, a treaty signing, a
    corporate divestment, a legislative vote. It is NOT a topic, a policy
    label, a long-running situation, or a generic news category.

    For each event, extract:

    Required fields (always fill):
      - name: a concise human-readable label as written / paraphrased from
        the source. Example: "Israeli strike kills Hamas leader Izz al-Din
        al-Haddad", "US Treasury sanctions Gaza flotilla organizers".
      - event_type: exactly one of:
          military_action   (strikes, raids, attacks, kinetic operations)
          sanctions         (asset freezes, terror designations, OFAC
                             listings, export controls)
          indictment        (criminal charges, arrests, extraditions,
                             court rulings)
          diplomatic        (summits, treaties, statements, expulsions)
          corporate_action  (M&A, divestments, layoffs, audits, IPOs)
          legislative       (laws passed, votes, regulatory rule-making)
          other             (anything that clearly is an event but does
                             not fit the categories above)
      - description: a 1-2 sentence factual summary in the source's voice.
      - confidence: 0.0-1.0 -- how confident the event is correctly read
        from the text. 1.0 = explicit, dated, named participants. 0.5 =
        described but key fields missing. 0.1 = passing mention.
      - participants: list of participants. Each carries:
          - name: canonical name of an entity the NER will also extract.
            MUST match the entity's name as it appears in the text so the
            participates_in edge can be wired downstream.
          - role: the part this participant played in THIS event, in 1-3
            words. "perpetrator", "target", "victim", "issuer", "subject",
            "defendant", "plaintiff", "sponsor", etc. Empty string if the
            source does not make the role explicit.

    Optional fields (extract verbatim from the source when explicitly
    present; otherwise emit empty string ""):
      - date: ISO-8601 (YYYY-MM-DD) when the source gives a specific date.
        If only a month or month-year is given, use YYYY-MM. If only a
        year is given, use YYYY. "" if no date is stated.
      - location: where the event happened (city, country, or named site).
        "" if not stated.
      - source_url: the source URL where this event is attested. "" when
        the chunk does not carry a URL.

    Absence rule (STRICT): use "" for missing optional string fields;
    0.0 for missing confidence; empty list for missing participants.
    Forbidden placeholder values for optional fields include but are not
    limited to "Not found", "Not specified", "Not available", "Not
    provided", "N/A", "NA", "None", "Null", "Unknown", "-", "—", "TBD",
    or any prose describing the absence. Use "" -- nothing else.

    What is NOT an event (do NOT extract these):
      - A topic or policy area ("Iran's nuclear program", "drug trafficking")
      - A long-running situation ("the war in Ukraine", "Hamas's terror
        financing network") without a specific incident
      - An entity's general activity ("Hamas operates in Gaza")
      - Editorial framing or opinion ("the controversial designation")
      - Article publication metadata ("Published 2026-05-12") -- that is
        about the article, not an event the article describes

    Discipline: ground every event in a specific incident attested in the
    source text. Never use prior knowledge to fill date / location /
    participants. If the article does not name the event explicitly,
    paraphrase the headline+lede into a name; do not invent.
    """

    source_text: str = dspy.InputField()
    investigation_query: str = dspy.InputField(desc="The investigation query providing relevance for event extraction.")
    events: list[Event] = dspy.OutputField(desc="List of real-world incidents described in the source text.")


class CausalClaimsExtraction(dspy.Signature):
    """
    Extract CAUSAL ASSERTIONS the source text makes between actors / events.

    A causal claim is a statement in which the SOURCE ASSERTS that one
    entity or event caused, triggered, resulted in, or responded to
    another. This is NOT for you to reason about causation -- it is for
    capturing what the article CLAIMS, with the article's own hedging.

    Examples that ARE causal claims:
      - "Israel killed Haddad to retaliate for the October 7 attacks"
          -> cause: "October 7 attacks", effect: "Israel killed Haddad",
             direction: "triggers", hedging: "explicit", strength: 0.8
      - "The sanctions came in response to Hamas's financing activity"
          -> cause: "Hamas financing activity", effect: "US sanctions",
             direction: "responds_to", hedging: "explicit", strength: 0.85
      - "Analysts say the appointment was likely triggered by the strike"
          -> cause: "Israeli strike", effect: "Mohammed Ouda appointment",
             direction: "triggers", hedging: "likely", strength: 0.55
      - "Some observers speculate that the attack may have prompted ..."
          -> hedging: "speculative", strength: 0.30

    Examples that are NOT causal claims (do NOT extract these):
      - Mere temporal sequence ("after the strike, X happened" with NO
        assertion of causation) -- pure timeline is not a claim.
      - The reporter's narrative framing without a causal verb.
      - Your own causal inference -- only extract what the SOURCE asserts.
      - Hypotheticals or counterfactual framing ("if X had not happened").

    Hedging tag definitions:
      - "explicit": source uses direct causal language ("caused", "led
        to", "in response to", "triggered by"); strength typically 0.7-1.0.
      - "likely": source uses qualified causal language ("likely",
        "probably", "appears to have"); strength typically 0.45-0.7.
      - "speculative": source attributes the claim to analysts /
        observers / unnamed sources, or uses "may have" / "could have";
        strength typically 0.2-0.45.
      - "weak": passing mention, single qualifier, or claim deeply
        embedded in a hedge; strength 0.0-0.2.

    For each claim, also output:
      - cause: the actor or event name as it appears in the text. Should
        match an entity / event the other extractors will surface; the
        downstream merge will only emit edges when both cause and effect
        resolve to existing graph nodes.
      - effect: same, for the effect side.
      - direction: one of {causes, responds_to, triggers, results_in,
        unclear}. Pick the verb that best fits how the source phrases it.
      - claim_text: one-sentence paraphrase of what the source asserts.
        Used for analyst review.
      - confidence: 0.0-1.0 -- how confident YOU are that you correctly
        identified the causal claim (vs. mis-reading prose as a claim).
      - source_url: the article URL if the chunk's JSON path carries one.

    Strict absence: when a field is not stated, emit "" (or 0.0 for
    floats). No "Not specified" prose.
    """

    source_text: str = dspy.InputField()
    investigation_query: str = dspy.InputField(desc="The investigation query providing topical context.")
    claims: list[CausalClaim] = dspy.OutputField(desc="List of causal claims asserted in the source text.")


class ExtractInvestigationSubject(dspy.Signature):
    """
    Identify the single primary SUBJECT entity that an investigation query
    is about. The subject can be any of the four named-entity types:
    PERSON, ORG, GPE (country / city / region), or LOC (physical place).

    The query is a short natural-language instruction, e.g.
      "Find all financial connections for Globalaid"  (ORG subject)
      "Investigate Acme Corp's ties to sanctioned groups"  (ORG)
      "Iran's proxy network across Hezbollah, Houthis, and Hamas"  (GPE)
      "Strait of Hormuz shipping disruptions"  (LOC)
      "Mohammed bin Salman foreign policy"  (PERSON)
    Return the name of the one entity the investigation centers on -- the
    entity whose relationships are being investigated -- exactly as
    written in the query, with no surrounding verbs or descriptors.

    Return an empty string if the query names no concrete PERSON / ORG /
    GPE / LOC entity (e.g., a generic topic like "terror financing 2026"
    has no single subject entity).
    """

    query: str = dspy.InputField(desc="The free-text investigation query/question.")
    subject: str = dspy.OutputField(desc="The single primary PERSON/ORG/GPE/LOC the investigation is about, verbatim from the query; empty string if none.")


class MostRepresentativeIdentifier(dspy.Signature):
    """
    Task: Deduplicate identifiers by finding the most representative or significant one.
    Objective:
    Analyze a list of identifiers to determine the most representative identifiers among them.
    Group similar or duplicate identifiers together, and select the most informative identifier from each group.
    Consider informativity: The most complete, informative, significant and descriptive identifier.
    Ensure all input identifiers are analyzed and appropriately deduplicated and grouped.
    """

    identifiers: list[str] = dspy.InputField(desc="A list of identifiers to analyze.")
    representative_identifiers: list[Identifier] = dspy.OutputField(desc="List of identifiers, grouped with their most representative identifier.")
