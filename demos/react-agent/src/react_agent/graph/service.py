"""Run the ReAct agent graph and stream its output to the console."""

from typing import Any

from langgraph.graph.state import CompiledStateGraph

from react_agent.graph.state import State


def stream_agent_chat(graph: CompiledStateGraph, messages: list) -> Any | None:
    """Run the graph on the given messages, streaming output as it is produced.

    Prints model text and tool-call names as the generator streams them, and each tool
    result as the tool node returns it.

    Returns the final graph state once execution completes, or None if the graph does
    not finish.
    """
    final_state = None
    for mode, payload in graph.stream(
        State(messages=messages), stream_mode=["messages", "values"]
    ):
        if mode == "values":
            final_state = payload
            continue

        chunk, metadata = payload
        node = metadata.get("langgraph_node")

        if node == "generator":
            for tool_call in chunk.tool_call_chunks:
                if tool_call.get("name"):
                    print(f"\n[calling {tool_call['name']}]", flush=True)
            if chunk.content:
                print(chunk.content, end="", flush=True)
        elif node == "tool":
            print(f"[{chunk.name} -> {chunk.content}]\n", flush=True)

    return final_state
