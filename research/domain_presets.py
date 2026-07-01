"""Domain-specific preset bundles for investigation runs.

The pipeline is largely domain-agnostic -- the NER, Event NER, causal-claim,
and edges-enrichment signatures all work for any topic. The three things
that DO need to change per investigation domain are the hypothesis text
(what evidence is scored against), the relevance threshold (how strictly
the per-evidence relevance gate filters), and an optional categorical
domain label.

Each preset bundles those three knobs. Callers can either pass --domain
to pick a preset, or override individual fields with --hypothesis /
--relevance-threshold.

Add a new domain: drop a new entry in PRESETS with the three fields and
a short `description`. Keep hypotheses focused on one question -- the
LLM scores evidence against the exact text.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DomainPreset:
    hypothesis: str
    relevance_threshold: float
    description: str


PRESETS: dict[str, DomainPreset] = {
    "terror_financing": DomainPreset(
        hypothesis=(
            "Does the entity maintain relationships or conduct activities "
            "with entities, individuals, or groups that are designated, "
            "suspected of, or known to be affiliated with terrorism, where "
            "such relationships or activities meet the threshold of material "
            "support for terrorism?"
        ),
        relevance_threshold=0.6,
        description="Material support for designated terror groups",
    ),
    "corporate_misconduct": DomainPreset(
        hypothesis=(
            "Does the entity show patterns of regulatory violations, "
            "financial misconduct, executive malfeasance, or systematic "
            "safety failures that meet the threshold of corporate wrongdoing "
            "as documented by regulators, enforcement agencies, or credible "
            "investigations?"
        ),
        relevance_threshold=0.5,
        description=("Regulatory violations, executive misconduct, safety "
                     "failures, fraud, accounting irregularities"),
    ),
    "sanctions_evasion": DomainPreset(
        hypothesis=(
            "Does the entity engage in or facilitate activities designed to "
            "circumvent US, EU, or UN sanctions regimes -- including front "
            "companies, third-country routing, beneficial-owner obfuscation, "
            "or commodity / crypto channels that evade enforcement?"
        ),
        relevance_threshold=0.55,
        description="Sanctions circumvention, fronts, dark fleets, crypto routing",
    ),
    "election_interference": DomainPreset(
        hypothesis=(
            "Does the entity show evidence of state-sponsored or organised "
            "election interference activity -- disinformation campaigns, "
            "election-infrastructure intrusion, or covert financial flows "
            "to political actors -- attested by official investigation, "
            "indictment, or credible reporting?"
        ),
        relevance_threshold=0.55,
        description="Foreign or domestic election interference operations",
    ),
    "environmental_violations": DomainPreset(
        hypothesis=(
            "Does the entity have a documented pattern of environmental "
            "regulatory violations, pollution incidents, remediation "
            "failures, or material breaches of permitting / disclosure "
            "obligations attested by enforcement agencies or credible "
            "investigative reporting?"
        ),
        relevance_threshold=0.5,
        description="Environmental violations, pollution incidents, remediation failures",
    ),
    "supply_chain_human_rights": DomainPreset(
        hypothesis=(
            "Does the entity's supply chain, sourcing, or operational "
            "footprint involve forced labour, child labour, conflict "
            "minerals, or other internationally-recognised human-rights "
            "violations attested by NGOs, regulators, or credible reporting?"
        ),
        relevance_threshold=0.55,
        description="Forced labour, conflict minerals, human-rights supply-chain risk",
    ),
    "criminal_investigation": DomainPreset(
        # Framed as an INVOLVEMENT test, not a culpability test. Evidence
        # extraction is hypothesis-gated: a "does the entity commit crime"
        # wording produces evidence only for suspects/perpetrators, so victims,
        # witnesses, and investigating officers get no evidence record and are
        # pruned (no prob -> dropped). An investigation graph needs every party
        # to the incident, so the hypothesis credits any material role.
        hypothesis=(
            "Is the entity a party to, or materially involved in, the criminal "
            "matter under investigation -- as a suspect, perpetrator, victim, "
            "witness, investigating officer, or other involved person or "
            "organisation (e.g. organised crime, money laundering, fraud, "
            "trafficking, racketeering, bribery, or violent crime) -- as "
            "attested by the source material?"
        ),
        relevance_threshold=0.55,
        description="Crime incidents + parties: suspects, victims, witnesses, organised crime, fraud",
    ),
    "product_research": DomainPreset(
        # Framed as a CANDIDATE-FIT test, not a "reflects the subject" test.
        # The `general` preset scores an entity by how central it is to the
        # query subject, which on a product query ranks the reference brand the
        # buyer wants ALTERNATIVES to at the very top (observed on a real run:
        # "best android tablets ... alternatives to Apple and Huawei" ranked
        # APPLE #1 at score 1.0, with the actual Android options at ~0.49), and
        # admits retailers (Best Buy), review sites (PCMag), accessories, chips
        # (M3, A16), and unrelated marketing names (artists/studios) as
        # high-relevance nodes. This wording instead rewards specific, choosable
        # products that fit the stated need, platform, and constraints, and
        # explicitly down-ranks excluded brands plus non-product context.
        hypothesis=(
            "Is the entity a specific, purchasable PRODUCT -- a named model or "
            "product line -- that a buyer could realistically choose to satisfy "
            "the need, use case, platform, and constraints stated in the "
            "investigation query? Score it highly only when the source text "
            "presents it as a viable candidate for that need. Treat any brand or "
            "product the query asks to exclude or find alternatives to as LOW "
            "relevance, and treat retailers, review publications, companies, "
            "accessories, and component parts (chips, operating systems) as "
            "low-relevance context rather than choosable products."
        ),
        relevance_threshold=0.5,
        description="Compare candidate products for a stated buyer need, platform, and constraints",
    ),
    "general": DomainPreset(
        hypothesis=(
            "Does the entity's documented activity reflect the subject of "
            "the investigation query, based strictly on the source text?"
        ),
        relevance_threshold=0.5,
        description="Catch-all when no preset applies; investigation_query carries the framing",
    ),
}


def resolve(domain: str | None, *,
            override_hypothesis: str | None = None,
            override_threshold: float | None = None) -> tuple[str, float, str]:
    """Return (hypothesis, threshold, domain_label) given a preset name and
    optional CLI overrides. CLI overrides win when present."""
    preset = PRESETS.get(domain or "general") or PRESETS["general"]
    hyp = override_hypothesis or preset.hypothesis
    thr = override_threshold if override_threshold is not None else preset.relevance_threshold
    return hyp, thr, (domain or "general")


def list_domains() -> str:
    """One-line summary suitable for --help output."""
    return "\n".join(f"  {name:<28s} {p.description}" for name, p in PRESETS.items())
