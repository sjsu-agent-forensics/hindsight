"""Run the ReAct agent against its MCP servers, all reached through the interposer."""

import argparse
import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from typing import get_args

from hindsight.llm import Provider, get_llm
from hindsight.prompts import sys_prompt
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph

from agent.config import load_servers
from agent.console import stream_turn
from agent.tools import open_mcp_tools

_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "servers.toml"


def main() -> None:
    """Answer one prompt given on the command line, or chat until "exit" or "quit"."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "prompt", nargs="?", help="answer this one prompt and exit (default: chat)"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help="TOML file listing the MCP servers (default: %(default)s)",
    )
    parser.add_argument("--provider", choices=get_args(Provider), default="ollama")
    parser.add_argument("--model", help="model name (default: the provider's default)")
    args = parser.parse_args()
    asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> None:
    async with AsyncExitStack() as stack:
        tools = await open_mcp_tools(stack, load_servers(args.config))
        llm = get_llm(args.provider, model=args.model, reasoning=True)
        # The standard LangChain agent loop, so results apply to a typical agent rather
        # than a custom one. MCP tools are async-only, so it is always run with astream.
        agent = create_agent(llm, tools, system_prompt=sys_prompt())
        if args.prompt:
            await stream_turn(agent, [HumanMessage(args.prompt)])
            print()
        else:
            await _chat(agent)


async def _chat(agent: CompiledStateGraph) -> None:
    """Chat until the user types "exit" or "quit".

    The conversation history carries over between turns. If a turn fails to finish, the
    history from before that turn is kept.
    """
    messages = []
    while True:
        user_message = await asyncio.to_thread(input, "User message: ")
        if not user_message:
            continue
        if user_message.lower() in ["exit", "quit"]:
            print("Exiting...")
            break

        final_state = await stream_turn(
            agent, [*messages, HumanMessage(content=user_message)]
        )
        if final_state is not None:
            messages = final_state["messages"]
        print("\n\n---\n")
