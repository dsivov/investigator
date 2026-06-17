"""Command-line entry point: ``python -m investigator``.

Loads secrets, builds the Flask app, and runs the development server. When
``--analytic_engine_enabled`` is set, the app builds an in-process
:class:`~investigator.analytics.CumulativeKG` that accumulates every finished
investigation's graph into the persistent LightRAG store under
``global_args.working_dir`` -- no separate server process.

Production deployment: front this with gunicorn/uvicorn instead of
``app.run(...)``. Phase 3 candidate.
"""

from __future__ import annotations

import logging

from investigator.config import SecretLoader, global_args

# Secrets must be exported BEFORE importing the app factory: the factory
# pulls in the orchestrator, which calls dspy.LM(...) at module load.
SecretLoader().export_to_env("OPENAI_API_KEY")

from investigator.app import create_app  # noqa: E402

_log = logging.getLogger("investigator")


def main() -> None:
    app = create_app()
    if global_args.analytic_engine_enabled:
        _log.info("Analytic engine enabled: cumulative KG at %s", global_args.working_dir)
    app.run(host=global_args.investigator_host, port=global_args.investigator_port)


if __name__ == "__main__":
    main()
