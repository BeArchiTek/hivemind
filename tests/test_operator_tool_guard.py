"""Per-bearer-token guard for ``audience=operator`` tools (spec 002).

Every ``audience=operator`` tool and alias stays registered and callable by
exact name. This suite pins the per-token control that decides whether a
bearer token may call them:

- a token whose ``operator_tools`` attribute is ``False`` is refused over a
  real MCP session built by the production ``create_app()`` factory, for the
  canonical name and for its historical alias, before the handler runs;
- the same token with the attribute ``True``, a stored record without the
  attribute (created before the feature) and the bootstrap key reach the
  handler, so an upgrade changes nothing for existing deployments;
- ``token_create`` stores ``False``; ``admin_create_token`` stores ``True``
  unless told otherwise; the admin update tools change it;
- ``POST /api/tool`` keeps working for operator tokens and refuses agent
  tokens, because it accepts the same bearer token and calls handlers
  in-process (``src/live_mem/tools/__init__.py:83-134``);
- the owned CLI client reaches operator tools over real HTTP with an operator
  token;
- every refusal writes one ``live_mem.audit`` line with no argument value;
- unregistered names are answered exactly as before.

Specification: ``specs/002-operator-tool-guard/spec.md``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner
from mcp.types import CallToolRequest
from starlette.testclient import TestClient

import live_mem.auth.context as auth_context
from live_mem.config import Settings
from live_mem.core.models import TokenInfo, TokensStore
from live_mem.core.tokens import TOKENS_KEY, TokenService
from live_mem.tools import call_tool_direct, register_all_tools
from live_mem.tools.exposure import (
    TOOL_EXPOSURES,
    HivemindMCPServer,
    ToolAudience,
)
from tests.test_lm2_11_access_roles import FakeStorage
from tests.test_mcp2_transport import _running_http
from tests.test_p10_tool_exposure import _identity, _mcp_request, _request_context

ROOT = Path(__file__).resolve().parents[1]

KEY = "operator-guard-fixture-bootstrap-key-32chars"
OPERATOR = "operator-guard-fixture-operator-token"
AGENT = "operator-guard-fixture-agent-token"
LEGACY = "operator-guard-fixture-legacy-token"
ADMIN_OPERATOR = "operator-guard-fixture-admin-operator-token"
INIT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
REFUSAL_MESSAGE = (
    "This tool is reserved for operators. Ask an operator to perform the "
    "action through the admin console or the CLI with an operator token."
)

FIXTURE_TOKENS = {
    OPERATOR: {
        "type": "token",
        "client_name": "ops-cli",
        "permissions": ["read", "write", "manage"],
        "allowed_resources": ["proj"],
        "token_hash": "sha256:" + "1" * 64,
        "operator_tools": True,
    },
    AGENT: {
        "type": "token",
        "client_name": "agent-worker",
        "permissions": ["read", "write", "manage"],
        "allowed_resources": ["proj"],
        "token_hash": "sha256:" + "2" * 64,
        "operator_tools": False,
    },
    LEGACY: {
        "type": "token",
        "client_name": "pre-upgrade",
        "permissions": ["read", "write", "manage"],
        "allowed_resources": ["proj"],
        "token_hash": "sha256:" + "3" * 64,
    },
    ADMIN_OPERATOR: {
        "type": "token",
        "client_name": "ops-admin",
        "permissions": ["read", "write", "manage", "admin"],
        "allowed_resources": [],
        "token_hash": "sha256:" + "4" * 64,
    },
}

OPERATOR_ENTRIES = tuple(
    entry for entry in TOOL_EXPOSURES if entry.audience is ToolAudience.OPERATOR
)
OPERATOR_NAMES = tuple(
    name for entry in OPERATOR_ENTRIES for name in (entry.canonical_name, *entry.aliases)
)


# ──────────────────────────────────────────────────────────────────────────
# Harness
# ──────────────────────────────────────────────────────────────────────────


class _Storage:
    """Minimal storage double: no space exists, every call is recorded."""

    def __init__(self) -> None:
        self.exists = AsyncMock(return_value=False)


def _install_production_app(monkeypatch):
    """Bind the production core server to fixture tokens and fakes."""
    from live_mem import server
    from live_mem.auth import middleware as auth_middleware
    from live_mem.core import consolidator as consolidator_module
    from live_mem.core import storage as storage_module
    from live_mem.core import tokens as tokens_module
    from tests.test_asgi_lifespan_integration import _prepare_core_session_preflight

    _prepare_core_session_preflight(monkeypatch)

    async def close_process_resource():
        return None

    monkeypatch.setattr(
        consolidator_module, "close_consolidator_if_initialized", close_process_resource
    )
    monkeypatch.setattr(server, "_start_mid_archive_projection", close_process_resource)
    monkeypatch.setattr(server, "_stop_mid_archive_projection", close_process_resource)
    monkeypatch.setenv("ADMIN_BOOTSTRAP_KEY", KEY)
    monkeypatch.setenv("HIVEMIND_MESH_ENABLED", "false")
    fresh = Settings(_env_file=None)
    monkeypatch.setattr(server, "settings", fresh)
    monkeypatch.setattr(auth_middleware, "get_settings", lambda: fresh)
    validate = auth_middleware.AuthMiddleware._validate_token

    async def validate_fixture(self, token):
        if token in FIXTURE_TOKENS:
            return dict(FIXTURE_TOKENS[token])
        return await validate(self, token)

    monkeypatch.setattr(auth_middleware.AuthMiddleware, "_validate_token", validate_fixture)

    storage = _Storage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: storage)

    # The lifespan preflight stub lacks list_tokens; keep its methods and add
    # the one admin_list_tokens needs.
    preflight_tokens = tokens_module.get_token_service()

    class Tokens(type(preflight_tokens)):
        list_tokens = AsyncMock(return_value={"status": "ok", "tokens": [], "total": 0})

    tokens = Tokens()
    monkeypatch.setattr(tokens_module, "get_token_service", lambda: tokens)
    auth_context._fresh_token_store.clear()
    auth_context._invalidated_token_hashes.clear()
    return server, storage, tokens


def _message(response):
    assert response.status_code == 200, (response.status_code, response.text[:500])
    if response.headers["content-type"].startswith("text/event-stream"):
        return next(
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ") and json.loads(line[6:]).get("id") == 1
        )
    return response.json()


def _open_session(client, token: str) -> dict:
    response = client.post(
        "/mcp",
        headers={**INIT_HEADERS, "Authorization": f"Bearer {token}"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "operator-guard", "version": "1"},
            },
        },
    )
    assert response.status_code == 200, (response.status_code, response.text[:300])
    headers = {
        **INIT_HEADERS,
        "Authorization": f"Bearer {token}",
        "MCP-Session-Id": response.headers["mcp-session-id"],
        "MCP-Protocol-Version": "2025-11-25",
    }
    ready = client.post(
        "/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert ready.status_code == 202, ready.text[:300]
    return headers


def _call(client, headers: dict, name: str, arguments: dict) -> dict:
    """Return the tool payload (dict) or the raw JSON-RPC result on error."""
    message = _message(
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
    )
    result = message["result"]
    text = result["content"][0]["text"]
    if result.get("isError"):
        return {"isError": True, "text": text}
    return json.loads(text)


def _refusal_records(caplog) -> list[dict]:
    entries = []
    for record in caplog.records:
        if record.name != "live_mem.audit":
            continue
        try:
            entry = json.loads(record.getMessage())
        except ValueError:
            continue
        if entry.get("event") == "operator_tool_refused":
            entries.append(entry)
    return entries


@pytest.fixture
def audit(caplog):
    with caplog.at_level(logging.INFO, logger="live_mem.audit"):
        yield caplog


@pytest.fixture
def exposed_mcp() -> HivemindMCPServer:
    mcp = HivemindMCPServer("operator-guard")
    register_all_tools(mcp)
    return mcp


async def _low_level_call(mcp: HivemindMCPServer, name: str, arguments: dict, token_info):
    handler = mcp._lowlevel_server._request_handlers["tools/call"].handler
    request = CallToolRequest(params={"name": name, "arguments": arguments})
    with _mcp_request(token_info):
        result = await handler(_request_context(token_info), request.params)
    return json.loads(result.content[0].text)


# ──────────────────────────────────────────────────────────────────────────
# US1 — an agent token is refused over /mcp
# ──────────────────────────────────────────────────────────────────────────


def test_agent_token_is_refused_for_mid_write_and_its_alias_over_mcp(monkeypatch, audit):
    server, storage, _ = _install_production_app(monkeypatch)
    arguments = {"space_id": "proj", "filename": "notes.md", "content": "x"}
    with TestClient(server.create_app(), base_url="http://localhost:8002") as client:
        headers = _open_session(client, AGENT)
        canonical = _call(client, headers, "mid_write", arguments)
        alias = _call(client, headers, "bank_write", arguments)

    assert canonical == {"status": "forbidden", "message": REFUSAL_MESSAGE}
    assert alias == canonical
    storage.exists.assert_not_awaited()

    entries = _refusal_records(audit)
    assert len(entries) == 2, entries
    assert [entry["tool"] for entry in entries] == ["mid_write", "bank_write"]
    for entry in entries:
        assert entry["canonical_tool"] == "mid_write"
        assert entry["cause"] == "operator_tools_refused"
        assert entry["route"] == "mcp"
        assert entry["client"] == "agent-worker"
        assert entry["auth_type"] == "token"
        assert entry["request_id"]
        serialized = json.dumps(entry)
        assert "notes.md" not in serialized and "proj" not in serialized
        assert AGENT not in serialized


def test_every_operator_entry_and_alias_is_refused_for_an_agent_token(monkeypatch, audit):
    server, _, _ = _install_production_app(monkeypatch)
    with TestClient(server.create_app(), base_url="http://localhost:8002") as client:
        headers = _open_session(client, AGENT)
        refused = {name: _call(client, headers, name, {}) for name in OPERATOR_NAMES}
        whoami = _call(client, headers, "system_whoami", {})

    # 36 operator entries plus their 4 historical aliases.
    assert len(OPERATOR_ENTRIES) == 36 and len(OPERATOR_NAMES) == 40
    assert all(
        payload == {"status": "forbidden", "message": REFUSAL_MESSAGE}
        for payload in refused.values()
    ), {name: payload for name, payload in refused.items() if payload.get("status") != "forbidden"}
    # Agent-core tools are untouched by the guard.
    assert whoami["status"] == "ok" and whoami["client_name"] == "agent-worker"
    assert len(_refusal_records(audit)) == len(OPERATOR_NAMES)


def test_agent_core_tools_are_not_guarded_and_unknown_names_are_unchanged(monkeypatch, audit):
    server, _, _ = _install_production_app(monkeypatch)
    with TestClient(server.create_app(), base_url="http://localhost:8002") as client:
        headers = _open_session(client, AGENT)
        note = _call(client, headers, "short_note", {"space_id": "proj", "content": "x"})
        unknown = _call(client, headers, "no_such_tool", {})

    # short_note reaches its handler (the storage double has no space).
    assert note.get("status") != "forbidden" and note.get("message") != REFUSAL_MESSAGE
    assert unknown["isError"] is True and "Unknown tool" in unknown["text"]
    assert _refusal_records(audit) == []


@pytest.mark.asyncio
async def test_non_boolean_attribute_and_unresolved_audience_fail_closed(
    exposed_mcp: HivemindMCPServer, audit
):
    corrupt = {**_identity("manage"), "operator_tools": "yes"}
    refused = await _low_level_call(exposed_mcp, "space_export", {"space_id": "proj"}, corrupt)
    assert refused == {"status": "forbidden", "message": REFUSAL_MESSAGE}

    # A registered name without a registry entry cannot happen after startup
    # validation; if it does, the audience is unknown and the call is refused.
    @exposed_mcp.tool()
    async def rogue_operator_tool() -> dict:
        """Registered after validation, absent from the exposure registry."""
        return {"status": "ok"}

    allowed_identity = {**_identity("manage"), "operator_tools": True}
    refused_rogue = await _low_level_call(exposed_mcp, "rogue_operator_tool", {}, allowed_identity)
    assert refused_rogue == {"status": "forbidden", "message": REFUSAL_MESSAGE}

    causes = [(entry["tool"], entry["cause"], entry["canonical_tool"]) for entry in _refusal_records(audit)]
    assert causes == [
        ("space_export", "operator_tools_refused", "space_export"),
        ("rogue_operator_tool", "audience_unresolved", None),
    ]


@pytest.mark.asyncio
async def test_missing_identity_is_refused_by_the_handler_not_the_guard(
    exposed_mcp: HivemindMCPServer, audit
):
    """Without an identity there is no attribute to read; the handler's own
    authentication gate refuses (constitution principle II), and the guard
    writes no operator_tool_refused entry for it."""
    refused = await _low_level_call(exposed_mcp, "space_export", {"space_id": "proj"}, None)
    assert refused == {"status": "error", "message": "Authentication required"}
    assert _refusal_records(audit) == []


# ──────────────────────────────────────────────────────────────────────────
# US2 — operator tokens, legacy records, bootstrap, CLI and /api/tool
# ──────────────────────────────────────────────────────────────────────────


def test_operator_legacy_and_bootstrap_tokens_reach_the_handler_over_mcp(monkeypatch, audit):
    server, storage, _ = _install_production_app(monkeypatch)
    arguments = {"space_id": "proj", "filename": "notes.md", "content": "x"}
    with TestClient(server.create_app(), base_url="http://localhost:8002") as client:
        results = {}
        for token in (OPERATOR, LEGACY, KEY):
            headers = _open_session(client, token)
            results[token] = _call(client, headers, "mid_write", arguments)

    for token, payload in results.items():
        assert payload == {"status": "not_found", "message": "Space 'proj' not found"}, (token, payload)
    assert storage.exists.await_count == 3
    assert _refusal_records(audit) == []


def test_attribute_change_applies_on_the_next_call_of_an_open_session(monkeypatch, audit):
    server, storage, _ = _install_production_app(monkeypatch)
    arguments = {"space_id": "proj", "filename": "notes.md", "content": "x"}
    with TestClient(server.create_app(), base_url="http://localhost:8002") as client:
        headers = _open_session(client, OPERATOR)
        before = _call(client, headers, "mid_write", arguments)
        monkeypatch.setitem(FIXTURE_TOKENS, OPERATOR, {**FIXTURE_TOKENS[OPERATOR], "operator_tools": False})
        after = _call(client, headers, "mid_write", arguments)

    assert before == {"status": "not_found", "message": "Space 'proj' not found"}
    assert after == {"status": "forbidden", "message": REFUSAL_MESSAGE}
    assert storage.exists.await_count == 1


@pytest.mark.asyncio
async def test_cli_client_operator_commands_work_with_an_operator_token(monkeypatch, audit):
    """``bank write`` and ``token list`` go over /mcp through scripts/cli/client.py."""
    from scripts.cli.client import MCPClient

    server, storage, tokens = _install_production_app(monkeypatch)
    arguments = {"space_id": "proj", "filename": "notes.md", "content": "x"}
    async with _running_http(server.create_app()) as url:
        operator_write = await MCPClient(url, OPERATOR, timeout=5).call_tool("bank_write", arguments)
        admin_list = await MCPClient(url, ADMIN_OPERATOR, timeout=5).call_tool("admin_list_tokens", {})
        agent_write = await MCPClient(url, AGENT, timeout=5).call_tool("bank_write", arguments)

    assert operator_write == {"status": "not_found", "message": "Space 'proj' not found"}
    assert admin_list == {"status": "ok", "tokens": [], "total": 0}
    tokens.list_tokens.assert_awaited_once()
    assert agent_write == {"status": "forbidden", "message": REFUSAL_MESSAGE}
    assert [entry["client"] for entry in _refusal_records(audit)] == ["agent-worker"]


def test_api_tool_route_keeps_operators_and_refuses_agent_tokens(monkeypatch, audit):
    """/api/tool calls handlers in-process; the same bearer token reaches it."""
    server, storage, _ = _install_production_app(monkeypatch)
    body = {"tool": "mid_write", "arguments": {"space_id": "proj", "filename": "n.md", "content": "x"}}
    with TestClient(server.create_app(), base_url="http://localhost:8002") as client:
        operator = client.post("/api/tool", headers={"Authorization": f"Bearer {OPERATOR}"}, json=body)
        legacy = client.post("/api/tool", headers={"Authorization": f"Bearer {LEGACY}"}, json=body)
        agent = client.post("/api/tool", headers={"Authorization": f"Bearer {AGENT}"}, json=body)
        unknown = client.post(
            "/api/tool", headers={"Authorization": f"Bearer {AGENT}"}, json={"tool": "no_such_tool"}
        )

    assert operator.json() == {"status": "not_found", "message": "Space 'proj' not found"}
    assert legacy.json() == operator.json()
    assert agent.status_code == 200
    assert agent.json() == {"status": "forbidden", "message": REFUSAL_MESSAGE}
    assert unknown.json() == {"status": "error", "message": "Unknown tool: no_such_tool"}
    assert storage.exists.await_count == 2
    entries = _refusal_records(audit)
    assert [(entry["route"], entry["tool"], entry["client"]) for entry in entries] == [
        ("api_tool", "mid_write", "agent-worker")
    ]


@pytest.mark.asyncio
async def test_call_tool_direct_reads_the_console_identity(monkeypatch, audit):
    """The in-process proxy applies the guard with the contextvar identity."""
    from live_mem.core import storage as storage_module
    from live_mem import tools as tools_package

    mcp = HivemindMCPServer("operator-guard-direct")
    register_all_tools(mcp)
    monkeypatch.setattr(tools_package, "_mcp_ref", mcp)
    storage = _Storage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: storage)
    auth_context._fresh_token_store.clear()
    auth_context._invalidated_token_hashes.clear()
    arguments = {"space_id": "proj", "filename": "n.md", "content": "x"}

    ambient = auth_context.current_token_info.set({**_identity("manage"), "operator_tools": False})
    try:
        refused = await call_tool_direct("bank_write", arguments)
    finally:
        auth_context.current_token_info.reset(ambient)
    ambient = auth_context.current_token_info.set(_identity("manage"))
    try:
        allowed = await call_tool_direct("bank_write", arguments)
    finally:
        auth_context.current_token_info.reset(ambient)

    assert refused == {"status": "forbidden", "message": REFUSAL_MESSAGE}
    assert allowed == {"status": "not_found", "message": "Space 'proj' not found"}
    assert [entry["route"] for entry in _refusal_records(audit)] == ["api_tool"]


# ──────────────────────────────────────────────────────────────────────────
# Token record and creation paths
# ──────────────────────────────────────────────────────────────────────────


def _record(name: str, suffix: str, permissions: list[str], **fields) -> TokenInfo:
    return TokenInfo(
        hash=f"sha256:{suffix * 64}",
        name=name,
        permissions=permissions,
        space_ids=fields.pop("space_ids", []),
        created_at="2026-10-12T00:00:00+00:00",
        **fields,
    )


def _service(monkeypatch, *records: TokenInfo, raw_tokens: list[dict] | None = None):
    from live_mem.core import tokens as tokens_module

    storage = FakeStorage()
    store = TokensStore(tokens=list(records)).model_dump()
    if raw_tokens is not None:
        store["tokens"] = raw_tokens
    storage.objects[TOKENS_KEY] = json.dumps(store)
    monkeypatch.setattr(tokens_module, "get_storage", lambda: storage)
    auth_context._fresh_token_store.clear()
    auth_context._invalidated_token_hashes.clear()
    return TokenService(), storage


def _stored(storage: FakeStorage) -> dict[str, dict]:
    data = json.loads(storage.objects[TOKENS_KEY])
    return {token["name"]: token for token in data["tokens"]}


@pytest.mark.asyncio
async def test_record_without_the_attribute_reads_as_allowed_and_non_boolean_is_corrupt(monkeypatch):
    legacy = _record("pre-upgrade", "a", ["read", "write", "manage"]).model_dump()
    legacy.pop("operator_tools", None)
    service, storage = _service(monkeypatch, raw_tokens=[legacy])
    raw_token = "lm_fixture_plaintext"
    import hashlib

    legacy["hash"] = "sha256:" + hashlib.sha256(raw_token.encode()).hexdigest()
    storage.objects[TOKENS_KEY] = json.dumps({"version": 2, "tokens": [legacy]})

    assert TokenInfo().operator_tools is True
    validated = await service.validate_token(raw_token)
    assert validated is not None and validated["operator_tools"] is True

    corrupt = {**legacy, "operator_tools": "yes"}
    with pytest.raises(RuntimeError, match="Corrupt token registry"):
        service._store_from_data({"version": 2, "tokens": [corrupt]})


@pytest.mark.asyncio
async def test_token_create_stores_refused_and_admin_create_token_stores_allowed_by_default(monkeypatch):
    manager = _record("manager", "b", ["read", "write", "manage"])
    service, storage = _service(monkeypatch, manager)

    delegated = await service.create_delegated_token(
        actor_token_hash=manager.hash, name="agent-worker", permissions="read,write,manage"
    )
    assert delegated["status"] == "created" and delegated["operator_tools"] is False

    default_admin = await service.create_token(name="ops-cli", permissions="read,write,manage")
    assert default_admin["status"] == "created" and default_admin["operator_tools"] is True

    explicit = await service.create_token(
        name="agent-two", permissions="read,write", operator_tools=False
    )
    assert explicit["status"] == "created" and explicit["operator_tools"] is False

    stored = _stored(storage)
    assert stored["agent-worker"]["operator_tools"] is False
    assert stored["ops-cli"]["operator_tools"] is True
    assert stored["agent-two"]["operator_tools"] is False


@pytest.mark.asyncio
async def test_admin_update_paths_change_the_attribute_and_invalidate_the_identity(monkeypatch):
    one = _record("agent-one", "c", ["read", "write"], operator_tools=False)
    two = _record("agent-two", "d", ["read", "write"], operator_tools=False)
    revoked = _record("agent-old", "e", ["read"], operator_tools=False, revoked=True)
    service, storage = _service(monkeypatch, one, two, revoked)
    auth_context._fresh_token_store[one.hash] = {"token_hash": one.hash}

    invalid = await service.update_token(token_hash=one.hash, operator_tools="maybe")
    assert invalid["status"] == "error" and "operator_tools" in invalid["message"]
    assert _stored(storage)["agent-one"]["operator_tools"] is False

    updated = await service.update_token(token_hash=one.hash, operator_tools="true")
    assert updated["status"] == "ok"
    assert updated["operator_tools_before"] is False and updated["operator_tools_after"] is True
    assert "mcp_reconnect_required" not in updated
    assert _stored(storage)["agent-one"]["operator_tools"] is True
    assert one.hash in auth_context._invalidated_token_hashes
    assert one.hash not in auth_context._fresh_token_store

    unchanged = await service.update_token(token_hash=one.hash, operator_tools="TRUE")
    assert unchanged["status"] == "ok" and "operator_tools_before" not in unchanged

    listed = await service.list_tokens()
    assert {t["name"]: t["operator_tools"] for t in listed["tokens"]} == {
        "agent-one": True,
        "agent-two": False,
        "agent-old": False,
    }

    bulk = await service.bulk_update_tokens(name_contains="agent-", operator_tools="true")
    assert bulk["status"] == "ok" and bulk["updated"] == 2
    assert bulk["operations"]["operator_tools"] is True
    report = {entry["name"]: entry for entry in bulk["tokens"]}
    assert report["agent-two"]["before"]["operator_tools"] is False
    assert report["agent-two"]["after"]["operator_tools"] is True
    assert bulk["skipped_revoked"] == [{"name": "agent-old", "hash": revoked.hash}]
    assert _stored(storage)["agent-two"]["operator_tools"] is True
    assert _stored(storage)["agent-old"]["operator_tools"] is False

    nothing = await service.bulk_update_tokens(name_contains="agent-", operator_tools="")
    assert nothing["status"] == "error"


@pytest.mark.asyncio
async def test_whoami_reports_the_effective_attribute(exposed_mcp: HivemindMCPServer):
    refused = await _low_level_call(
        exposed_mcp, "system_whoami", {}, {**_identity("manage"), "operator_tools": False}
    )
    legacy = await _low_level_call(exposed_mcp, "system_whoami", {}, _identity("manage"))
    bootstrap = await _low_level_call(
        exposed_mcp,
        "system_whoami",
        {},
        {"type": "bootstrap", "client_name": "admin", "permissions": ["admin", "read", "write"],
         "allowed_resources": [], "token_hash": None},
    )
    assert refused["operator_tools"] is False
    assert legacy["operator_tools"] is True
    assert bootstrap["operator_tools"] is True


# ──────────────────────────────────────────────────────────────────────────
# Frozen contract (constitution principle III)
# ──────────────────────────────────────────────────────────────────────────


def test_tool_surface_fixture_pins_the_attribute_contract(exposed_mcp: HivemindMCPServer):
    from live_mem.tools import exposure

    fixture = json.loads(
        (ROOT / "tests" / "fixtures" / "tool_surface.json").read_text(encoding="utf-8")
    )
    block = fixture["operator_tool_guard"]
    attribute = block["token_attribute"]
    assert attribute == "operator_tools"
    assert attribute in TokenInfo.model_fields
    assert TokenInfo.model_fields[attribute].default is block["absent_record_reads_as"] is True
    assert attribute in auth_context._REQUEST_TOKEN_SCALAR_FIELDS

    tools = exposed_mcp._tool_manager._tools
    assert block["creation_defaults"] == {"token_create": False, "admin_create_token": True}
    assert attribute not in tools["token_create"].parameters["properties"]
    for name in block["tools_with_parameter"]:
        assert attribute in tools[name].parameters["properties"], name
    assert set(block["tools_with_parameter"]) == {
        name for name, tool in tools.items() if attribute in tool.parameters["properties"]
    }
    assert tools["admin_create_token"].parameters["properties"][attribute]["default"] is True
    assert exposure.OPERATOR_TOOL_REFUSAL["status"] == block["refusal_status"] == "forbidden"
    assert exposure.OPERATOR_TOOLS_REFUSED == block["refusal_cause"] == "operator_tools_refused"
    assert exposure.OPERATOR_TOOL_REFUSAL["message"] == REFUSAL_MESSAGE


# ──────────────────────────────────────────────────────────────────────────
# CLI (Click and shell)
# ──────────────────────────────────────────────────────────────────────────


def _click(monkeypatch, identity: dict, argv: list[str]):
    import sys

    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from cli import commands

    calls = []

    class FakeClient:
        def __init__(self, url, token):
            assert url and token is not None

        async def call_tool(self, tool_name, args):
            calls.append((tool_name, args))
            if tool_name == "system_whoami":
                return identity
            return {"status": "created" if tool_name.endswith("create_token") else "ok",
                    "name": args.get("name", ""), "permissions": ["read"], "tokens": [],
                    "updated": 0, "filters": {}, "operations": {}}

    monkeypatch.setattr(commands, "MCPClient", FakeClient)
    result = CliRunner().invoke(commands.cli, argv)
    return result, calls


def test_cli_token_commands_carry_the_attribute(monkeypatch):
    admin = {"status": "ok", "auth_type": "token", "permissions": ["read", "write", "manage", "admin"]}
    manager = {"status": "ok", "auth_type": "token", "permissions": ["read", "write", "manage"]}

    result, calls = _click(monkeypatch, admin, ["token", "create", "agent-x", "-p", "read,write", "--no-operator-tools", "--json"])
    assert result.exit_code == 0, result.output
    assert calls[-1] == ("admin_create_token", {
        "name": "agent-x", "permissions": "read,write", "expires_in_days": 0, "email": "",
        "space_ids": "", "operator_tools": False,
    })

    result, calls = _click(monkeypatch, admin, ["token", "create", "ops", "-p", "read,write,manage", "--json"])
    assert result.exit_code == 0, result.output
    assert "operator_tools" not in calls[-1][1]

    result, calls = _click(monkeypatch, manager, ["token", "create", "agent-y", "-p", "read,write", "--operator-tools"])
    assert result.exit_code == 2
    assert "refuses operator tools" in result.output
    assert calls == [("system_whoami", {})]

    result, calls = _click(monkeypatch, admin, ["token", "update", "sha256:" + "a" * 16, "--operator-tools", "false"])
    assert result.exit_code == 0, result.output
    assert calls == [("admin_update_token", {"token_hash": "sha256:" + "a" * 16, "operator_tools": "false"})]

    result, calls = _click(monkeypatch, admin, ["token", "bulk-update", "--names", "a,b", "--operator-tools", "true", "--confirm"])
    assert result.exit_code == 0, result.output
    assert calls[-1][0] == "admin_bulk_update_tokens"
    assert calls[-1][1]["operator_tools"] == "true"


@pytest.mark.asyncio
async def test_shell_token_update_forwards_the_attribute(monkeypatch):
    import sys

    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from cli import shell

    calls = []

    class FakeClient:
        async def call_tool(self, tool_name, args):
            calls.append((tool_name, args))
            return {"status": "ok", "message": "Token updated"}

    await shell._handle_token(FakeClient(), ["update", "sha256:" + "b" * 16, "--operator-tools", "false"], False)
    assert calls == [("admin_update_token", {"token_hash": "sha256:" + "b" * 16, "operator_tools": "false"})]


def test_cli_token_list_and_created_renderers_show_the_attribute():
    import sys

    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from cli import display

    with display.console.capture() as capture:
        display.show_token_list({
            "status": "ok",
            "total": 2,
            "tokens": [
                {"hash": "sha256:" + "a" * 64, "name": "ops", "permissions": ["read", "write", "manage"],
                 "space_ids": [], "operator_tools": True},
                {"hash": "sha256:" + "b" * 64, "name": "agent", "permissions": ["read", "write"],
                 "space_ids": [], "operator_tools": False},
            ],
        })
    rendered = capture.get()
    assert "Operator tools" in rendered
    assert "yes" in rendered and "no" in rendered

    with display.console.capture() as capture:
        display.show_token_created({
            "status": "created", "name": "agent", "token": "lm_x", "token_hash": "sha256:" + "c" * 64,
            "permissions": ["read", "write"], "space_ids": [], "operator_tools": False,
        })
    assert "refused" in capture.get()
