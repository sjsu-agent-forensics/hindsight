"""Check that a real session's proxy and agent traces are complete and join exactly.

Run on a session's trace directory after a launcher run; the launcher prints it when the
session ends. The checks themselves are ``trace_schema.validate``'s. Also reports model
steps that asked for several tools at once, and whether those calls were in flight at
the proxy at the same time.

Exits 0 when every check passes, 1 otherwise.
"""

import argparse
import sys
from pathlib import Path

import trace_schema as ts
from trace_schema.validate import check_events, parallel_steps, read_session


def main() -> None:
    """Check one session's trace directory."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trace_dir", type=Path, help="the session's trace directory")
    args = parser.parse_args()

    proxy, agent = read_session(args.trace_dir)
    problems = check_events(proxy, agent)
    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        calls = sum(1 for e in agent if e[ts.EVENT_TYPE] == "tool_call")
        print(f"PASS: {calls} tool calls; traces complete and joined exactly")
    for ids, overlapped in parallel_steps(proxy, agent):
        print(
            f"  parallel step: {len(ids)} tool calls, "
            f"{'in flight together' if overlapped else 'not overlapping'} at the proxy"
        )
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
