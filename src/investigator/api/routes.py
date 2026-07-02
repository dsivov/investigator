"""Flask blueprint that exposes the OSINTGraph HTTP API.

The route is intentionally tiny: parse JSON → hand to the injected
``InvestigationPipeline`` → return the dict. Real work happens inside
the pipeline.
"""

from __future__ import annotations

from flask import Blueprint, request
from pydantic import ValidationError

from investigator.api.schemas import GetNodesRequest
from investigator.pipeline.orchestrator import InvestigationPipeline


def create_api_blueprint(pipeline: InvestigationPipeline) -> Blueprint:
    """Build the ``api_v1`` blueprint wired to a specific pipeline instance.

    Pipeline is injected (not module-imported) so tests / alternate apps
    can construct their own pipeline with stubbed state / analytics.
    """
    bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

    @bp.route("/get_nodes", methods=["POST"])
    async def get_graph_nodes():
        """Run the triangulation pipeline. The body is validated against
        ``investigator.api.schemas.GetNodesRequest``; invalid input gets a 400
        with field-level errors instead of silently coercing to defaults.
        """
        payload = request.get_json(silent=True) or {}
        try:
            # Validate as a gate only (pydantic ignores extra keys), then forward
            # the ORIGINAL payload so pass-through fields like `run` still reach
            # the pipeline.
            GetNodesRequest.model_validate(payload)
        except ValidationError as e:
            return {
                "status": "error",
                "message": "Invalid request body.",
                "errors": [
                    {"field": ".".join(str(p) for p in err["loc"]), "error": err["msg"]}
                    for err in e.errors()
                ],
            }, 400
        return await pipeline.run(payload)

    return bp
