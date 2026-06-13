"""Flask application factory.

Wires the four state objects (``state_repo``, ``session_store``,
``analytics_worker``, ``pipeline``) and the HTTP blueprint into a single
``Flask`` instance.

SecretLoader must have run before calling ``create_app`` because the
orchestrator imports DSPy at module-load and DSPy reads
``OPENAI_API_KEY`` from the environment immediately.
"""

from __future__ import annotations

from flask import Flask

from tangraph.analytics import AnalyticsWorker, RAGClient
from tangraph.api import create_api_blueprint, register_error_handlers
from tangraph.config import global_args
from tangraph.pipeline.orchestrator import InvestigationPipeline
from tangraph.state import InvestigationStateRepo, SessionStore


def create_app(
    *,
    rag_base_url: str = "http://localhost:9626",
    debug_mode: bool = False,
) -> tuple[Flask, AnalyticsWorker]:
    """Construct the Flask app + return the analytics worker.

    Returns the worker as well so the entry-point can call ``start()`` on
    it when ``--analytic_engine_enabled`` is set, without re-importing
    module-level globals.
    """
    state_client = RAGClient(base_url=rag_base_url)
    analytics_worker = AnalyticsWorker(state_client)
    state_repo = InvestigationStateRepo()
    session_store = SessionStore()
    pipeline = InvestigationPipeline(
        state_repo=state_repo,
        session_store=session_store,
        analytics_worker=analytics_worker,
        analytics_enabled=global_args.analytic_engine_enabled,
        debug_mode=debug_mode,
    )

    app = Flask(__name__)
    app.config["ANALYTIC_ENGINE_ENABLED"] = global_args.analytic_engine_enabled
    app.register_blueprint(create_api_blueprint(pipeline))
    register_error_handlers(app)
    return app, analytics_worker
