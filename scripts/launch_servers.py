"""Start the HTTP MCP tool servers in one terminal and wait until each is listening.

Runs `uv run --project apps/mcp-servers/<name> <name>-server` for each server, prefixes every
line a server prints with its name, and stops them all on Ctrl-C or as soon as one of them exits.
`math-server` is not started: it speaks stdio and is spawned by the demo that uses it.

Usage, from the repo root:

    python scripts/launch_servers.py                # every server below
    python scripts/launch_servers.py records clock  # only these

Standard library only, so any Python 3.12+ runs it; it does not need the workspace environment.
This is a stand-in for the orchestrator, which will take over starting the servers.
"""

import argparse
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"

# Seconds to wait for a server's ports. The first start of a server also syncs its .venv, which
# can take a while, so this is generous.
START_TIMEOUT = 180


@dataclass(frozen=True)
class Server:
    """An HTTP tool server under apps/mcp-servers/<name>/."""

    name: str
    port: int
    control_port: int | None = None  # internal servers built on the kit; MCP port + 100

    @property
    def ports(self) -> list[int]:
        return [self.port] + ([self.control_port] if self.control_port else [])


# Every HTTP server, with its port from the internal tools plan. Add new servers here.
SERVERS = [
    Server("weather", 8001),
    Server("records", 8011, 8111),
    Server("email", 8012, 8112),
    Server("web", 8013, 8113),
    Server("files", 8014, 8114),
    Server("clock", 8015, 8115),
    Server("messaging", 8016, 8116),
    Server("calendar", 8017, 8117),
]


def main() -> None:
    """Start the chosen servers, report where they are, and wait for Ctrl-C."""
    by_name = {s.name: s for s in SERVERS}
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", metavar="server", help=", ".join(by_name))
    args = parser.parse_args()
    unknown = [n for n in args.names if n not in by_name]
    if unknown:
        parser.error(f"unknown server {', '.join(unknown)}; choose from {', '.join(by_name)}")
    chosen = [by_name[n] for n in args.names] if args.names else SERVERS

    # Refuse up front if a port is taken: the server would fail anyway, after a slower start.
    busy = [(s, p) for s in chosen for p in s.ports if _listening(p)]
    if busy:
        for s, p in busy:
            print(f"Port {p} ({s.name}) is already in use; is {s.name}-server already running?")
        sys.exit(1)

    processes: dict[str, subprocess.Popen] = {}
    interrupted = False
    try:
        for s in chosen:
            processes[s.name] = _start(s)
        _wait_until_ready(chosen, processes)
        _print_summary(chosen)
        # Serve until Ctrl-C or until any server exits on its own.
        while all(p.poll() is None for p in processes.values()):
            time.sleep(0.5)
        for name, p in processes.items():
            if p.returncode is not None:
                print(f"{name}-server exited with code {p.returncode}; stopping the others.")
    except KeyboardInterrupt:
        interrupted = True
        print("\nStopping servers...")
    finally:
        _stop(processes, grace=5 if interrupted else 0)


def _start(server: Server) -> subprocess.Popen:
    process = subprocess.Popen(
        ["uv", "run", "--project", f"apps/mcp-servers/{server.name}", f"{server.name}-server"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    prefix = f"[{server.name}]".ljust(10)
    threading.Thread(target=_relay, args=(process, prefix), daemon=True).start()
    return process


def _relay(process: subprocess.Popen, prefix: str) -> None:
    for line in process.stdout:
        # uv warns about this deprecated setting on some machines; it is noise here.
        if "UV_NATIVE_TLS" not in line:
            print(f"{prefix} {line}", end="", flush=True)


def _wait_until_ready(servers: list[Server], processes: dict[str, subprocess.Popen]) -> None:
    deadline = time.monotonic() + START_TIMEOUT
    pending = list(servers)
    while pending:
        for s in list(pending):
            if processes[s.name].poll() is not None:
                raise SystemExit(
                    f"{s.name}-server exited before it was ready; see its output above."
                )
            if all(_listening(p) for p in s.ports):
                pending.remove(s)
        if pending and time.monotonic() > deadline:
            names = ", ".join(s.name for s in pending)
            raise SystemExit(f"Timed out after {START_TIMEOUT}s waiting for: {names}.")
        time.sleep(0.2)


def _print_summary(servers: list[Server]) -> None:
    print("\nAll servers are ready (Ctrl-C to stop them):")
    for s in servers:
        line = f"  {s.name:<10} http://{HOST}:{s.port}/api/mcp"
        if s.control_port:
            line += f"   control http://{HOST}:{s.control_port}/control"
        print(line)
    print(flush=True)


def _stop(processes: dict[str, subprocess.Popen], grace: float) -> None:
    # Ctrl-C in a terminal already reaches every child, since they share its process group (and
    # its console on Windows), so after one give them `grace` seconds to exit cleanly first.
    deadline = time.monotonic() + grace
    for p in processes.values():
        if p.poll() is None and time.monotonic() < deadline:
            try:
                p.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
    for p in processes.values():
        if p.poll() is None:
            p.terminate()
    for p in processes.values():
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def _listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex((HOST, port)) == 0


if __name__ == "__main__":
    main()
