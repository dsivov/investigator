"""Flask blueprint that exposes the OSINTGraph HTTP API.

The route is intentionally tiny: parse JSON → hand to the injected
``InvestigationPipeline`` → return the dict. Real work happens inside
the pipeline.
"""

from __future__ import annotations

from flask import Blueprint, request

from tangraph.pipeline.orchestrator import InvestigationPipeline


def create_api_blueprint(pipeline: InvestigationPipeline) -> Blueprint:
    """Build the ``api_v1`` blueprint wired to a specific pipeline instance.

    Pipeline is injected (not module-imported) so tests / alternate apps
    can construct their own pipeline with stubbed state / analytics.
    """
    bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

    @bp.route("/get_nodes", methods=["POST"])
    async def get_graph_nodes():
        """Run the triangulation pipeline. See
        ``tangraph.api.schemas.GetNodesRequest`` for the body shape.
        """
        payload = request.get_json() or {}
        return await pipeline.run(payload)

    return bp
