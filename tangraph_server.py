"""Backwards-compatible entry point.

The canonical entry point is now ``python -m tangraph`` (see
``src/tangraph/__main__.py``). This shim is preserved so the documented
``python tangraph_server.py`` command from the original README keeps
working.
"""

from tangraph.__main__ import main

if __name__ == "__main__":
    main()
