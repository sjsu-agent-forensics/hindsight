"""State schema for the ReAct agent graph."""

from typing import Annotated, TypedDict

from langgraph.graph import add_messages


class State(TypedDict):
    """Graph state: the conversation, with new messages appended by each node."""

    messages: Annotated[list, add_messages]
