"""Unit tests for public tool session sandboxing and reseedability."""

from pathlib import Path

from public_bridge.sandbox import SessionSandboxManager


def test_sandbox_creation_and_fixtures(tmp_path: Path) -> None:
    """Test creating an isolated session sandbox with initial fixture files."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    (fixtures_dir / "sample.txt").write_text("initial data", encoding="utf-8")

    mgr = SessionSandboxManager(
        server_name="test_tool",
        base_dir=tmp_path / "sandboxes",
        fixture_dir=fixtures_dir,
    )

    box = mgr.get_or_create("session_1")
    assert box.sandbox_dir.exists()
    assert (box.sandbox_dir / "sample.txt").exists()
    assert (box.sandbox_dir / "sample.txt").read_text(
        encoding="utf-8"
    ) == "initial data"


def test_sandbox_reset(tmp_path: Path) -> None:
    """Test resetting a mutated sandbox restores clean fixture state (E-10, N-06)."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    (fixtures_dir / "base.txt").write_text("clean state", encoding="utf-8")

    mgr = SessionSandboxManager(
        server_name="test_tool",
        base_dir=tmp_path / "sandboxes",
        fixture_dir=fixtures_dir,
    )

    box = mgr.get_or_create("session_2")
    # Simulate attacker or agent mutating the sandbox
    (box.sandbox_dir / "base.txt").write_text("poisoned state", encoding="utf-8")
    (box.sandbox_dir / "injected_file.txt").write_text("attack", encoding="utf-8")

    # Reseed session
    reset_box = mgr.reset("session_2")
    assert (reset_box.sandbox_dir / "base.txt").read_text(
        encoding="utf-8"
    ) == "clean state"
    assert not (reset_box.sandbox_dir / "injected_file.txt").exists()


def test_sandbox_effects_and_discard(tmp_path: Path) -> None:
    """Test effect logging and session discarding."""
    mgr = SessionSandboxManager(
        server_name="test_tool",
        base_dir=tmp_path / "sandboxes",
    )

    mgr.record_effect("session_3", "write_file", path="a.txt", bytes=10)
    box = mgr.lookup("session_3")
    assert box is not None
    assert len(box.effects) == 1
    assert box.effects[0]["action"] == "write_file"

    assert mgr.discard("session_3") is True
    assert mgr.lookup("session_3") is None
    assert not box.sandbox_dir.exists()
