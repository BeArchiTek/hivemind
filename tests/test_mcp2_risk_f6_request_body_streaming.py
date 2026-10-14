"""F6 — do not buffer MCP request bodies twice.

The SDK already bounds and buffers the request body (``max_request_body_size``
on ``streamable_http_app``). For ``/mcp`` requests Hivemind's
``MCPRequestLimitMiddleware`` must no longer buffer or copy the body: body
chunks reach the downstream app as they arrive. Oversized requests are still
rejected with 413: by Hivemind before the body is read when ``Content-Length``
says so, and through the full production stack (SDK limit) for a chunked
request without ``Content-Length``.

Wire-level through the production ASGI stack built by ``create_app`` over a
real HTTP server, with a probe placed directly below the middleware.
"""

from __future__ import annotations

import asyncio
import json

import httpx2

from live_mem.middleware import MCPRequestLimitMiddleware
from tests.test_mcp2_risk_f5_session_limits import KEY, _install_core_settings
from tests.test_mcp2_transport import _running_http

LIMIT = 4096
HEADERS = {
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


class _DownstreamProbe:
    """Records what the app below the request-limit middleware receives."""

    def __init__(self, app):
        self.app = app
        self.first_chunk = asyncio.Event()
        self.requests = 0
        self.chunks: list[bytes] = []

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        self.requests += 1

        async def probed_receive():
            message = await receive()
            if message["type"] == "http.request":
                self.chunks.append(message.get("body", b""))
                self.first_chunk.set()
            return message

        await self.app(scope, probed_receive, send)


def _build_stack(monkeypatch) -> tuple[object, _DownstreamProbe]:
    server, _ = _install_core_settings(monkeypatch, mcp_request_max_bytes=LIMIT)
    app = server.create_app()
    node = app
    while not isinstance(node, MCPRequestLimitMiddleware):
        assert hasattr(node, "app"), "MCPRequestLimitMiddleware not found in the stack"
        node = node.app
    probe = _DownstreamProbe(node.app)
    node.app = probe
    return app, probe


def _initialize_body() -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "f6-stream", "version": "1"},
            },
        }
    ).encode()


async def test_mcp_body_chunks_reach_the_app_before_the_last_chunk_is_sent(monkeypatch):
    app, probe = _build_stack(monkeypatch)
    body = _initialize_body()
    first, rest = body[:20], body[20:]
    delivered_early = {"value": None}

    async def chunked_body():
        yield first
        try:
            async with asyncio.timeout(2):
                await probe.first_chunk.wait()
            delivered_early["value"] = True
        except TimeoutError:
            delivered_early["value"] = False
        yield rest

    async with _running_http(app) as url, httpx2.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{url}/mcp", headers=HEADERS, content=chunked_body()
        )
    assert response.status_code == 200, response.text[:300]
    assert delivered_early["value"] is True, (
        "the middleware buffered the whole body before passing it downstream"
    )
    assert b"".join(probe.chunks) == body


async def test_declared_oversized_body_is_rejected_by_hivemind_before_reading(
    monkeypatch,
):
    app, probe = _build_stack(monkeypatch)

    async with _running_http(app) as url, httpx2.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{url}/mcp", headers=HEADERS, content=b" " * (LIMIT + 1)
        )
    assert response.status_code == 413, response.text[:300]
    assert json.loads(response.text)["max_bytes"] == LIMIT
    assert probe.requests == 0
    assert probe.chunks == []


async def test_chunked_oversized_body_without_content_length_is_rejected(monkeypatch):
    app, _ = _build_stack(monkeypatch)
    padded = _initialize_body() + b" " * LIMIT  # exceeds the limit on the wire

    async def chunked_body():
        for start in range(0, len(padded), 512):
            yield padded[start : start + 512]

    async with _running_http(app) as url, httpx2.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{url}/mcp", headers=HEADERS, content=chunked_body()
        )
    assert response.status_code == 413, (response.status_code, response.text[:300])


async def test_exact_limit_chunked_body_is_accepted(monkeypatch):
    app, probe = _build_stack(monkeypatch)
    body = _initialize_body()
    body += b" " * (LIMIT - len(body))
    assert len(body) == LIMIT

    async def chunked_body():
        for start in range(0, len(body), 1000):
            yield body[start : start + 1000]

    async with _running_http(app) as url, httpx2.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{url}/mcp", headers=HEADERS, content=chunked_body()
        )
    assert response.status_code == 200, response.text[:300]
    assert b"".join(probe.chunks) == body
