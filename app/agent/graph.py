from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    anomaly_detect_node,
    assemble_evidence_node,
    decide_heal_node,
    fetch_logs_node,
    fetch_metrics_node,
    fetch_traces_node,
    rag_retrieve_node,
    root_cause_node,
)
from app.agent.state import AgentState


def route_after_anomaly(state: AgentState) -> str:
    return "rag_retrieve_node" if state.anomaly_detected else END


def build_agent_graph(context: Any | None = None):
    workflow = StateGraph(AgentState)

    workflow.add_node(
        "fetch_metrics_node",
        _bind_context(fetch_metrics_node, context),
    )
    workflow.add_node(
        "fetch_logs_node",
        _bind_context(fetch_logs_node, context),
    )
    workflow.add_node(
        "fetch_traces_node",
        _bind_context(fetch_traces_node, context),
    )
    workflow.add_node(
        "anomaly_detect_node",
        _bind_context(anomaly_detect_node, context),
    )
    workflow.add_node(
        "rag_retrieve_node",
        _bind_context(rag_retrieve_node, context),
    )
    workflow.add_node(
        "root_cause_node",
        _bind_context(root_cause_node, context),
    )
    workflow.add_node(
        "decide_heal_node",
        _bind_context(decide_heal_node, context),
    )
    workflow.add_node(
        "assemble_evidence_node",
        _bind_context(assemble_evidence_node, context),
    )

    workflow.add_edge(START, "fetch_metrics_node")
    workflow.add_edge("fetch_metrics_node", "fetch_logs_node")
    workflow.add_edge("fetch_logs_node", "fetch_traces_node")
    workflow.add_edge("fetch_traces_node", "anomaly_detect_node")
    workflow.add_conditional_edges(
        "anomaly_detect_node",
        route_after_anomaly,
        {
            "rag_retrieve_node": "rag_retrieve_node",
            END: END,
        },
    )
    workflow.add_edge("rag_retrieve_node", "root_cause_node")
    workflow.add_edge("root_cause_node", "decide_heal_node")
    workflow.add_edge("decide_heal_node", "assemble_evidence_node")
    workflow.add_edge("assemble_evidence_node", END)

    return workflow.compile()


def _bind_context(node_func, context: Any | None):
    async def _node(state: AgentState) -> dict[str, Any]:
        if context is None:
            raise RuntimeError("AgentNodeContext is required to execute agent graph")
        return await node_func(state, context)

    return _node
