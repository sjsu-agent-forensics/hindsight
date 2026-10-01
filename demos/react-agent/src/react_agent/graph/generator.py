"""Generator node: the model-calling step of the ReAct loop."""

from collections.abc import Callable

from hindsight import get_llm, get_local_datetime
from react_agent.graph.state import State


def generator_node_factory() -> Callable[[State], dict]:
    """Create the generator node for the state graph.

    The node calls the model with the conversation so far, with the local tools bound so
    the model can request them. Tool calls it requests are executed by the graph's tool
    node, not here.
    """

    def generator_node(state: State) -> dict:
        llm_with_tools = get_llm(reasoning=True).bind_tools([get_local_datetime])
        response = llm_with_tools.invoke(state["messages"])

        return {"messages": [response]}

    return generator_node
