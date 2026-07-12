"""WSGI entry point for the pipeline engine.

Production serving (roadmap P0):

    PYTHONPATH=src:. gunicorn -w 1 --threads 16 --timeout 0 \\
        -b 127.0.0.1:5003 investigator.wsgi:app

Constraints baked into the command above:
  - ``-w 1`` is REQUIRED: session state, the per-session pipeline locks, and
    the cumulative-KG background loop are all in-process. Multiple workers
    would each hold their own copy and corrupt nothing loudly -- they would
    just silently not see each other's sessions. Concurrency comes from
    threads.
  - ``--timeout 0``: a stage-2 ``/get_nodes`` call legitimately runs for many
    minutes; the client (cross_event_investigation.py) applies its own 1800 s
    timeout.
  - Bind to 127.0.0.1: only the UI backend talks to the engine. Exposing it
    on a routable interface hands out unauthenticated pipeline access.

Configuration comes from the environment (ANALYTIC_ENGINE_ENABLED,
INVESTIGATOR_TMFG, INVESTIGATOR_*, ...) -- CLI flags are unavailable under a
WSGI server.
"""
from __future__ import annotations

from investigator.config import SecretLoader

# Secrets must be exported BEFORE importing the app factory: the factory
# pulls in the orchestrator, which calls dspy.LM(...) at module load.
SecretLoader().export_to_env("OPENAI_API_KEY")

from investigator.app import create_app  # noqa: E402

app = create_app()
