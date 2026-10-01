"""System prompt and initial message history for the demo agents."""

from langchain_core.messages import BaseMessage, SystemMessage


def sys_prompt() -> SystemMessage:
    """Return the default system prompt."""
    return SystemMessage(
        content="""You are a helpful assistant that can reason and answer questions.
    If you don't know the answer, you should say "I don't know"."""
    )


def init_messages() -> list[BaseMessage]:
    """Return a new message history containing only the system prompt."""
    return [sys_prompt()]
