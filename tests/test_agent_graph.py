from app.agent.graph import build_agent_graph, route_after_anomaly
from app.agent.state import AgentState


def test_route_after_anomaly_ends_when_no_anomaly():
    assert (
        route_after_anomaly(
            AgentState(
                project_id="prod",
                trigger_source="manual",
                anomaly_detected=False,
            )
        )
        == "__end__"
    )
    assert (
        route_after_anomaly(
            AgentState(
                project_id="prod",
                trigger_source="manual",
                anomaly_detected=True,
            )
        )
        == "rag_retrieve_node"
    )


def test_build_agent_graph_compiles():
    graph = build_agent_graph()

    assert graph is not None
