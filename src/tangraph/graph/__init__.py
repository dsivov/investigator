"""NetworkX graph build, connectivity scoring, dedup/merge, visualization."""

from tangraph.graph.dedup import (
    attach_relations_to_nodes,
    cluster_identifiers,
    dedup_events_by_signature,
    deduplicate_entities,
    infer_event_temporal_edges,
    validate_entity_canonicals,
    group_edges_by_chunk,
    merge_data_fields,
    merge_duplicate_group,
    merge_run_into_saved,
    resolve_edge_endpoints,
)
from tangraph.graph.filter import filter_by_corroboration
from tangraph.graph.junction_tree import BeliefPropagationResult, propagate as junction_tree_propagate
from tangraph.graph.tmfg import construct_tmfg, tetrahedron_weight
from tangraph.graph.operations import (
    build_graph,
    evidence_probability,
    filter_nodes_by_score,
    score_graph_by_connectivity,
)
from tangraph.graph.similarity import (
    cosine_similarity_edges,
    cosine_similarity_nodes,
    jaccard_similarity_edges,
    jaccard_similarity_nodes,
    preprocess_text,
)
from tangraph.graph.visualize import visualize_clique_tree, visualize_graph, _delta_color, _posterior_color

__all__ = [
    "BeliefPropagationResult",
    "attach_relations_to_nodes",
    "build_graph",
    "cosine_similarity_edges",
    "cosine_similarity_nodes",
    "construct_tmfg",
    "tetrahedron_weight",
    "junction_tree_propagate",
    "visualize_clique_tree",
    "filter_by_corroboration",
    "filter_nodes_by_score",
    "dedup_events_by_signature",
    "deduplicate_entities",
    "infer_event_temporal_edges",
    "validate_entity_canonicals",
    "group_edges_by_chunk",
    "cluster_identifiers",
    "jaccard_similarity_edges",
    "jaccard_similarity_nodes",
    "merge_duplicate_group",
    "merge_data_fields",
    "merge_run_into_saved",
    "evidence_probability",
    "preprocess_text",
    "resolve_edge_endpoints",
    "score_graph_by_connectivity",
    "visualize_graph",
]
