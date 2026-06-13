"""Canonical entity-id derivation.

One function so every consumer derives a node's key the same way. Today the
pipeline open-codes ``node.get("representative_identifier", node["identifier"])``
(and ``.upper()`` on top, in graph build) at several call sites; when those drift
the index, graph nodes, and edge endpoints stop agreeing on a node's identity.

``canonical_id`` is that single key:
  * ``representative_identifier`` wins (the post-dedup canonical name),
  * falling back to ``identifier`` (the entity's working name).

Result is upper-cased because the extraction step stores ``identifier`` as
``name.upper()`` and ``build_graph`` keys graph nodes on
``representative_identifier.upper()`` — normalising here keeps the state index,
the NetworkX graph, and edge endpoints on the same key.
"""

from __future__ import annotations


def canonical_id(node: dict) -> str:
    """Return the canonical upper-cased id for an entity record.

    Empty string when neither id field is usable (caller decides whether to
    index/skip such a record).
    """
    rep = node.get("representative_identifier")
    if rep:
        return str(rep).upper()
    ident = node.get("identifier")
    return str(ident).upper() if ident else ""
