"""OpenAI LLM function for the cumulative KG (LightRAG-compatible).

LightRAG needs an ``llm_model_func`` for description summarisation during merge
and keyword extraction during retrieval. This builds one backed by OpenAI.

We deliberately avoid structured-output keyword extraction: openai>=1.x exposes
``chat.completions.parse`` only on the beta client, but LightRAG calls the
non-beta ``.parse`` whenever ``response_format`` is set. So we drop
``keyword_extraction``/``response_format`` and route through ``.create`` --
LightRAG then parses the keyword JSON from the plain completion text.
"""
from __future__ import annotations

import os

from lightrag.llm.openai import openai_complete_if_cache

# Default kept light (sufficient for keyword extraction + short summaries);
# override with INVESTIGATOR_KG_LLM_MODEL.
DEFAULT_KG_LLM_MODEL = os.getenv("INVESTIGATOR_KG_LLM_MODEL", "gpt-4.1-mini")


def make_openai_llm(model: str = DEFAULT_KG_LLM_MODEL, api_key: str | None = None):
    """Return a LightRAG ``llm_model_func`` backed by OpenAI."""
    key = api_key or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("LLM_BINDING_HOST") or None

    async def _llm(prompt, system_prompt=None, history_messages=None, **kwargs):
        kwargs.pop("keyword_extraction", None)
        kwargs.pop("response_format", None)
        return await openai_complete_if_cache(
            model,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages or [],
            api_key=key,
            base_url=base_url,
            **kwargs,
        )

    return _llm
