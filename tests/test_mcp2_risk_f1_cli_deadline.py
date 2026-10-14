"""F1 — the CLI client must not impose a per-request deadline (MCP SDK 2).

``MCPClient(timeout=T)`` keeps ``T`` as the HTTP idle read timeout only. A tool
that runs longer than ``T`` while keeping its SSE stream active (a
notification at intervals shorter than ``T``) completes, and the client never
sends ``notifications/cancelled`` (which in SDK 2 makes the server abort the
tool handler).
"""

from __future__ import annotations

import asyncio
import warnings

from mcp.server.mcpserver import Context, MCPServer

from tests.test_mcp2_transport import _running_http

T = 1.0
TOOL_DURATION = 2.5
KEEPALIVE_INTERVAL = 0.25


class _RecordingRequests:
    """ASGI wrapper that keeps every request body the client sent to /mcp."""

    def __init__(self, app):
        self.app = app
        self.bodies: list[bytes] = []

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks: list[bytes] = []

        async def recording_receive():
            message = await receive()
            if message["type"] == "http.request":
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    self.bodies.append(b"".join(chunks))
            return message

        await self.app(scope, recording_receive, send)


def _slow_server() -> tuple[MCPServer, dict]:
    outcome = {"completed": False, "cancelled": False}
    mcp = MCPServer("f1-slow-tool-fixture")

    @mcp.tool()
    async def slow_tool(ctx: Context) -> dict:
        try:
            elapsed = 0.0
            while elapsed < TOOL_DURATION:
                await asyncio.sleep(KEEPALIVE_INTERVAL)
                elapsed += KEEPALIVE_INTERVAL
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    # Request-scoped notification: rides this request's SSE
                    # stream, so the HTTP idle read timeout T never elapses.
                    await ctx.log("info", f"still working ({elapsed:.2f}s)")
        except asyncio.CancelledError:
            outcome["cancelled"] = True
            raise
        outcome["completed"] = True
        return {"status": "ok", "elapsed": elapsed}

    return mcp, outcome


async def test_cli_tool_longer_than_timeout_completes_without_client_cancel():
    from scripts.cli.client import MCPClient

    mcp, outcome = _slow_server()
    recorder = _RecordingRequests(mcp.streamable_http_app())
    async with _running_http(recorder) as url:
        result = await MCPClient(url, timeout=T).call_tool("slow_tool", {})
        # Let any courtesy cancel the client might send reach the server.
        await asyncio.sleep(0.2)

    assert result.get("status") == "ok", result
    assert outcome["completed"] is True
    assert outcome["cancelled"] is False
    cancelled_bodies = [b for b in recorder.bodies if b"notifications/cancelled" in b]
    assert cancelled_bodies == [], cancelled_bodies


async def test_cli_keeps_http_idle_read_timeout():
    """The HTTP idle read timeout T stays: a silent stream longer than T fails."""
    from scripts.cli.client import MCPClient

    mcp = MCPServer("f1-silent-tool-fixture")

    @mcp.tool()
    async def silent_tool() -> dict:
        await asyncio.sleep(T * 3)
        return {"status": "ok"}

    async with _running_http(mcp.streamable_http_app()) as url:
        result = await MCPClient(url, timeout=T).call_tool("silent_tool", {})
    assert result.get("status") == "error", result
