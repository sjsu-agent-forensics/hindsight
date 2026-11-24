"""Run one agent turn and stream its output to the console."""

from typing import Any

from langgraph.graph.state import CompiledStateGraph


async def stream_turn(agent: CompiledStateGraph, messages: list) -> Any | None:
    """Run the agent on the given messages, printing output as it is produced.

    Prints model text and tool-call names as the model node streams them, and each tool
    result as the tools node returns it. Tool results are untrusted and printed as is.

    Returns the final agent state once execution completes, or None if the agent does
    not finish.
    """
    final_state = None
    async for mode, payload in agent.astream(
        {"messages": messages}, stream_mode=["messages", "values"]
    ):
        if mode == "values":
            final_state = payload
            continue

        # Node names are the ones create_agent() gives its model and tool steps.
        chunk, metadata = payload
        node = metadata.get("langgraph_node")
        if node == "model":
            for tool_call in chunk.tool_call_chunks:
                if tool_call.get("name"):
                    print(f"\n[calling {tool_call['name']}]", flush=True)
            if chunk.content:
                print(chunk.text, end="", flush=True)
        elif node == "tools":
            print(f"[{chunk.name} -> {chunk.text}]\n", flush=True)

    return final_state
