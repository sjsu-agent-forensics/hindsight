"""The control endpoint: seeds, reads back and discards sessions on one server.

It is served on its own port, apart from the MCP endpoint. The agent's config never lists this
port and the interposer never proxies it, so nothing the agent does can reset or reseed a session.
"""

from json import JSONDecodeError

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from hindsight_server_kit.session import SessionStore


def control_app(store: SessionStore) -> Starlette:
    """Build the control API for `store`.

    - `POST /control/sessions/{sid}` seeds (or resets) a session; see `SessionStore.seed` for the
      optional JSON body.
    - `GET /control/sessions/{sid}/effects` returns the session's effect log.
    - `DELETE /control/sessions/{sid}` discards the session.
    - `GET /control/sessions` lists seeded sessions; `GET /control/health` answers when up.
    """

    async def health(request: Request) -> Response:
        return JSONResponse({"server": store.name, "status": "ok"})

    async def list_sessions(request: Request) -> Response:
        return JSONResponse({"server": store.name, "sessions": store.session_ids()})

    async def seed_session(request: Request) -> Response:
        sid = request.path_params["sid"]
        try:
            body = await request.json() if await request.body() else {}
        except JSONDecodeError as e:
            return JSONResponse({"error": f"Body is not JSON: {e}"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Body must be a JSON object."}, status_code=400)
        try:
            session = store.seed(sid, body)
        except Exception as e:
            # Whatever the server's fixture loader raises means the seed request was bad; say
            # why instead of answering with a bare 500.
            return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=400)
        return JSONResponse(session.describe(), status_code=201)

    async def session_effects(request: Request) -> Response:
        sid = request.path_params["sid"]
        session = store.lookup(sid)
        if session is None:
            return JSONResponse({"error": f"No session {sid!r}."}, status_code=404)
        with session.lock:
            effects = list(session.effects)
        return JSONResponse({"session": sid, "effects": effects})

    async def discard_session(request: Request) -> Response:
        sid = request.path_params["sid"]
        if not store.discard(sid):
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
