"""Structural tests for the Phase 4 graph."""

from agent.graph import build_graph


def test_graph_has_recovery_branch_and_gate() -> None:
    graph = build_graph()
    nodes = graph.get_graph().nodes
    assert {"planner", "investigator", "evidence_reviewer", "recovery", "human_gate", "filer"} <= set(nodes)
    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
    assert ("evidence_reviewer", "recovery") in edges
    assert ("evidence_reviewer", "human_gate") in edges