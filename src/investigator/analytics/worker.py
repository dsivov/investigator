"""Background worker that ferries chunk records to the LightRAG state client.

Wraps what used to be three loose pieces in investigator_server.py:
  * module-level ``global_state_queue = Queue()``
  * module-level ``state_client = RAGClient(...)``
  * free function ``global_state_worker(q)`` plus the daemon thread that
    invokes it inside ``__main__``.
"""

from __future__ import annotations

import threading
from queue import Queue

from investigator.analytics.client import RAGClient
from investigator.logging import get_logger

_log = get_logger()


class AnalyticsWorker:
    """A daemon thread that consumes chunk records and pushes them into
    the LightRAG ingestion endpoint via ``RAGClient.add_text_document``.

    Use ``enqueue`` from request handlers; call ``start`` once during app
    startup to spin the daemon thread.
    """

    def __init__(self, client: RAGClient) -> None:
        self._client = client
        self._queue: Queue = Queue()
        self._thread: threading.Thread | None = None

    @property
    def queue(self) -> Queue:
        return self._queue

    def enqueue(self, item: dict) -> None:
        self._queue.put(item)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                _log.debug(f"Global state processing item with text len={len(item.get('text', ''))}")
                self._client.add_text_document(
                    text=item["text"],
                    file_source=item["identifier"] + "|" + item["uuid"],
                )
            except Exception as exc:
                _log.error(f"AnalyticsWorker failed to ingest item: {exc}")
            finally:
                self._queue.task_done()
