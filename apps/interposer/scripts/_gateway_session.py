"""Shared by the check scripts: a registered session to reach a server through."""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx


@asynccontextmanager
async def gateway_session(gateway_url: str, server: str) -> AsyncGenerator[str]:
    """Register a session with the gateway and yield ``server``'s URL within it.

    The session is ended on exit. Its only trusted input is a placeholder prompt, since
    the checks run no agent.
    """
    session_id = str(uuid.uuid4())
    async with httpx.AsyncClient(base_url=gateway_url) as http:
        response = await http.post(
            "/sessions",
            json={
                "session_id": session_id,
                "trusted_inputs": [{"kind": "user_prompt", "content": "check"}],
            },
        )
        response.raise_for_status()
        try:
            yield f"{gateway_url}/s/{session_id}/mcp/{server}"
        finally:
            await http.delete(f"/sessions/{session_id}")
