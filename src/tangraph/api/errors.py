"""Flask error handlers — structured JSON responses for uncaught errors.

Phase 1 keeps these minimal: pipeline-level errors are already swallowed
inside ``InvestigationPipeline.run`` and returned as 200-with-cached-state.
These handlers cover *framework-level* failures (bad JSON body, route
not found, method not allowed, unhandled exceptions in route wiring).
"""

import logging

from flask import Flask, jsonify

_log = logging.getLogger(__name__)


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(400)
    def _bad_request(e):  # type: ignore[no-untyped-def]
        return jsonify({"status": "error", "message": str(getattr(e, "description", "bad request"))}), 400

    @app.errorhandler(404)
    def _not_found(e):  # type: ignore[no-untyped-def]
        return jsonify({"status": "error", "message": "not found"}), 404

    @app.errorhandler(405)
    def _method_not_allowed(e):  # type: ignore[no-untyped-def]
        return jsonify({"status": "error", "message": "method not allowed"}), 405

    @app.errorhandler(500)
    def _server_error(e):  # type: ignore[no-untyped-def]
        _log.exception("Unhandled server error")
        return jsonify({"status": "error", "message": "internal server error"}), 500
