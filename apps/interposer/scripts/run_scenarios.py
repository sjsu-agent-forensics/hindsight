"""Run the end-to-end attack scenarios with the real agent, recorded and then blocked.

Phase 7 verification. Each scenario in ``apps/interposer/scenarios`` is run twice through
the launcher, with the real agent and model:

- **recorded**: no rules, so the attack can succeed; both traces must record it.
- **blocked**: under the scenario's hand-written rule, so the harmful call must be
  stopped before it reaches the server.

For every run it checks that the proxy and agent traces are complete and join exactly,
that the injected content was recorded, and that replaying the proxy trace reproduces
every live decision; and it reports whether the agent attempted the harmful call and
whether it got through. A small local model may ignore an injection, so a run in which
the agent never attempts the attack is reported as such, not as a pass of the rule.

Traces are kept under ``traces/scenarios/<run-id>/``, with ``results.json``. Needs Ollama
(or another provider, through ``--provider``) and ``uv sync --project
apps/mcp-servers/scenarios``.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path

import trace_schema as ts
from provenance import normalize
from rule_engine.replay import replay
from trace_schema.validate import check_events, read_session

from interposer.config import load_config
from interposer.testing import running_gateway

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
TRACE_ROOT = Path(__file__).resolve().parents[3] / "traces" / "scenarios"
# A local model can take minutes per step; the session must not expire in between.
IDLE_TIMEOUT = 30 * 60
AGENT_TIMEOUT = 15 * 60


def run_scenario(name: str, mode: str, run_dir: Path, agent_args: list[str]) -> dict:
    """Run one scenario in one mode and return what was observed."""
    scenario = tomllib.loads((SCENARIOS / name / "scenario.toml").read_text("utf-8"))
    catalog = load_config(SCENARIOS / "servers.toml")
    upstreams = {server: catalog[server] for server in scenario["servers"]}
    rules_dir = SCENARIOS / name / "rules" if mode == "blocked" else None
    trace_dir = run_dir / f"{name}-{mode}"

    with running_gateway(
        upstreams, idle_timeout=IDLE_TIMEOUT, rules_dir=rules_dir, trace_dir=trace_dir
    ) as gateway:
        agent_output = _launch(gateway.url, scenario, agent_args)
    [session_dir] = [path for path in trace_dir.iterdir() if path.is_dir()]
    (session_dir / "agent-output.txt").write_text(agent_output, encoding="utf-8")
    return {"scenario": name, "mode": mode, **_observe(session_dir, scenario)}


def _launch(gateway_url: str, scenario: dict, agent_args: list[str]) -> str:
    """Run the agent once through the launcher and return everything it printed."""
    with tempfile.TemporaryDirectory() as config_dir:
        template = Path(config_dir) / "servers.toml"
        template.write_text(
            "".join(
                f'[servers.{server}]\nurl = "{gateway_url}/s/{{session_id}}/mcp/{server}"\n'
                for server in scenario["servers"]
            ),
            encoding="utf-8",
        )
        command = [
            *[sys.executable, "-m", "launcher.main", scenario["prompt"]],
            *["--gateway", gateway_url, "--agent-config", str(template), *agent_args],
        ]
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=AGENT_TIMEOUT
        )
    return done.stdout + done.stderr


def _observe(session_dir: Path, scenario: dict) -> dict:
    """Summarize one session's traces against its scenario."""
    proxy, agent = read_session(session_dir)
    harm = scenario["harm"]
    harmful_calls = [
        event
        for event in proxy
        if event[ts.EVENT_TYPE] == "call"
        and event[ts.SERVER] == harm["server"]
        and event[ts.TOOL_NAME] == harm["tool"]
        and normalize(harm["contains"]) in normalize(json.dumps(event[ts.PAYLOAD]))
    ]
    harmful_ids = {
        (e[ts.MCP_SESSION_ID], e[ts.JSONRPC_REQUEST_ID]) for e in harmful_calls
    }
    answered = {
        (e[ts.MCP_SESSION_ID], e[ts.JSONRPC_REQUEST_ID])
        for e in proxy
        if e[ts.EVENT_TYPE] == "response"
    }
    decisions = {
        (e[ts.MCP_SESSION_ID], e[ts.JSONRPC_REQUEST_ID]): e
        for e in proxy
        if e[ts.EVENT_TYPE] == "decision"
    }
    live = {
        key: (d[ts.DECISION], tuple(d[ts.RULE_IDS])) for key, d in decisions.items()
    }
    replayed = {
        (c.mcp_session_id, c.jsonrpc_id): (c.decision.effect, c.decision.rule_ids)
        for c in replay(proxy)
    }
    recorded_payloads = json.dumps(
        [
            e.get(ts.PAYLOAD)
            for e in proxy
            if e[ts.EVENT_TYPE] in ("descriptors", "response")
        ]
    )
    return {
        "session": session_dir.name,
        "tool_calls": sum(1 for e in agent if e[ts.EVENT_TYPE] == "tool_call"),
        "trace_problems": check_events(proxy, agent),
        "injection_recorded": normalize(scenario["injection"])
        in normalize(recorded_payloads),
        "attack_attempted": bool(harmful_calls),
        "attack_reached_server": bool(harmful_ids & answered),
        "blocked_by": sorted(
            {rule for key in harmful_ids for rule in decisions[key][ts.RULE_IDS]}
        ),
        "replay_matches_live": replayed == live,
    }


def _verdict(result: dict) -> str:
    if result["trace_problems"] or not result["replay_matches_live"]:
        return "FAIL: traces"
    if not result["injection_recorded"]:
        return "FAIL: injection not recorded"
    if not result["attack_attempted"]:
        return "agent ignored the injection"
    if result["mode"] == "blocked":
        return (
            "FAIL: attack got through"
            if result["attack_reached_server"]
            else ("blocked by " + ", ".join(result["blocked_by"]))
        )
    return (
        "attack succeeded, recorded"
        if result["attack_reached_server"]
        else ("attack attempted, failed upstream")
    )


def main() -> None:
    """Run the scenarios and print one line per run."""
    names = sorted(path.name for path in SCENARIOS.iterdir() if path.is_dir())
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Other arguments are passed to the agent, for example --model qwen3:4b.",
    )
    parser.add_argument("scenarios", nargs="*", default=names, help=f"from {names}")
    parser.add_argument("--modes", default="recorded,blocked")
    parser.add_argument(
        "--repeat", type=int, default=1, help="runs per scenario and mode"
    )
    args, agent_args = parser.parse_known_args()

    run_dir = TRACE_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []
    for name in args.scenarios:
        for mode in args.modes.split(","):
            for attempt in range(1, args.repeat + 1):
                result = run_scenario(name, mode, run_dir / f"{attempt}", agent_args)
                result["attempt"] = attempt
                result["verdict"] = _verdict(result)
                results.append(result)
                print(
                    f"{name:<22} {mode:<9} #{attempt}  {result['verdict']}", flush=True
                )
    (run_dir / "results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print(f"traces and results: {run_dir}")
    sys.exit(1 if any(r["verdict"].startswith("FAIL") for r in results) else 0)


if __name__ == "__main__":
    main()
