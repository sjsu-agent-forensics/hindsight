"""Chat model factory."""

from langchain_ollama import ChatOllama


def get_llm(
    model: str = "gemma4",
    base_url: str = "http://localhost:11434",
    reasoning: bool | None = False,
) -> ChatOllama:
    """Create a chat model served by Ollama.

    Args:
        model: Ollama model name. Must already be pulled on the Ollama server.
        reasoning: Whether the model thinks before answering. True returns the thinking
            separately from the answer; False disables thinking; None uses the model's own
            default. With gemma4, False sometimes yields an empty final answer after tool
            calls.
    """
    return ChatOllama(model=model, base_url=base_url, verbose=True, reasoning=reasoning)
