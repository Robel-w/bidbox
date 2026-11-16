"""Structural tests for the Phase 4 graph."""

from langgraph.graph import END

from agent.graph import approval_route, build_graph
from agent.investigator import npx_command


def test_graph_has_recovery_branch_and_gate() -> None:
    graph = build_graph()
    nodes = graph.get_graph().nodes
    assert {"planner", "investigator", "evidence_reviewer", "recovery", "human_gate", "filer"} <= set(nodes)
    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
    assert ("evidence_reviewer", "recovery") in edges
    assert ("evidence_reviewer", "human_gate") in edges


def test_borrowed_server_uses_platform_npx() -> None:
    assert npx_command("win32") == "npx.cmd"
    assert npx_command("linux") == "npx"
    assert npx_command("darwin") == "npx"


def test_rejected_or_missing_decision_never_reaches_filer() -> None:
    assert approval_route({"approval_decision": "approve"}) == "filer"
    assert approval_route({"approval_decision": "reject"}) == END
    assert approval_route({}) == END