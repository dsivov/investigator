"""Flask application factory.

Wires the state objects (``state_repo``, ``cumulative_kg``, ``pipeline``) and
the HTTP blueprint into a single ``Flask`` instance.

SecretLoader must have run before calling ``create_app`` because the
orchestrator imports DSPy at module-load and DSPy reads
``OPENAI_API_KEY`` from the environment immediately.
"""

from __future__ import annotations

from flask import Flask

from investigator.analytics import CumulativeKG, kg_store_dir
from investigator.analytics.llm import make_openai_llm
from investigator.api import create_api_blueprint, register_error_handlers
from investigator.config import global_args
from investigator.pipeline.orchestrator import InvestigationPipeline
from investigator.state import InvestigationStateRepo


def create_app(*, debug_mode: bool = False) -> Flask:
    """Construct the Flask app.

    When ``--analytic_engine_enabled`` is set, a :class:`CumulativeKG` is built
    over the shared :func:`kg_store_dir` (persistent, outside the code tree;
    the same store the UI Knowledge Base queries) and accumulates every finished
    investigation's graph (in-process; no LightRAG server). Otherwise the
    pipeline runs without KG accumulation.
    """
    analytics_enabled = global_args.analytic_engine_enabled
    cumulative_kg = (
        CumulativeKG(working_dir=kg_store_dir(), llm_model_func=make_openai_llm())
        if analytics_enabled
        else None
    )
    state_repo = InvestigationStateRepo()
    pipeline = InvestigationPipeline(
        state_repo=state_repo,
        cumulative_kg=cumulative_kg,
        analytics_enabled=analytics_enabled,
        debug_mode=debug_mode,
    )

    app = Flask(__name__)
    app.config["ANALYTIC_ENGINE_ENABLED"] = analytics_enabled
    app.register_blueprint(create_api_blueprint(pipeline))
    register_error_handlers(app)
    return app
