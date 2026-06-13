"""OSINTGraph: triangulation-graph server for compliance & OSINT investigations.

Core flow: ingest text/JSON -> extract entities & edges via LLM -> build NetworkX
graph -> triangulate (filter by query-relevance & connectivity) -> return graph.
"""

__version__ = "0.1.0"
