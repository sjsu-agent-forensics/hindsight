"""Chat model factory."""

from typing import Literal

from langchain.chat_models import BaseChatModel
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

Provider = Literal["ollama", "anthropic", "openai"]

_DEFAULT_MODELS: dict[Provider, str] = {
    "ollama": "gemma4",
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-5-mini",
}


def get_llm(
    provider: Provider = "ollama",
    model: str | None = None,
    base_url: str | None = None,
    reasoning: bool | None = False,
) -> BaseChatModel:
    """Create a chat model from Ollama, Anthropic, or OpenAI.

    Anthropic and OpenAI read their API keys from the ``ANTHROPIC_API_KEY`` and
    ``OPENAI_API_KEY`` environment variables.

    Args:
        provider: Service that hosts the model.
        model: Model name. If None, uses the provider's default from ``_DEFAULT_MODELS``.
            For Ollama, the model must already be pulled on the Ollama server.
        base_url: Server URL. If None, uses the provider's default (``localhost:11434``
            for Ollama, the public API otherwise).
        reasoning: Whether the model thinks before answering. True returns the thinking
            separately from the answer; False disables thinking; None uses the model's own
            default. With gemma4, False sometimes yields an empty final answer after tool
            calls. OpenAI ignores this flag: its reasoning models always reason.

    Raises:
        ValueError: ``provider`` is not one of the supported providers.
    """
    model = model or _DEFAULT_MODELS.get(provider)
    match provider:
        case "ollama":
            return _get_ollama_llm(model, base_url, reasoning)
        case "anthropic":
            return _get_anthropic_llm(model, base_url, reasoning)
        case "openai":
            return ChatOpenAI(model=model, base_url=base_url, verbose=True)
        case _:
            raise ValueError(
                f"Unknown LLM provider {provider!r}; expected one of {list(_DEFAULT_MODELS)}"
            )


def _get_ollama_llm(
    model: str, base_url: str | None, reasoning: bool | None
) -> ChatOllama:
    return ChatOllama(
        model=model,
        base_url=base_url or "http://localhost:11434",
        verbose=True,
        reasoning=reasoning,
    )


def _get_anthropic_llm(
    model: str, base_url: str | None, reasoning: bool | None
) -> ChatAnthropic:
    # None leaves thinking unset so the model's own default applies.
    thinking = None
    if reasoning is not None:
        thinking = {"type": "adaptive"} if reasoning else {"type": "disabled"}
    return ChatAnthropic(
        model=model, base_url=base_url, thinking=thinking, verbose=True
    )
