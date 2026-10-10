"""Measure per-call latency direct to a server and through the interposer.

Phase 1 exit check. Calls one tool repeatedly over a single persistent MCP session to
each URL and reports the median and 95th percentile, then the median the interposer adds
per call. The earlier baseline was about 6 ms median per call with a persistent session.
"""

import argparse
import asyncio
import json
import statistics
import time
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def _measure(
    url: str, tool: str, arguments: dict[str, Any], calls: int, warmup: int
) -> list[float]:
    """Return each timed call's latency in milliseconds, after untimed warmup calls.

    Raises:
        RuntimeError: The tool returned an error result.
    """

    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for _ in range(warmup):
                await session.call_tool(tool, arguments)
            latencies = []
            for _ in range(calls):
                start = time.perf_counter()
                result = await session.call_tool(tool, arguments)
                latencies.append((time.perf_counter() - start) * 1000)
                if result.isError:
                    raise RuntimeError(f"{tool} returned an error via {url}")
            return latencies


def _summary(label: str, latencies: list[float]) -> str:
    p95 = statistics.quantiles(latencies, n=20)[-1]
    return (
        f"{label:<11} median {statistics.median(latencies):7.2f} ms   p95 {p95:7.2f} ms"
    )


async def _run(args: argparse.Namespace) -> None:
    arguments = json.loads(args.arguments)
    direct = await _measure(
        args.direct_url, args.tool, arguments, args.calls, args.warmup
    )
    proxied = await _measure(
        args.proxy_url, args.tool, arguments, args.calls, args.warmup
    )
    added = statistics.median(proxied) - statistics.median(direct)
    print(f"{args.calls} calls to {args.tool} each, after {args.warmup} warmup calls")
    print(_summary("direct", direct))
    print(_summary("interposer", proxied))
    print(f"added by the interposer: {added:+.2f} ms median per call")


def main() -> None:
    """Benchmark one tool direct and through the interposer."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("direct_url", help="the upstream server's own URL")
    parser.add_argument("proxy_url", help="the same server through the interposer")
    parser.add_argument("tool", help="name of the tool to call")
    parser.add_argument(
        "--arguments", default="{}", help="tool arguments as JSON (default: {})"
    )
    parser.add_argument("--calls", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
