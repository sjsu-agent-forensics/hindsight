"""Unit and contract tests for the public tool control endpoint (/control)."""

from pathlib import Path
from starlette.testclient import TestClient

from public_bridge.control import control_app
from public_bridge.sandbox import SessionSandboxManager


def test_control_endpoints(tmp_path: Path) -> None:
    """Test the full control protocol: health, list, seed, effects, and discard."""
    mgr = SessionSandboxManager(
        server_name="mock_tool", base_dir=tmp_path / "sandboxes"
    )
    app = control_app(
        server_name="mock_tool",
        sandbox_manager=mgr,
        is_alive_fn=lambda: True,
    )

    client = TestClient(app)

    # 1. Health check
    resp = client.get("/control/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "server": "mock_tool",
        "status": "ok",
        "upstream_alive": True,
    }

    # 2. List sessions when empty
    resp = client.get("/control/sessions")
    assert resp.status_code == 200
    assert resp.json() == {"server": "mock_tool", "sessions": []}

    # 3. Seed session
    resp = client.post("/control/sessions/exp_session_42")
    assert resp.status_code == 201
    data = resp.json()
    assert data["session"] == "exp_session_42"
    assert data["status"] == "seeded"
    assert "sandbox" in data

    # 4. List sessions reflects seeded session
    resp = client.get("/control/sessions")
    assert "exp_session_42" in resp.json()["sessions"]

    # 5. Record effect and read back effects log
    mgr.record_effect("exp_session_42", "fetch", url="http://127.0.0.1")
    resp = client.get("/control/sessions/exp_session_42/effects")
    assert resp.status_code == 200
    effects = resp.json()["effects"]
    assert len(effects) == 1
    assert effects[0]["action"] == "fetch"

    # 6. Discard session
    resp = client.delete("/control/sessions/exp_session_42")
    assert resp.status_code == 204

    # 7. Discard non-existent session returns 404
    resp = client.delete("/control/sessions/exp_session_42")
    assert resp.status_code == 404
