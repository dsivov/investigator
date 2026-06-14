"""Pydantic request/response schemas for the HTTP API.

Phase 1 ships these as **typed documentation only** — the route handler
does not yet validate against them. Enforcing validation would change
behavior (currently invalid payloads silently coerce to defaults), so
that flip belongs in Phase 2 alongside the broader input-validation pass.

Phase 3 candidate: use FastAPI so these schemas drive both validation
and OpenAPI documentation automatically.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GetNodesRequest(BaseModel):
    """Body schema for ``POST /api/v1/get_nodes``."""

    session_id: str = Field(..., description="Investigation session id; new id = new investigation.")
    text: str = Field(..., description="JSON-encoded payload to triangulate (or plain text on resume).")
    query: str | None = Field(None, description="Free-form investigation query string.")
    hypotests: str | None = Field(None, description="Hypothesis / investigation subject.")
    domain: str = Field("general", description="Domain key: 'terror_financing' | 'narcotics' | 'edd' | 'general'.")
    relevance_threshold: float = Field(0.7, ge=0.0, le=1.0, description="Triangulation cutoff score.")


class GetNodesResponse(BaseModel):
    """Response schema for ``POST /api/v1/get_nodes``."""

    status: str
    session_id: str
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    status: str = "error"
    message: str
