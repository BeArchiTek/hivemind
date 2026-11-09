"""Audit entry, Prometheus counter and rollout setting for refused tool calls.

Specification: ``specs/001-tool-guidance-remediation/spec.md`` (FR-030, FR-031).
"""

from __future__ import annotations

import json
import logging

import pytest
from mcp.server.mcpserver import Context
from starlette.testclient import TestClient

import live_mem.auth.context as auth_context
from live_mem.tools import register_all_tools
from live_mem.tools.exposure import HivemindMCPServer

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

INIT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


@pytest.fixture(autouse=True)
def _clear_auth_stores():
    auth_context._fresh_token_store.clear()
    auth_context._invalidated_token_hashes.clear()
    yield
    auth_context._fresh_token_store.clear()
    auth_context._invalidated_token_hashes.clear()


def _message(response):
    assert response.status_code == 200, (response.status_code, response.text[:500])
    if response.headers["content-type"].startswith("text/event-stream"):
        return next(
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ") and json.loads(line[6:]).get("id") == 1
        )
    return response.json()


def _open_session(client, headers: dict) -> dict:
    response = client.post(
        "/mcp",
        headers={**INIT_HEADERS, **headers},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "guidance-observability", "version": "1"},
            },
        },
    )
    assert response.status_code == 200, (response.status_code, response.text[:300])
    session_headers = {
        **INIT_HEADERS,
        **headers,
        "MCP-Session-Id": response.headers["mcp-session-id"],
        "MCP-Protocol-Version": "2025-11-25",
    }
    ready = client.post(
        "/mcp",
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert ready.status_code == 202, ready.text[:300]
    return session_headers


def _call(client, headers: dict, name: str, arguments: dict):
    return _message(
        client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
    )["result"]


# ──────────────────────────────────────────────────────────────────────────
# Risk R1: the ASGI state dict is shared with the SDK tool call (T007)
# ──────────────────────────────────────────────────────────────────────────


def test_state_written_inside_a_tool_call_is_read_by_the_asgi_middleware_finally():
    from live_mem.tools.guidance import TOOL_OUTCOME_STATE_KEY

    mcp = HivemindMCPServer("state-sharing")

    @mcp.tool()
    async def write_state(context: Context) -> dict:
        """Write a marker into the request's ASGI state."""
        state = context.request_context.request.scope["state"]
        state[TOOL_OUTCOME_STATE_KEY] = {"tool": "write_state", "error_kind": "marker"}
        return {"status": "ok"}

    seen: list[dict | None] = []

    class Observer:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            scope.setdefault("state", {})
            try:
                await self.app(scope, receive, send)
            finally:
                seen.append(scope.get("state", {}).get(TOOL_OUTCOME_STATE_KEY))

    app = Observer(mcp.streamable_http_app())
    with TestClient(app, base_url="http://localhost:8002") as client:
        headers = _open_session(client, {})
        result = _call(client, headers, "write_state", {})

    assert json.loads(result["content"][0]["text"]) == {"status": "ok"}
    assert {"tool": "write_state", "error_kind": "marker"} in seen


# ──────────────────────────────────────────────────────────────────────────
# Rollout setting (T016)
# ──────────────────────────────────────────────────────────────────────────


def test_mcp_refusal_is_error_parses_and_warns_when_false(monkeypatch, caplog):
    from live_mem.config import Settings

    monkeypatch.setenv("ADMIN_BOOTSTRAP_KEY", "guidance-observability-bootstrap-key-32chars")
    monkeypatch.setenv("HIVEMIND_MESH_ENABLED", "false")
    assert Settings(_env_file=None).mcp_refusal_is_error is True
    with caplog.at_level(logging.WARNING, logger="live_mem.config"):
        settings = Settings(_env_file=None, mcp_refusal_is_error="false")
    assert settings.mcp_refusal_is_error is False
    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "MCP_REFUSAL_IS_ERROR" in record.getMessage()
    ]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert "temporary" in warnings[0].getMessage()


# ──────────────────────────────────────────────────────────────────────────
# Audit entry and Prometheus counter (T013)
# ──────────────────────────────────────────────────────────────────────────


def _request_entries(caplog) -> list[dict]:
    entries = []
    for record in caplog.records:
        if record.name != "live_mem.audit":
            continue
        try:
            entry = json.loads(record.getMessage())
        except ValueError:
            continue
        if entry.get("event") == "request":
            entries.append(entry)
    return entries


def test_refused_calls_are_audited_and_counted_by_canonical_tool_and_kind(
    monkeypatch, caplog
):
    from tests.test_operator_tool_guard import AGENT, _install_production_app

    server, _, _ = _install_production_app(monkeypatch)
    space = "space-outside-the-token-scope"
    with caplog.at_level(logging.INFO, logger="live_mem.audit"):
        with TestClient(server.create_app(), base_url="http://localhost:8002") as client:
            headers = _open_session(client, {"Authorization": f"Bearer {AGENT}"})
            refused = _call(
                client,
                headers,
                "live_note",
                {"space_id": space, "category": "observation", "content": "x"},
            )
            proxied = client.post(
                "/api/tool",
                headers={"Authorization": f"Bearer {AGENT}"},
                json={"tool": "space_info", "arguments": {"space_id": space}},
            )
            text = client.get("/metrics").text
            as_json = client.get("/metrics", headers={"Accept": "application/json"}).json()

    # The kind itself is pinned by the replay test; this test proves that the
    # kind the response carries reaches the audit entry and the counter.
    payload = json.loads(refused["content"][0]["text"])
    kind = payload["error_kind"]
    assert proxied.status_code == 200
    assert proxied.json()["status"] == "error"
    # The proxy returns the handler's dict unchanged; a dict without
    # error_kind is counted as unclassified_refusal (data-model.md).
    proxy_kind = proxied.json().get("error_kind", "unclassified_refusal")

    with_tool = [entry for entry in _request_entries(caplog) if "tool" in entry]
    assert [(e["tool"], e["error_kind"]) for e in with_tool] == [
        ("short_note", kind),
        ("space_info", proxy_kind),
    ]
    for entry in with_tool:
        serialized = json.dumps(entry)
        assert space not in serialized
        assert payload["remediation"]["reason"] not in serialized
        assert AGENT not in serialized
        assert set(entry) - {"tool", "error_kind"} == set(
            next(e for e in _request_entries(caplog) if "tool" not in e)
        )

    assert f'livemem_tool_refusals_total{{tool="short_note",error_kind="{kind}"}} 1' in text
    assert f'livemem_tool_refusals_total{{tool="space_info",error_kind="{proxy_kind}"}} 1' in text
    assert "# HELP livemem_tool_refusals_total" in text
    assert "# TYPE livemem_tool_refusals_total counter" in text
    assert as_json["tool_refusals"] == {
        f"short_note/{kind}": 1,
        f"space_info/{proxy_kind}": 1,
    }


# ──────────────────────────────────────────────────────────────────────────
# /api/tool proxy and the CLI display (T058, T059)
# ──────────────────────────────────────────────────────────────────────────


def test_api_tool_returns_the_payload_unchanged_and_keeps_the_403_check(monkeypatch):
    from tests.test_operator_tool_guard import AGENT, FIXTURE_TOKENS, _install_production_app

    server, _, _ = _install_production_app(monkeypatch)
    monkeypatch.setitem(
        FIXTURE_TOKENS,
        "reader-token",
        {
            "type": "token",
            "client_name": "reader",
            "permissions": ["read"],
            "allowed_resources": ["proj"],
            "token_hash": "sha256:" + "9" * 64,
        },
    )
    body = {"tool": "space_info", "arguments": {"space_id": "q"}}
    with TestClient(server.create_app(), base_url="http://localhost:8002") as client:
        refused = client.post("/api/tool", headers={"Authorization": f"Bearer {AGENT}"}, json=body)
        reader = client.post("/api/tool", headers={"Authorization": "Bearer reader-token"}, json=body)

    assert refused.status_code == 200
    payload = refused.json()
    assert payload["status"] == "error"
    assert payload["message"] == "Access denied to space 'q'"
    assert payload["error_kind"] == "space_access_denied"
    assert payload["remediation"]["next_tool"] == "space_list"
    assert reader.status_code == 403
    assert "write" in reader.json()["message"]


def test_cli_shows_the_remediation_reason_of_a_bank_compact_failure(monkeypatch):
    import io
    import sys

    from rich.console import Console

    scripts = str(ROOT_DIR / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from cli import display

    buffer = io.StringIO()
    monkeypatch.setattr(display, "console", Console(file=buffer, width=200, force_terminal=False))
    display.show_bank_compact_failure(
        {
            "status": "error",
            "message": "Bank compaction could not be completed safely.",
            "failure_reason": "compaction_tool_failure",
            "failed_phase": "prepare",
            "rollback_outcome": "not_needed",
            "failures": [],
            "error_kind": "internal_failure",
            "remediation": {"reason": "Inspect the server health before retrying.", "retryable": False},
        }
    )
    display.show_bank_compact_failure({"status": "error", "failure_reason": "x"})
    output = buffer.getvalue()
    assert "remediation: Inspect the server health before retrying." in output
    assert "remediation: <missing>" in output
    assert "retryable" not in output
