"""State graph wiring for the ReAct agent: generator and tool nodes in a loop."""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt.tool_node import ToolNode

from hindsight.tools import get_local_datetime
from react_agent.graph.generator import generator_node_factory
from react_agent.graph.state import State


def _should_continue(state: State) -> str:
    """Route to the tool node if the last message requests a tool call, else end."""
    messages = state["messages"]
    last_message = messages[-1]
    if last_message.tool_calls:
        return "tool"
    return END


def build_graph() -> CompiledStateGraph:
    """Build and compile the ReAct agent's state graph."""
    tool_node = ToolNode(tools=[get_local_datetime])
    graph = StateGraph(state_schema=State)

    graph.add_node("generator", generator_node_factory())
    graph.add_node("tool", tool_node)

    graph.add_edge(START, "generator")
    graph.add_conditional_edges("generator", _should_continue)
    graph.add_edge("tool", "generator")

    return graph.compile()
