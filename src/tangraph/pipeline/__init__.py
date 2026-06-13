"""Investigation pipeline: chunking, source shaping, hypothesis selection."""

from tangraph.pipeline.chunking import json_chunker, text_chunker
from tangraph.pipeline.hypothesis import return_hypothesis_for_domain

# Orchestrator is intentionally NOT imported here to avoid forcing every
# tangraph.pipeline consumer to pay the DSPy + heavy-model load cost at
# import. Import it directly: `from tangraph.pipeline.orchestrator import
# InvestigationPipeline`.

__all__ = [
    "json_chunker",
    "return_hypothesis_for_domain",
    "text_chunker",
]
