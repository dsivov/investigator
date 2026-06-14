"""Investigation pipeline: chunking, source shaping, hypothesis selection."""

from investigator.pipeline.chunking import json_chunker, text_chunker
from investigator.pipeline.hypothesis import return_hypothesis_for_domain

# Orchestrator is intentionally NOT imported here to avoid forcing every
# investigator.pipeline consumer to pay the DSPy + heavy-model load cost at
# import. Import it directly: `from investigator.pipeline.orchestrator import
# InvestigationPipeline`.

__all__ = [
    "json_chunker",
    "return_hypothesis_for_domain",
    "text_chunker",
]
