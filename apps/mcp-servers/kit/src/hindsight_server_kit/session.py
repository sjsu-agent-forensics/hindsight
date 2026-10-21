"""Per-session state for a tool server, picked by the `Hindsight-Session` request header.

A request that carries the header gets that session's state, which must have been seeded through
the control endpoint first. A request without it -- a quick manual run, `fastmcp call`, an agent
config with no header -- gets one default state, loaded on first use. Resetting the default state
means restarting the server.
"""

import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from random import Random
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers

SESSION_HEADER = "Hindsight-Session"

# Scenario time and random seed for any session that does not set its own. The time is fixed
# rather than the wall clock so that a rerun from reset returns identical responses (E-10, E-13).
DEFAULT_TIME = "2026-11-06T09:00:00-08:00"
DEFAULT_SEED = 295

# A server's fixture loader: given the fixture file for a session, or None for the server's
# benign default, return whatever that server's tools read from `Session.data`.
FixtureLoader = Callable[[Path | None], Any]


@dataclass
class Session:
    """One run's state on one server.

    `data` is whatever the server's fixture loader returned and is private to this session.
    `lock` serialises tool calls within the session, since sync tools run on worker threads.
    """

    id: str | None
    data: Any
    now: datetime
    seed: int
    fixture: Path | None
    random: Random = field(init=False)
    effects: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self) -> None:
        self.random = Random(self.seed)

    def record_effect(self, tool: str, **details: Any) -> dict[str, Any]:
        """Append what a call changed to this session's effect log and return the entry.

        The log is append-only. It is the ground truth for what the agent's calls changed,
        and what the orchestrator reads back after the run (E-03).
        """
        effect = {"seq": len(self.effects) + 1, "tool": tool, "at": self.now.isoformat(), **details}
        self.effects.append(effect)
        return effect

    def describe(self) -> dict[str, Any]:
        """Return the session's settings as JSON-ready values."""
        return {
            "session": self.id,
            "fixture": str(self.fixture) if self.fixture else None,
            "time": self.now.isoformat(),
            "seed": self.seed,
        }


class SessionStore:
    """All sessions of one server, plus its default state.

    Build one at module level in each server, so that the `fastmcp` CLI, which imports the module
    and never calls `main()`, still finds the fixture loader.
    """

    def __init__(self, name: str, load_fixture: FixtureLoader | None = None) -> None:
        self.name = name
        self._load_fixture = load_fixture or (lambda path: None)
        self._sessions: dict[str, Session] = {}
        self._default: Session | None = None
        self._lock = threading.Lock()

    def get(self) -> Session:
        """Return the session the current request belongs to.

        Raises a ToolError, which reaches the agent as an MCP error, when the header names a
        session that was never seeded. Falling back to the default state there would let a run
        silently read another run's data (N-04).
        """
        sid = get_http_headers().get(SESSION_HEADER.lower())
        if sid:
            with self._lock:
                session = self._sessions.get(sid)
            if session is None:
                raise ToolError(
                    f"Session {sid!r} has not been set up on the {self.name} server; "
                    f"seed it with POST /control/sessions/{sid} on its control port first."
                )
            return session
        return self.default()

    def default(self) -> Session:
        """Return the default state, loading it on first use.

        It reads `HINDSIGHT_FIXTURE`, `HINDSIGHT_TIME` and `HINDSIGHT_SEED` from the environment,
        each falling back to the server's benign fixture, `DEFAULT_TIME` and `DEFAULT_SEED`.
        """
        with self._lock:
            if self._default is None:
                fixture = os.environ.get("HINDSIGHT_FIXTURE")
                self._default = self._build(
                    None,
                    fixture=Path(fixture) if fixture else None,
                    time=os.environ.get("HINDSIGHT_TIME"),
                    seed=os.environ.get("HINDSIGHT_SEED"),
                )
            return self._default

    def seed(self, sid: str, spec: dict[str, Any] | None = None) -> Session:
        """Create session `sid` from a seed request, replacing any session already under it.

        `spec` takes optional keys: `fixture`, a path to this server's fixture file; `scenario`,
        a scenario id resolved to `<HINDSIGHT_SCENARIOS>/<scenario>/<server>.json`, where a missing
        file means the scenario leaves this server on its benign fixture; `time`, the ISO 8601
        scenario time; and `seed`, the integer seed for the session's random source. Replacing an
        existing session is how a session is reset.
        """
        spec = spec or {}
        fixture = spec.get("fixture")
        if fixture is None and spec.get("scenario"):
            candidate = self.scenarios_dir() / str(spec["scenario"]) / f"{self.name}.json"
            fixture = candidate if candidate.exists() else None
        session = self._build(
            sid,
            fixture=Path(fixture) if fixture else None,
            time=spec.get("time"),
            seed=spec.get("seed"),
        )
        with self._lock:
            self._sessions[sid] = session
        return session

    def lookup(self, sid: str) -> Session | None:
        """Return seeded session `sid`, or None."""
        with self._lock:
            return self._sessions.get(sid)

    def discard(self, sid: str) -> bool:
        """Drop session `sid` and its effect log; return whether it existed."""
        with self._lock:
            return self._sessions.pop(sid, None) is not None

    def session_ids(self) -> list[str]:
        """Return the ids of every seeded session."""
        with self._lock:
            return sorted(self._sessions)

    @staticmethod
    def scenarios_dir() -> Path:
        """Return the directory scenario ids resolve against (`HINDSIGHT_SCENARIOS`, else ./scenarios)."""
        return Path(os.environ.get("HINDSIGHT_SCENARIOS", "scenarios"))

    def _build(
        self, sid: str | None, *, fixture: Path | None, time: str | None, seed: Any
    ) -> Session:
        if fixture is not None and not fixture.exists():
            raise FileNotFoundError(f"Fixture {fixture} does not exist.")
        now = datetime.fromisoformat(time or DEFAULT_TIME)
        if now.tzinfo is None:
            raise ValueError(f"Scenario time {time!r} needs a UTC offset, e.g. -08:00.")
        return Session(
            id=sid,
            data=self._load_fixture(fixture),
            now=now,
            seed=int(seed) if seed is not None else DEFAULT_SEED,
            fixture=fixture,
        )


def read_json(path: Path) -> Any:
    """Read a JSON fixture file as UTF-8, whatever the platform's default encoding."""
    return json.loads(path.read_text(encoding="utf-8"))
