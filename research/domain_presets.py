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
