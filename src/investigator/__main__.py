"""Command-line entry point: ``python -m investigator``.

Loads secrets, builds the Flask app, optionally spawns the analytics
processes (LightRAG server + reranker), and runs the development server.

Production deployment: front this with gunicorn/uvicorn instead of
``app.run(...)``. Phase 3 candidate.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import threading

from investigator.config import SecretLoader, global_args

# Secrets must be exported BEFORE importing the app factory: the factory
# pulls in the orchestrator, which calls dspy.LM(...) at module load.
SecretLoader().export_to_env("OPENAI_API_KEY")

from investigator.app import create_app  # noqa: E402

_log = logging.getLogger("investigator")


def main() -> None:
    app, analytics_worker = create_app()

    if global_args.analytic_engine_enabled:
        from investigator.analytics.reranker import main as rag_rerank_server
        from investigator.analytics.server import main as lightrag_server

        def _start_lightrag_server() -> None:
            lightrag_server()

        def _start_rerank_server(host: str, port: int) -> None:
            asyncio.run(rag_rerank_server(host=host, port=port))

        analytics_worker.start()
        _log.info("Analytic engine enabled")
        reranker_thread = threading.Thread(
            target=_start_rerank_server,
            args=(global_args.reranker_host, global_args.reranker_port),
            daemon=True,
        )
        reranker_thread.start()
        lightrag_process = multiprocessing.Process(target=_start_lightrag_server)
        lightrag_process.start()
    else:
        lightrag_process = None
        reranker_thread = None

    app.run(host=global_args.investigator_host, port=global_args.investigator_port)

    if lightrag_process is not None:
        lightrag_process.join()


if __name__ == "__main__":
    main()
