"""Control endpoint for public MCP tools: seeds, resets, inspects, and discards sessions.

Conforms to the hindsight control endpoint contract on port + 100, isolated from the agent's
execution path so that experiments have a resettable starting point (E-10, N-06).
"""

from json import JSONDecodeError
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from public_bridge.sandbox import SessionSandboxManager


def control_app(
    server_name: str,
    sandbox_manager: SessionSandboxManager,
    is_alive_fn: Any | None = None,
) -> Starlette:
    """Build the control API for a public MCP tool server bridge.

    Args:
        server_name: Name of the public tool server.
        sandbox_manager: Session sandbox manager for this server.
        is_alive_fn: Optional callable returning True if the underlying server/process is alive.

    Returns:
        Starlette application exposing /control endpoints.
    """

    async def health(_request: Request) -> Response:
        alive = is_alive_fn() if is_alive_fn else True
        return JSONResponse(
            {
                "server": server_name,
                "status": "ok" if alive else "degraded",
                "upstream_alive": alive,
            }
        )

    async def list_sessions(_request: Request) -> Response:
        return JSONResponse(
            {"server": server_name, "sessions": sandbox_manager.session_ids()}
        )

    async def seed_session(request: Request) -> Response:
        sid = request.path_params["sid"]
        custom_fixture: Path | None = None
        if await request.body():
            try:
                body = await request.json()
            except JSONDecodeError as err:
                return JSONResponse(
                    {"error": f"Body is not JSON: {err}"}, status_code=400
                )
            if not isinstance(body, dict):
                return JSONResponse(
                    {"error": "Body must be a JSON object."}, status_code=400
                )
            if "fixture" in body and isinstance(body["fixture"], str):
                custom_fixture = Path(body["fixture"])

        try:
            sandbox = sandbox_manager.reset(sid, custom_fixture_dir=custom_fixture)
        except Exception as exc:
            return JSONResponse(
                {"error": f"{type(exc).__name__}: {exc}"}, status_code=400
            )

        return JSONResponse(
            {
                "session": sid,
                "server": server_name,
                "status": "seeded",
                "sandbox": str(sandbox.sandbox_dir),
                "created_at": sandbox.created_at,
            },
            status_code=201,
        )

    async def session_effects(request: Request) -> Response:
        sid = request.path_params["sid"]
        sandbox = sandbox_manager.lookup(sid)
        if sandbox is None:
            return JSONResponse({"error": f"No session {sid!r}."}, status_code=404)
        return JSONResponse(
            {
                "session": sid,
                "server": server_name,
                "effects": list(sandbox.effects),
                "sandbox": str(sandbox.sandbox_dir),
            }
        )

    async def discard_session(request: Request) -> Response:
        sid = request.path_params["sid"]
        if not sandbox_manager.discard(sid):
            return JSONResponse({"error": f"No session {sid!r}."}, status_code=404)
        return Response(status_code=204)

    return Starlette(
        routes=[
            Route("/control/health", health, methods=["GET"]),
            Route("/control/sessions", list_sessions, methods=["GET"]),
            Route("/control/sessions/{sid}", seed_session, methods=["POST"]),
            Route("/control/sessions/{sid}", discard_session, methods=["DELETE"]),
            Route("/control/sessions/{sid}/effects", session_effects, methods=["GET"]),
        ]
    )
