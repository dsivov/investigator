"""Junction-tree belief propagation over a TMFG (Phase 2).

Takes a ``TMFGResult`` from ``construct_tmfg`` (a chordal graph decomposed
into tetrahedra glued by triangular separators -- exactly the *junction tree*
inference needs) and runs sum-product message passing to compute a posterior
probability for each entity, fusing:

  * the entity's own evidence-based prior (from ``evidence_probability``);
  * the *affiliation-coupled* priors of its clique-mates, weighted by TMFG
    edge weight.

Factor model (pairwise Ising on the chordal graph):
  Node potential:  psi_i(X_i = 1) = prior_i,  psi_i(X_i = 0) = 1 - prior_i
  Pair potential:  psi_ij(X_i, X_j) = exp(beta * w_ij * s_i * s_j)
                   where s = 2*X - 1 in {-1, +1} (Ising spin) and
                   w_ij is the TMFG edge weight in [0, 1] (positive = affinity).
Rewards same-status pairs (both implicated OR both cleared) proportional to
affiliation strength; penalises mismatches.

Two-pass sum-product on the rooted clique tree yields exact marginals (a
classical result for chordal graphs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations, product

import networkx as nx
import numpy as np

from investigator.graph.tmfg import TMFGResult


@dataclass
class BeliefPropagationResult:
    posterior: dict      # node id -> P(X=1) after BP
    prior: dict          # node id -> P(X=1) before BP (input)
    delta: dict          # posterior - prior
    clique_beliefs: list = field(default_factory=list)   # one np.ndarray of shape (2,2,2,2) per tetrahedron
    tetrahedra: list = field(default_factory=list)       # mirrors TMFGResult.tetrahedra (order preserved)


def _pair_factor(weight: float, beta: float) -> np.ndarray:
    """Pairwise Ising factor on the [0,1] node states. Index by (X_i, X_j)."""
    # psi[x_i, x_j] = exp(beta * w * (2x_i - 1)(2x_j - 1))
    e = np.exp(beta * float(weight))
    return np.array([[e, 1.0 / e], [1.0 / e, e]])     # (++) high, (+-) low, (-+) low, (--) high


def propagate(tmfg: TMFGResult, priors: dict, *, beta: float = 1.0) -> BeliefPropagationResult:
    """Sum-product BP on the TMFG's clique tree. Returns posterior marginals.

    Parameters
    ----------
    tmfg : TMFGResult
        Output of ``construct_tmfg``. Provides tetrahedra (4-cliques) + clique
        tree (separators).
    priors : dict[node_id, float]
        Per-entity prior P(X=1). Nodes absent from the dict get prior 0.5
        (uninformative).
    beta : float
        Inverse temperature for the Ising coupling. Larger = stronger pull
        toward clique-mates' status. Default 1.0.
    """
    tetras = [tuple(t) for t in tmfg.tetrahedra]
    if not tetras:
        return BeliefPropagationResult(posterior=dict(priors), prior=dict(priors), delta={k: 0.0 for k in priors})

    # Assign each entity's node potential to ONE clique only (the first that
    # contains it). The remaining cliques get a uniform-1 factor on that
    # variable; the clique-tree messages enforce consistency.
    node_owner: dict = {}
    for ci, members in enumerate(tetras):
        for v in members:
            node_owner.setdefault(v, ci)

    # 1) Initial clique potentials -- one (2,2,2,2) array per tetrahedron.
    clique_potentials: list[np.ndarray] = []
    for ci, members in enumerate(tetras):
        a, b, c, d = members
        psi = np.ones((2, 2, 2, 2))
        # Node factors (only for nodes owned by this clique)
        for axis, v in enumerate((a, b, c, d)):
            if node_owner[v] != ci:
                continue
            p = float(priors.get(v, 0.5))
            p = min(max(p, 1e-6), 1 - 1e-6)
            node_psi = np.array([1.0 - p, p])
            psi = psi * node_psi.reshape([2 if i == axis else 1 for i in range(4)])
        # Pair factors -- six pairs in a tetrahedron.
        for i, j in combinations(range(4), 2):
            u, v = members[i], members[j]
            w = 0.0
            if tmfg.graph.has_edge(u, v):
                w = float(tmfg.graph[u][v].get("weight", 0.0))
            elif tmfg.graph.has_edge(v, u):
                w = float(tmfg.graph[v][u].get("weight", 0.0))
            f = _pair_factor(w, beta)    # shape (2, 2) indexed by (X_i, X_j)
            shape = [1, 1, 1, 1]
            shape[i], shape[j] = 2, 2
            psi = psi * f.reshape(shape)
        clique_potentials.append(psi)

    # 2) Root the clique tree and compute parent / children traversal order.
    tree: nx.Graph = tmfg.clique_tree
    if tree.number_of_nodes() == 0:
        # Single tetrahedron -- the only clique is its own marginal source.
        belief = clique_potentials[0]
        belief = belief / belief.sum()
        posterior = _marginals_from_clique(belief, tetras[0])
        prior = {v: float(priors.get(v, 0.5)) for v in posterior}
        return BeliefPropagationResult(
            posterior=posterior, prior=prior, delta={k: posterior[k] - prior[k] for k in posterior},
            clique_beliefs=[belief], tetrahedra=tetras,
        )
    root = 0
    parent: dict = {root: None}
    order: list = []     # BFS order for the upward pass (in REVERSE), root last
    visited = {root}
    queue = [root]
    while queue:
        u = queue.pop(0)
        order.append(u)
        for v in tree.neighbors(u):
            if v not in visited:
                visited.add(v)
                parent[v] = u
                queue.append(v)

    # 3) Messages: msg[(src, dst)] = a tensor over the SEPARATOR variables
    # (the 3 entities shared by src's clique and dst's clique).
    messages: dict = {}

    # Cache for "separator variables" between two adjacent cliques.
    def _separator_axes(src_ci: int, dst_ci: int):
        """Return (axes_in_src, axes_in_dst) — which axes in each clique are
        the shared 3 separator vertices, and the matching axes in the other."""
        sep = set(tree.edges[src_ci, dst_ci]["separator"])
        src_axes = [i for i, v in enumerate(tetras[src_ci]) if v in sep]
        dst_axes = [i for i, v in enumerate(tetras[dst_ci]) if v in sep]
        # Map src axes to dst axes by matching the actual vertex
        sep_vs_src = [tetras[src_ci][i] for i in src_axes]
        dst_axis_for = {v: i for i, v in enumerate(tetras[dst_ci]) if v in sep}
        dst_axes_aligned = [dst_axis_for[v] for v in sep_vs_src]
        return src_axes, dst_axes_aligned

    # Upward pass: leaves -> root. Process in REVERSE BFS order.
    for u in reversed(order):
        if parent[u] is None:
            continue
        p = parent[u]
        psi = clique_potentials[u].copy()
        # Incorporate incoming messages from children (other than parent direction).
        for child in tree.neighbors(u):
            if child == p:
                continue
            psi = _absorb_message(psi, messages[(child, u)])
        # Marginalize over u's non-separator variable (the variable NOT shared with p).
        src_axes, dst_axes = _separator_axes(u, p)
        sum_axes = tuple(i for i in range(4) if i not in src_axes)
        marg = psi.sum(axis=sum_axes)   # shape (2,2,2) over u's separator axes (in order)
        # Re-permute axes to match p's axis order for the separator.
        # marg's axes are u's separator axes in their original order (low->high).
        # We need them aligned to p's axes (dst_axes order).
        # Approach: build a (2,2,2) tensor indexed by the SHARED entity vertex
        # tuple (in src order), then store. When consumer (p) absorbs, it knows
        # the dst_axes mapping.
        messages[(u, p)] = (marg, src_axes, dst_axes)

    # Downward pass: root -> leaves. Process in BFS order.
    for u in order:
        for child in tree.neighbors(u):
            if parent.get(child) != u:
                continue
            # message from u to child: marginalize u's potential (with incoming
            # messages from EVERYONE except `child`) over u's non-separator axis.
            psi = clique_potentials[u].copy()
            for nbr in tree.neighbors(u):
                if nbr == child:
                    continue
                psi = _absorb_message(psi, messages[(nbr, u)])
            src_axes, dst_axes = _separator_axes(u, child)
            sum_axes = tuple(i for i in range(4) if i not in src_axes)
            marg = psi.sum(axis=sum_axes)
            messages[(u, child)] = (marg, src_axes, dst_axes)

    # 4) Clique beliefs: combine each clique's potential with ALL incoming
    # messages from neighbors. Normalize.
    beliefs: list[np.ndarray] = []
    for ci, members in enumerate(tetras):
        psi = clique_potentials[ci].copy()
        for nbr in tree.neighbors(ci):
            psi = _absorb_message(psi, messages[(nbr, ci)])
        z = psi.sum()
        beliefs.append(psi / z if z > 0 else psi)

    # 5) Marginalize beliefs -> per-entity posterior. A node lives in multiple
    # cliques; on the tree, all cliques agree on its marginal. Use the first
    # clique that contains the entity.
    posterior: dict = {}
    for ci, members in enumerate(tetras):
        for axis, v in enumerate(members):
            if v in posterior:
                continue
            sum_axes = tuple(i for i in range(4) if i != axis)
            m = beliefs[ci].sum(axis=sum_axes)   # shape (2,) -> P(X=0), P(X=1)
            posterior[v] = float(m[1] / m.sum()) if m.sum() > 0 else 0.5

    prior_out = {v: float(priors.get(v, 0.5)) for v in posterior}
    delta = {v: posterior[v] - prior_out[v] for v in posterior}
    return BeliefPropagationResult(
        posterior=posterior, prior=prior_out, delta=delta,
        clique_beliefs=beliefs, tetrahedra=tetras,
    )


def _absorb_message(psi: np.ndarray, msg, *_unused) -> np.ndarray:
    """Multiply an incoming message into the receiving clique's potential.

    Message tensor has 3 axes in the SENDER's separator axis order; the
    receiver's matching axes are ``dst_axes`` (the *same* vertices, in the
    sender's order — which may or may not be monotonic in the receiver). To
    broadcast correctly we transpose the tensor so its axes line up with the
    ``argsort(dst_axes)`` ordering, then reshape into the (2,2,2,2) receiver
    space with size-2 at the receiver's separator positions.
    """
    tensor, _src_axes, dst_axes = msg
    sort_order = np.argsort(dst_axes)
    tensor_sorted = np.transpose(tensor, axes=sort_order)
    full_shape = [1, 1, 1, 1]
    for a in dst_axes:
        full_shape[a] = 2
    return psi * tensor_sorted.reshape(full_shape)


def _marginals_from_clique(belief: np.ndarray, members) -> dict:
    out = {}
    for axis, v in enumerate(members):
        sum_axes = tuple(i for i in range(4) if i != axis)
        m = belief.sum(axis=sum_axes)
        out[v] = float(m[1] / m.sum()) if m.sum() > 0 else 0.5
    return out
