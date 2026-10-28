"""Start the public MCP tool server bridges in one terminal and wait until ready.

Runs `uv run --project apps/mcp-servers/public-bridge public-bridge <name>` for each configured
server, prefixes every line printed with the server name, and stops them all on Ctrl-C.

Usage, from the repo root:

    python scripts/launch_public_servers.py         # every configured public server
    python scripts/launch_public_servers.py mock    # only the mock server

Standard library only, so any Python 3.12+ runs it without extra dependencies.
"""

import argparse
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
START_TIMEOUT = 60
CONFIG_PATH = REPO_ROOT / "public_servers.toml"


def _load_configured_servers() -> dict[str, dict[str, int]]:
    """Read server names and ports from public_servers.toml."""
    if not CONFIG_PATH.exists():
        sys.exit(f"Configuration file not found: {CONFIG_PATH}")
    with CONFIG_PATH.open("rb") as f:
        data = tomllib.load(f)
    servers = {}
    for name, entry in data.get("public_servers", {}).items():
        if isinstance(entry, dict):
            port = int(entry.get("port", 8021))
            ctrl = int(entry.get("control_port", port + 100))
            servers[name] = {"port": port, "control_port": ctrl}
    return servers


def main() -> None:
    """Start the selected public servers, report status, and await termination."""
    all_servers = _load_configured_servers()
    if not all_servers:
        sys.exit(f"No public servers defined in {CONFIG_PATH}")

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "names",
        nargs="*",
        metavar="server",
        help=", ".join(all_servers.keys()),
    )
    args = parser.parse_args()

    unknown = [n for n in args.names if n not in all_servers]
    if unknown:
        parser.error(
            f"unknown server {', '.join(unknown)}; choose from {', '.join(all_servers.keys())}"
        )

    chosen_names = args.names if args.names else list(all_servers.keys())
    chosen = {name: all_servers[name] for name in chosen_names}

    # Verify ports before spawning
    busy = []
    for name, info in chosen.items():
        if _listening(info["port"]):
            busy.append((name, info["port"], "MCP"))
        if _listening(info["control_port"]):
            busy.append((name, info["control_port"], "Control"))

    if busy:
        for name, port, role in busy:
            print(
                f"Port {port} ({name} {role}) is already in use; is a server already running?",
                file=sys.stderr,
            )
        sys.exit(1)

    processes: dict[str, subprocess.Popen[str]] = {}
    interrupted = False
    try:
        for name in chosen:
            processes[name] = _start_server(name)
        _wait_until_ready(chosen, processes)
        _print_summary(chosen)

        while all(p.poll() is None for p in processes.values()):
            time.sleep(0.5)

        for name, p in processes.items():
            if p.returncode is not None:
                print(
                    f"public-bridge ({name}) exited with code {p.returncode}; stopping the others."
                )
    except KeyboardInterrupt:
        interrupted = True
        print("\nStopping public MCP server bridges...")
    finally:
        _stop_processes(processes, grace=5 if interrupted else 0)


def _start_server(name: str) -> subprocess.Popen[str]:
    """Launch public-bridge process for the specified server name."""
    cmd = [
        "uv",
        "run",
        "--project",
        "apps/mcp-servers/public-bridge",
        "public-bridge",
        name,
        "--config",
        str(CONFIG_PATH),
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    prefix = f"[public:{name}]".ljust(16)
    threading.Thread(target=_relay_logs, args=(proc, prefix), daemon=True).start()
    return proc


def _relay_logs(proc: subprocess.Popen[str], prefix: str) -> None:
    """Relay stdout lines with a server prefix."""
    if proc.stdout is None:
        return
    for line in proc.stdout:
        if "UV_NATIVE_TLS" not in line:
            print(f"{prefix} {line}", end="", flush=True)


def _wait_until_ready(
    servers: dict[str, dict[str, int]], processes: dict[str, subprocess.Popen[str]]
) -> None:
    """Poll listeners until all ports are open or startup times out."""
    deadline = time.monotonic() + START_TIMEOUT
    pending = list(servers.keys())

    while pending:
        for name in list(pending):
            if processes[name].poll() is not None:
                raise SystemExit(
                    f"public-bridge ({name}) exited before it was ready; see output above."
                )
            ports = [servers[name]["port"], servers[name]["control_port"]]
            if all(_listening(p) for p in ports):
                pending.remove(name)

        if pending and time.monotonic() > deadline:
            names = ", ".join(pending)
            raise SystemExit(f"Timed out after {START_TIMEOUT}s waiting for: {names}")
        time.sleep(0.2)


def _print_summary(servers: dict[str, dict[str, int]]) -> None:
    """Print connection information for all running public server bridges."""
    print("\nAll public MCP tool bridges are ready (Ctrl-C to stop):")
    for name, info in servers.items():
        line = f"  {name:<12} http://{HOST}:{info['port']}/api/mcp"
        line += f"   control: http://{HOST}:{info['control_port']}/control"
        print(line)
    print(flush=True)


def _stop_processes(processes: dict[str, subprocess.Popen[str]], grace: float) -> None:
    """Terminate all running server processes."""
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
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()


def _listening(port: int) -> bool:
    """Check if a port on loopback is currently listening."""
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex((HOST, port)) == 0


if __name__ == "__main__":
    main()
