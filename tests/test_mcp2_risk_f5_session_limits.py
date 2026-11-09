"""F5 — explicit, configurable MCP session limits on both runtimes.

MCP SDK 2 defaults (10,000 open stateful sessions per process, 30 min idle
close) must be taken explicitly from configuration, and the core server must
cap the number of open sessions one token may hold:

- ``Settings.mcp_max_sessions`` (default 10000),
  ``Settings.mcp_session_idle_timeout_seconds`` (default 1800.0),
  ``Settings.mcp_max_sessions_per_bearer_token`` (default 100; 0 disables);
- ``streamable_http_app`` receives ``max_sessions`` and ``session_idle_timeout``
  from those settings in the core server and from Graph Memory's own config;
- a bearer token at its per-bearer-token cap gets HTTP 429 with a JSON-RPC error body on a
  session-creating request; other tokens are unaffected; ``DELETE /mcp`` and
  idle expiry free the slot;
- docs/DEPLOYMENT.md documents the three settings and the 30-minute idle close.

Session tests run through the production ASGI stack built by
``live_mem.server.create_app`` (lifespan included).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from live_mem.config import Settings
from tests.fakes.inference_fakes import apply_graph_memory_baseline_env

ROOT = Path(__file__).resolve().parents[1]
KEY = "mcp2-f5-fixture-bootstrap-key-32chars!"
READER = "reader-f5-fixture-token"
INIT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


# ──────────────────────────────────────────────────────────────────────────
# Settings contract
# ──────────────────────────────────────────────────────────────────────────


def test_core_settings_expose_session_limits_with_sdk_defaults(monkeypatch):
    for name in (
        "MCP_MAX_SESSIONS",
        "MCP_SESSION_IDLE_TIMEOUT_SECONDS",
        "MCP_MAX_SESSIONS_PER_BEARER_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.mcp_max_sessions == 10000
    assert settings.mcp_session_idle_timeout_seconds == 1800.0
    assert settings.mcp_max_sessions_per_bearer_token == 100


def test_core_settings_session_limits_follow_environment(monkeypatch):
    monkeypatch.setenv("MCP_MAX_SESSIONS", "7")
    monkeypatch.setenv("MCP_SESSION_IDLE_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("MCP_MAX_SESSIONS_PER_BEARER_TOKEN", "0")
    settings = Settings(_env_file=None)
    assert settings.mcp_max_sessions == 7
    assert settings.mcp_session_idle_timeout_seconds == 12.5
    assert settings.mcp_max_sessions_per_bearer_token == 0


# ──────────────────────────────────────────────────────────────────────────
# Production app factories pass the limits to the SDK
# ──────────────────────────────────────────────────────────────────────────


def _install_core_settings(monkeypatch, **overrides):
    """Build a fresh core ``Settings`` from the environment and bind it."""
    from live_mem import server
    from live_mem.auth import middleware as auth_middleware
    from live_mem.core import consolidator as consolidator_module
    from tests.test_asgi_lifespan_integration import _prepare_core_session_preflight

    _prepare_core_session_preflight(monkeypatch)

    async def close_process_resource():  # same isolation as the lifespan suite
        return None

    monkeypatch.setattr(
        consolidator_module, "close_consolidator_if_initialized", close_process_resource
    )
    # The MID archive projector needs a configured S3 storage at startup; it is
    # unrelated to MCP session handling, so keep it out of this stack.
    monkeypatch.setattr(server, "_start_mid_archive_projection", close_process_resource)
    monkeypatch.setattr(server, "_stop_mid_archive_projection", close_process_resource)
    for name, value in overrides.items():
        monkeypatch.setenv(name.upper(), str(value))
    monkeypatch.setenv("ADMIN_BOOTSTRAP_KEY", KEY)
    monkeypatch.setenv("HIVEMIND_MESH_ENABLED", "false")
    # Build a fresh object instead of clearing the process cache: other suites
    # rely on ``server.settings is get_settings()`` and monkeypatch restores both.
    fresh = Settings(_env_file=None)
    monkeypatch.setattr(server, "settings", fresh)
    monkeypatch.setattr(auth_middleware, "get_settings", lambda: fresh)
    validate = auth_middleware.AuthMiddleware._validate_token

    async def validate_with_reader(self, token):
        if token == READER:
            return {
                "type": "token",
                "client_name": "reader",
                "permissions": ["read"],
                "allowed_resources": [],
                "token_hash": "sha256:" + "r" * 64,
            }
        return await validate(self, token)

    monkeypatch.setattr(
        auth_middleware.AuthMiddleware, "_validate_token", validate_with_reader
    )
    return server, fresh


def _spy_streamable_http_app(monkeypatch, mcp_server):
    """Record the keyword arguments ``mcp_server.streamable_http_app`` receives.

    The spy is bound to the INSTANCE the app factory uses, not to its class:
    ``monkeypatch.setattr(instance, ...)`` in other suites records the bound
    method as the old value and writes it back into the instance dict on
    undo, which would shadow a class-level spy for the rest of the session.
    """
    calls: list[dict] = []
    original = mcp_server.streamable_http_app

    def spy(**kwargs):
        calls.append(dict(kwargs))
        return original(**kwargs)

    monkeypatch.setattr(mcp_server, "streamable_http_app", spy)
    return calls


def test_core_app_factory_passes_configured_session_limits(monkeypatch):
    server, settings = _install_core_settings(
        monkeypatch, mcp_max_sessions=321, mcp_session_idle_timeout_seconds=45.5
    )
    calls = _spy_streamable_http_app(monkeypatch, server.mcp)
    server.create_app()
    assert len(calls) == 1, calls
    assert calls[0].get("max_sessions") == 321 == settings.mcp_max_sessions
    assert (
        calls[0].get("session_idle_timeout")
        == 45.5
        == settings.mcp_session_idle_timeout_seconds
    )


def test_core_app_factory_refuses_to_start_without_the_sdk_session_registry(monkeypatch):
    """The per-bearer-token cap reads the SDK's private session registry.

    If a future SDK no longer exposes it, the cap would count zero sessions
    and silently fail open; the factory must refuse to build instead, unless
    the cap is disabled.
    """
    server, _ = _install_core_settings(monkeypatch, mcp_max_sessions_per_bearer_token=5)
    original = server.mcp.streamable_http_app

    def without_registry(**kwargs):
        app = original(**kwargs)
        monkeypatch.setattr(server.mcp._lowlevel_server, "_session_manager", object())
        return app

    monkeypatch.setattr(server.mcp, "streamable_http_app", without_registry)
    with pytest.raises(RuntimeError, match="session registry"):
        server.create_app()


def test_core_app_factory_without_registry_builds_when_the_cap_is_disabled(monkeypatch):
    server, _ = _install_core_settings(monkeypatch, mcp_max_sessions_per_bearer_token=0)
    original = server.mcp.streamable_http_app

    def without_registry(**kwargs):
        app = original(**kwargs)
        monkeypatch.setattr(server.mcp._lowlevel_server, "_session_manager", object())
        return app

    monkeypatch.setattr(server.mcp, "streamable_http_app", without_registry)
    server.create_app()


def test_graph_memory_app_factory_passes_its_own_session_limits(monkeypatch):
    apply_graph_memory_baseline_env(monkeypatch)
    from mcp_memory import server as graph_server

    settings = graph_server.settings
    assert settings.mcp_max_sessions >= 1
    assert settings.mcp_session_idle_timeout_seconds > 0
    calls = _spy_streamable_http_app(monkeypatch, graph_server.mcp)
    graph_server._create_app()
    assert len(calls) == 1, calls
    assert calls[0].get("max_sessions") == settings.mcp_max_sessions
    assert (
        calls[0].get("session_idle_timeout")
        == settings.mcp_session_idle_timeout_seconds
    )


# ──────────────────────────────────────────────────────────────────────────
# Observable session behaviour through the production stack
# ──────────────────────────────────────────────────────────────────────────


def _initialize(client, token: str, request_id: int = 1):
    return client.post(
        "/mcp",
        headers={**INIT_HEADERS, "Authorization": f"Bearer {token}"},
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "f5", "version": "1"},
            },
        },
    )


def _close(client, token: str, session_id: str):
    return client.delete(
        "/mcp",
        headers={"Authorization": f"Bearer {token}", "MCP-Session-Id": session_id},
    )


def _open_session(client, token: str) -> str:
    response = _initialize(client, token)
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.headers["mcp-session-id"]


def test_process_session_cap_is_the_configured_value(monkeypatch):
    server, _ = _install_core_settings(
        monkeypatch, mcp_max_sessions=2, mcp_max_sessions_per_bearer_token=0
    )
    with TestClient(server.create_app(), base_url="http://localhost:8002") as client:
        first = _open_session(client, KEY)
        _open_session(client, READER)
        refused = _initialize(client, KEY)
        assert refused.status_code == 503, (refused.status_code, refused.text[:300])
        assert _close(client, KEY, first).status_code == 200
        _open_session(client, KEY)


def test_per_bearer_token_session_cap_returns_429_and_leaves_other_tokens_alone(monkeypatch):
    server, _ = _install_core_settings(
        monkeypatch, mcp_max_sessions=100, mcp_max_sessions_per_bearer_token=2
    )
    with TestClient(server.create_app(), base_url="http://localhost:8002") as client:
        admin_sessions = [_open_session(client, KEY), _open_session(client, KEY)]

        refused = _initialize(client, KEY, request_id=3)
        assert refused.status_code == 429, (refused.status_code, refused.text[:300])
        body = json.loads(refused.text)
        assert body.get("jsonrpc") == "2.0"
        assert "error" in body and "message" in body["error"], body

        # Another token is unaffected.
        reader_session = _open_session(client, READER)

        # Requests inside an existing session are not session-creating.
        follow_up = client.post(
            "/mcp",
            headers={
                **INIT_HEADERS,
                "Authorization": f"Bearer {KEY}",
                "MCP-Session-Id": admin_sessions[0],
                "MCP-Protocol-Version": "2025-11-25",
            },
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert follow_up.status_code == 202, follow_up.text[:300]

        # Closing one session frees the slot for that token.
        assert _close(client, KEY, admin_sessions[0]).status_code == 200
        _open_session(client, KEY)
        assert _initialize(client, KEY).status_code == 429
        assert _close(client, READER, reader_session).status_code == 200


def test_per_bearer_token_cap_zero_disables_the_limit(monkeypatch):
    server, _ = _install_core_settings(
        monkeypatch, mcp_max_sessions=100, mcp_max_sessions_per_bearer_token=0
    )
    with TestClient(server.create_app(), base_url="http://localhost:8002") as client:
        for _ in range(4):
            _open_session(client, KEY)


def test_idle_expired_session_stops_counting_against_its_token(monkeypatch):
    server, _ = _install_core_settings(
        monkeypatch,
        mcp_max_sessions=100,
        mcp_max_sessions_per_bearer_token=1,
        mcp_session_idle_timeout_seconds=0.5,
    )
    with TestClient(server.create_app(), base_url="http://localhost:8002") as client:
        _open_session(client, KEY)
        assert _initialize(client, KEY).status_code == 429
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            time.sleep(0.25)
            if _initialize(client, KEY).status_code == 200:
                break
        else:
            pytest.fail("idle-expired session still counts against its token")


# ──────────────────────────────────────────────────────────────────────────
# Documentation
# ──────────────────────────────────────────────────────────────────────────


def test_deployment_doc_covers_session_limits():
    text = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    for name in (
        "MCP_MAX_SESSIONS",
        "MCP_SESSION_IDLE_TIMEOUT_SECONDS",
        "MCP_MAX_SESSIONS_PER_BEARER_TOKEN",
    ):
        assert name in text, f"{name} is not documented in docs/DEPLOYMENT.md"
    assert "30" in text and "idle" in text.lower()
