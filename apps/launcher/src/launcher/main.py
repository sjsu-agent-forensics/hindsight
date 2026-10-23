"""Run the agent once under a hindsight session: register it, run the agent, end it.

The launcher generates the session ID and registers it with the interposer, with the
user's prompt as the only trusted input. It then runs the agent on that prompt, with the
agent's MCP server URLs pointing at the session's paths, and ends the session however
the agent exits. Arguments it does not recognize are passed to the agent, for example
``--provider anthropic``.
"""

import argparse
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import httpx

# The agent's config, whose URLs carry a {session_id} placeholder. Read as a file, not
# imported: apps never import each other.
_AGENT_CONFIG_TEMPLATE = Path(__file__).resolve().parents[3] / "agent" / "servers.toml"


def main() -> None:
    """Launch one agent run and exit with the agent's exit code."""
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Other arguments are passed to the agent (see: agent --help).",
    )
    parser.add_argument("prompt", help="the user's prompt for this run")
    parser.add_argument(
        "--gateway",
        default="http://127.0.0.1:8000",
        help="the interposer's base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--agent-config",
        type=Path,
        default=_AGENT_CONFIG_TEMPLATE,
        help="agent servers.toml with {session_id} placeholders (default: %(default)s)",
    )
    args, agent_args = parser.parse_known_args()
    sys.exit(_launch(args.prompt, args.gateway, args.agent_config, agent_args))


def _launch(prompt: str, gateway: str, template: Path, agent_args: list[str]) -> int:
    """Register a session, run the agent in it, end it, and return the agent's code."""
    session_id = str(uuid.uuid4())
    with httpx.Client(base_url=gateway, timeout=30) as http:
        _register(http, session_id, prompt)
        try:
            with tempfile.TemporaryDirectory() as config_dir:
                config = Path(config_dir) / "servers.toml"
                config.write_text(
                    template.read_text(encoding="utf-8").replace(
                        "{session_id}", session_id
                    ),
                    encoding="utf-8",
                )
                # "--" so a prompt starting with "-" is not read as an option.
                command = [
                    *[sys.executable, "-m", "agent.main"],
                    *["--config", str(config), *agent_args, "--", prompt],
                ]
                return subprocess.run(command).returncode
        finally:
            _end(http, session_id)


def _register(http: httpx.Client, session_id: str, prompt: str) -> None:
    """Register the session with the prompt as its only trusted input.

    Exits the launcher if the interposer is unreachable or refuses the registration,
    since the agent would be refused at startup anyway.
    """
    payload = {
        "session_id": session_id,
        "trusted_inputs": [{"kind": "user_prompt", "content": prompt}],
    }
    try:
        http.post("/sessions", json=payload).raise_for_status()
    except httpx.HTTPError as error:
        sys.exit(f"Could not register a session with the interposer: {error}")
    print(f"[hindsight session {session_id}]", file=sys.stderr)


def _end(http: httpx.Client, session_id: str) -> None:
    """End the session and print its MCP sessions to stderr.

    A failure is reported but not raised, so it does not replace the agent's exit code;
    the interposer's idle timeout ends the session instead.
    """
    try:
        response = http.delete(f"/sessions/{session_id}")
        response.raise_for_status()
    except httpx.HTTPError as error:
        print(f"Could not end session {session_id}: {error}", file=sys.stderr)
        return
    summary = response.json()
    print(
        f"[hindsight session {session_id} ended by {summary['ended_by']}]",
        file=sys.stderr,
    )
    for server, mcp_sessions in summary["mcp_sessions"].items():
        for mcp_session in mcp_sessions:
            print(
                f"  {server}: MCP session {mcp_session['mcp_session_id']}"
                f" (upstream {mcp_session['upstream_session_id']})",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
