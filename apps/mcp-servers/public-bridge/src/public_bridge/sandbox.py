"""Session sandboxing and resettable state management for public MCP tools.

Manages per-session isolated directories, initial fixture seeding, effect recording, and
reseedability (E-10, N-06) for third-party public MCP tools that interact with local state.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any

_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass
class SessionSandbox:
    """An isolated environment and state container for a single hindsight session."""

    session_id: str
    sandbox_dir: Path
    created_at: float = field(default_factory=time.time)
    effects: list[dict[str, Any]] = field(default_factory=list)


class SessionSandboxManager:
    """Manages ephemeral sandboxes for public MCP tool sessions.

    Provides isolated directories and reset capabilities so experiments have deterministic
    starting points without leaking state or touching real host data.
    """

    def __init__(
        self,
        server_name: str,
        base_dir: Path | None = None,
        fixture_dir: Path | None = None,
    ) -> None:
        self.server_name = server_name
        self.fixture_dir = fixture_dir
        self.base_dir = (
            base_dir
            if base_dir is not None
            else Path(tempfile.gettempdir()) / "hindsight_sandboxes" / server_name
        )
        self._sessions: dict[str, SessionSandbox] = {}

    def get_or_create(self, session_id: str) -> SessionSandbox:
        """Retrieve the sandbox for the given session ID, creating it if needed."""
        _validate_session_id(session_id)
        if session_id in self._sessions:
            return self._sessions[session_id]

        target_dir = self.base_dir / session_id
        target_dir.mkdir(parents=True, exist_ok=True)

        if self.fixture_dir and self.fixture_dir.exists():
            _copy_fixtures(self.fixture_dir, target_dir)

        sandbox = SessionSandbox(session_id=session_id, sandbox_dir=target_dir)
        self._sessions[session_id] = sandbox
        return sandbox

    def reset(
        self, session_id: str, custom_fixture_dir: Path | None = None
    ) -> SessionSandbox:
        """Reset a session sandbox to its clean initial fixture state.

        Args:
            session_id: The session identifier to reseed.
            custom_fixture_dir: Optional alternative fixture directory to seed from.

        Returns:
            The reset SessionSandbox instance.
        """
        _validate_session_id(session_id)
        target_dir = self.base_dir / session_id
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)

        seed_dir = custom_fixture_dir or self.fixture_dir
        if seed_dir and seed_dir.exists():
            _copy_fixtures(seed_dir, target_dir)

        sandbox = SessionSandbox(
            session_id=session_id,
            sandbox_dir=target_dir,
            created_at=time.time(),
            effects=[],
        )
        self._sessions[session_id] = sandbox
        return sandbox

    def discard(self, session_id: str) -> bool:
        """Clean up and remove a session sandbox.

        Returns:
            True if the session existed and was removed, False otherwise.
        """
        _validate_session_id(session_id)
        sandbox = self._sessions.pop(session_id, None)
        if sandbox is None:
            return False

        if sandbox.sandbox_dir.exists():
            shutil.rmtree(sandbox.sandbox_dir, ignore_errors=True)
        return True

    def session_ids(self) -> list[str]:
        """Return the list of active session IDs."""
        return list(self._sessions.keys())

    def lookup(self, session_id: str) -> SessionSandbox | None:
        """Look up a session without creating it."""
        return self._sessions.get(session_id)

    def record_effect(self, session_id: str, action: str, **details: Any) -> None:
        """Record an observed effect in the session's effect log."""
        sandbox = self.get_or_create(session_id)
        record = {
            "timestamp": time.time(),
            "action": action,
            **details,
        }
        sandbox.effects.append(record)

    def cleanup_all(self) -> None:
        """Remove all managed session sandboxes on server teardown."""
        for sid in list(self._sessions.keys()):
            self.discard(sid)
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir, ignore_errors=True)


def _copy_fixtures(source: Path, destination: Path) -> None:
    """Copy all files and subdirectories from source to destination."""
    for item in source.iterdir():
        dest_item = destination / item.name
        if item.is_dir():
            shutil.copytree(item, dest_item, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest_item)


def _validate_session_id(session_id: str) -> None:
    """Reject session IDs that could escape the sandbox root or form unsafe paths."""
    if not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError("Session ID must contain 1-128 letters, digits, '_' or '-'.")
