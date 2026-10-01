"""Interactive chat loop for the ReAct agent demo."""

from hindsight.prompts import init_messages
from langchain_core.messages import HumanMessage

from react_agent.graph.graph import build_graph
from react_agent.graph.service import stream_agent_chat


def main() -> None:
    """Chat with the agent until the user types "exit" or "quit".

    The conversation history carries over between turns. If a turn fails to finish, the
    history from before that turn is kept.
    """
    graph = build_graph()
    messages = init_messages()

    while True:
        user_message = input("User message: ")
        if not user_message:
            continue

        if user_message.lower() in ["exit", "quit"]:
            print("Exiting...")
            break

        messages.append(HumanMessage(content=user_message))

        final_state = stream_agent_chat(graph, messages)

        if final_state is not None:
            messages = final_state["messages"]

        print("\n\n---\n")


if __name__ == "__main__":
    main()
