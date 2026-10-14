"""Replay of wrong tool calls through an in-process ``HivemindMCPServer``.

Every refused call must reach the agent as an MCP error result carrying the
Guidance error payload (``error_kind`` and ``remediation`` next to the
unchanged ``status`` and ``message``). Specification:
``specs/001-tool-guidance-remediation/spec.md`` (FR-024, SC-001, SC-002).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.types import CallToolRequest, CallToolResult

import live_mem.auth.context as auth_context
from live_mem.auth.context import bind_mcp_request_identity
from live_mem.tools import register_all_tools
from live_mem.tools.exposure import HivemindMCPServer
from tests.test_p10_tool_exposure import _mcp_request, _request_context

ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = ROOT / "specs" / "001-tool-guidance-remediation"

# A ``read,write`` token with access to space ``p`` only.
TOKEN = {
    "type": "token",
    "client_name": "agent-p",
    "permissions": ["read", "write"],
    "allowed_resources": ["p"],
    "token_hash": "sha256:" + "a" * 64,
}


def _token(permissions: list[str], allowed: list[str] | None = None, **extra) -> dict:
    return {
        **TOKEN,
        "permissions": list(permissions),
        "allowed_resources": ["p"] if allowed is None else list(allowed),
        **extra,
    }


@pytest.fixture(autouse=True)
def _clear_auth_stores():
    auth_context._fresh_token_store.clear()
    auth_context._invalidated_token_hashes.clear()
    yield
    auth_context._fresh_token_store.clear()
    auth_context._invalidated_token_hashes.clear()


@pytest.fixture
def replay_server() -> HivemindMCPServer:
    """In-process server with the real registry and the identity middleware.

    A plain ``MCPServer`` would skip the ``HivemindMCPServer.call_tool``
    override, so the conversion point would not run (research.md, Item 2).
    """
    mcp = HivemindMCPServer("guidance-replay", middleware=[bind_mcp_request_identity])
    register_all_tools(mcp)
    return mcp


async def call(
    mcp: HivemindMCPServer, name: str, arguments: dict, token_info: dict | None = TOKEN
) -> CallToolResult:
    """Call ``name`` through the low-level ``tools/call`` handler with a bound identity."""
    handler = mcp._lowlevel_server._request_handlers["tools/call"].handler
    request = CallToolRequest(params={"name": name, "arguments": arguments})
    with _mcp_request(token_info):
        return await handler(_request_context(token_info), request.params)


def payload_of(result: CallToolResult) -> dict:
    assert result.content and result.content[0].type == "text"
    return json.loads(result.content[0].text)


def _assert_error_result(result: CallToolResult) -> dict:
    payload = payload_of(result)
    assert result.is_error is True, payload
    assert result.structured_content == payload
    return payload


# ──────────────────────────────────────────────────────────────────────────
# Phase 2: the conversion point (T008, T009)
# ──────────────────────────────────────────────────────────────────────────


async def test_refusal_is_an_mcp_error_result_with_the_payload_twice(
    replay_server, monkeypatch
):
    result = await call(
        replay_server,
        "short_note",
        {"space_id": "q", "category": "observation", "content": "x"},
    )
    payload = _assert_error_result(result)
    assert payload["status"] == "error"
    assert payload["message"] == "Access denied to space 'q'"
    assert "error_kind" in payload

    class Spaces:
        async def list_spaces(self, allowed_space_ids=None, include_counts=True):
            return {"status": "ok", "spaces": [], "total": 0}

    from live_mem.core import space as space_module

    monkeypatch.setattr(space_module, "get_space_service", lambda: Spaces())
    ok = await call(replay_server, "space_list", {})
    assert ok.is_error is False
    assert ok.structured_content is None
    assert len(ok.content) == 1
    assert ok.content[0].text == json.dumps(
        {"status": "ok", "spaces": [], "total": 0}, indent=2
    )


async def test_input_schema_failures_name_the_field_only(replay_server):
    missing = await call(
        replay_server, "short_note", {"space_id": "p", "category": "observation"}
    )
    payload = _assert_error_result(missing)
    assert payload["status"] == "error"
    assert payload["error_kind"] == "missing_argument"
    assert payload["remediation"]["missing_arguments"] == ["content"]
    lowered = payload["message"].lower()
    assert "pydantic" not in lowered and "validation" not in lowered
    assert "observation" not in payload["message"]

    invalid = await call(replay_server, "short_read", {"space_id": "p", "limit": "ten"})
    payload = _assert_error_result(invalid)
    assert payload["error_kind"] == "invalid_argument"
    assert payload["remediation"]["missing_arguments"] == ["limit"]
    assert "ten" not in json.dumps(payload)


async def test_rollout_setting_false_keeps_the_payload_and_clears_is_error(
    replay_server, monkeypatch
):
    from live_mem.config import get_settings

    arguments = {"space_id": "q", "category": "observation", "content": "x"}
    # The default value is true; set it explicitly so the check also holds
    # when the process started with MCP_REFUSAL_IS_ERROR=false (quickstart).
    monkeypatch.setattr(get_settings(), "mcp_refusal_is_error", True)
    default = await call(replay_server, "short_note", arguments)
    assert default.is_error is True

    monkeypatch.setattr(get_settings(), "mcp_refusal_is_error", False)
    restored = await call(replay_server, "short_note", arguments)
    assert restored.is_error is False
    assert payload_of(restored) == payload_of(default)
    assert restored.structured_content == default.structured_content


# ──────────────────────────────────────────────────────────────────────────
# User Story 1: replay cases (T018 to T022)
# ──────────────────────────────────────────────────────────────────────────

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from unittest.mock import AsyncMock

import jsonschema

from live_mem.core.live import VALID_CATEGORIES
from live_mem.tools.exposure import TOOL_EXPOSURES, ToolAudience, discovery_names_for_token
from live_mem.tools.guidance import ErrorKind

SCHEMA = json.loads(
    (SPEC_DIR / "contracts" / "guidance-error-payload.schema.json").read_text(encoding="utf-8")
)
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)

MANAGE = _token(["read", "write", "manage"])
FRESH = _token(["read", "write"], allowed=[])
OPERATOR_NAMES = {
    name
    for entry in TOOL_EXPOSURES
    if entry.audience is ToolAudience.OPERATOR
    for name in (entry.canonical_name, *entry.aliases)
}


class _Storage:
    """Storage double: no space exists."""

    def __init__(self) -> None:
        self.exists = AsyncMock(return_value=False)
        self.get = AsyncMock(return_value=None)


class _Registry:
    """Engine registry double: the route decides the short_note outcome."""

    def __init__(self, *, sink=None, short_raises: Exception | None = None):
        self._sink = sink
        self._short_raises = short_raises

    async def resolve_sink(self, space_id):
        return self._sink

    async def short_engine(self, space_id):
        if self._short_raises is not None:
            raise self._short_raises
        engine = type("Engine", (), {})()
        engine.write_sink = self._sink

        async def write_note(**kwargs):
            from live_mem.core.live import get_live_service

            return await get_live_service().write_note(**kwargs)

        engine.write_note = write_note
        return engine

    async def mid_engine(self, space_id):
        engine = type("Engine", (), {})()
        engine.write_sink = self._sink
        return engine


def _direct_local_sink():
    from live_mem.core.write_sink import DirectLocalWriteSink

    return DirectLocalWriteSink(_Storage())


@dataclass
class Case:
    id: str
    tool: str
    arguments: dict
    check: Callable[[dict], None]
    token: dict | None = field(default_factory=lambda: dict(TOKEN))
    setup: Callable[..., Awaitable[Callable[[], Awaitable[None]] | None]] | None = None
    refusal: bool = True


def _kind(payload: dict, kind: ErrorKind) -> None:
    assert payload["error_kind"] == kind.value, payload


def _no_internal_error(payload: dict) -> None:
    assert "Internal server error" not in json.dumps(payload), payload


# --- setups ---------------------------------------------------------------


async def _tombstone(monkeypatch, _mcp):
    """Simulate a token re-scoped during the session: the request carries no
    identity any more and the session's token hash is tombstoned."""
    auth_context._invalidated_token_hashes.add(TOKEN["token_hash"])
    reset = auth_context.current_token_info.set(dict(TOKEN))

    async def cleanup():
        auth_context.current_token_info.reset(reset)

    return cleanup


def _registry_setup(**kwargs):
    async def setup(monkeypatch, _mcp):
        from live_mem.core import engines

        monkeypatch.setattr(engines, "get_engine_registry", lambda: _Registry(**kwargs))

    return setup


async def _storage_setup(monkeypatch, _mcp):
    from live_mem.core import storage

    monkeypatch.setattr(storage, "get_storage", lambda: _Storage())


async def _queued_job_setup(monkeypatch, _mcp):
    from live_mem.core import consolidation_queue as queue_module
    from live_mem.core import engines

    monkeypatch.setattr(
        engines, "get_engine_registry", lambda: _Registry(sink=_direct_local_sink())
    )
    queue_module.reset_consolidation_queue_for_tests()

    async def idle_worker(self, space_id):
        return None

    monkeypatch.setattr(queue_module.ConsolidationQueueService, "_worker", idle_worker)
    queue = queue_module.get_consolidation_queue()
    await queue.enqueue(space_id="p", agent="other-agent", requested_by="other-agent")
    await queue.enqueue(space_id="p", agent=TOKEN["client_name"], requested_by=TOKEN["client_name"])

    async def cleanup():
        # FR-007: the replayed call queued no second job for the agent.
        assert len(queue._queues["p"]) == 1, dict(queue._queues)
        queue_module.reset_consolidation_queue_for_tests()

    return cleanup


async def _lock_held_setup(monkeypatch, _mcp):
    from live_mem.core.locks import get_lock_manager

    lock = get_lock_manager().consolidation("p")
    await lock.acquire()

    async def cleanup():
        lock.release()

    return cleanup


# --- checks ---------------------------------------------------------------


def _check_invalid_space_id(payload):
    _kind(payload, ErrorKind.INVALID_SPACE_ID)
    assert payload["remediation"]["missing_arguments"] == ["space_id"]
    assert "for example" in payload["remediation"]["reason"]


def _check_reserved_space_id(payload):
    _kind(payload, ErrorKind.INVALID_SPACE_ID)
    reason = payload["remediation"]["reason"].lower()
    assert "format" in reason or "letters" in reason
    assert "exist" not in reason


def _check_access_denied(payload):
    _kind(payload, ErrorKind.SPACE_ACCESS_DENIED)
    assert payload["accessible_spaces"] == ["p"]
    assert payload["remediation"]["next_tool"] == "space_list"


def _check_fresh_token(payload):
    _kind(payload, ErrorKind.SPACE_ACCESS_DENIED)
    assert payload["accessible_spaces"] == []
    assert "space_invite_token" in payload["remediation"]["reason"]
    assert "system_whoami" in payload["remediation"]["reason"]


def _check_manage_required(payload):
    _kind(payload, ErrorKind.PERMISSION_INSUFFICIENT)
    assert payload["required_permission"] == "manage"
    assert payload["current_permissions"] == ["read", "write"]
    assert "next_tool" not in payload["remediation"]
    assert payload["remediation"]["retryable"] is False
    reason = payload["remediation"]["reason"]
    assert "operator" in reason and ("manage" in reason or "admin" in reason)
    assert "bypass" not in reason.lower() and "workaround" not in reason.lower()


def _check_token_scope_changed(payload):
    _kind(payload, ErrorKind.TOKEN_SCOPE_CHANGED)
    assert payload["remediation"]["required_prior_step"] == "refresh the tool list"
    assert payload["message"] == "Authentication required"


def _check_invalid_category(payload):
    _kind(payload, ErrorKind.INVALID_ARGUMENT)
    reason = payload["remediation"]["reason"]
    for category in VALID_CATEGORIES:
        assert category in reason
    assert "for example" in reason
    assert payload["remediation"]["missing_arguments"] == ["category"]


def _check_confirm_missing(payload):
    _kind(payload, ErrorKind.MISSING_ARGUMENT)
    assert payload["remediation"]["missing_arguments"] == ["confirm"]
    assert payload["remediation"]["arguments"] == {"confirm": True}


def _check_mid_write_permission(payload):
    _check_manage_required(payload)
    reason = payload["remediation"]["reason"]
    assert "short_note" in reason
    hidden_names_mentioned = {
        name for name in OPERATOR_NAMES - {"mid_write", "bank_write"} if name in reason
    }
    assert hidden_names_mentioned == set(), reason


def _check_write_route_refused(payload):
    _kind(payload, ErrorKind.WRITE_ROUTE_REFUSED)
    _no_internal_error(payload)
    reason = payload["remediation"]["reason"]
    assert payload["remediation"]["retryable"] is False
    assert "operator" in reason.lower()
    assert "fallback" not in reason.lower() and "directlocal" not in reason.lower()


def _check_staged_write(payload):
    _check_write_route_refused(payload)
    assert payload["message"] == "Writes to a shared space are not accepted on this route"


def _check_internal_failure(payload):
    _kind(payload, ErrorKind.INTERNAL_FAILURE)
    assert payload["message"] == "Internal server error"
    assert payload["remediation"]["retryable"] is False


def _check_existing_job(payload):
    assert payload["status"] in {"queued", "running"}
    assert payload["existing_job"]["job_id"] == payload["job_id"]
    assert payload["existing_job"]["status"] == payload["status"]
    assert payload["next_action"] == "return_to_user_without_polling"


def _check_conflict(payload):
    assert payload["status"] == "conflict"
    _kind(payload, ErrorKind.OPERATION_IN_PROGRESS)
    assert payload["remediation"]["retryable"] is True


def _check_compact_failure(payload):
    _kind(payload, ErrorKind.WRITE_ROUTE_REFUSED)
    assert isinstance(payload["remediation"], dict)
    assert payload["remediation"]["reason"]
    assert payload["failure_reason"] == "direct_local_route_required"
    assert payload["remediation"]["retryable"] is False


def _check_client_name_missing(payload):
    # Q6 decided 2026-10-14: a bearer token without a client_name identity is
    # authentication_missing; the agent cannot fix it, an operator must (T067).
    _kind(payload, ErrorKind.AUTHENTICATION_MISSING)
    assert payload["message"] == (
        "A non-empty client_name identity is required to consolidate notes"
    )
    assert payload["remediation"]["retryable"] is False
    assert "client_name" in payload["remediation"]["reason"]


def _check_missing(payload, argument: str) -> None:
    assert payload["remediation"]["missing_arguments"] == [argument]
    assert "for example" in payload["remediation"]["reason"]


def _no_kind(payload, status: str) -> None:
    assert payload["status"] == status
    assert "error_kind" not in payload


def _registry_refused():
    from live_mem.core.engines import RegistryRefused
    from live_mem.core.hivemind.lifecycle import WriteRoute

    return RegistryRefused("p", WriteRoute.REFUSE)


def _corrupted_state():
    from live_mem.core.hivemind.models import CorruptedStateError

    return CorruptedStateError("critical state file is unreadable")


NOTE = {"space_id": "p", "category": "observation", "content": "x"}

CASES: list[Case] = [
    # T018: guard helpers
    Case("no-identity", "short_read", {"space_id": "p"},
         lambda p: _kind(p, ErrorKind.AUTHENTICATION_MISSING), token=None),
    Case("malformed-space-id", "short_read", {"space_id": "Bad Id"}, _check_invalid_space_id),
    Case("reserved-space-id", "short_read", {"space_id": "_backups"}, _check_reserved_space_id),
    Case("derived-namespace-space-id", "short_read",
         {"space_id": "hm-derived-0123456789abcdef"}, _check_reserved_space_id),
    Case("access-denied", "short_note", {**NOTE, "space_id": "q"}, _check_access_denied),
    Case("fresh-token", "short_read", {"space_id": "p"}, _check_fresh_token, token=FRESH),
    Case("space-create-needs-manage", "space_create",
         {"space_id": "new-space", "description": "d"}, _check_manage_required),
    Case("token-scope-changed", "short_read", {"space_id": "p"}, _check_token_scope_changed,
         token=None, setup=_tombstone),
    # T019: handler refusals
    Case("invalid-category", "short_note", {**NOTE, "category": "note"}, _check_invalid_category,
         setup=_registry_setup(sink=_direct_local_sink())),
    Case("space-delete-confirm", "space_delete", {"space_id": "p"}, _check_confirm_missing,
         token=MANAGE),
    Case("backup-restore-missing-id", "backup_restore", {"backup_id": ""},
         lambda p: (_kind(p, ErrorKind.MISSING_ARGUMENT),
                    _check_missing(p, "backup_id")), token=MANAGE),
    Case("ingest-list-limit", "long_ingest_list", {"space_id": "p", "limit": 0},
         lambda p: (_kind(p, ErrorKind.INVALID_ARGUMENT), _check_missing(p, "limit"))),
    Case("consolidate-other-agent", "mid_consolidate", {"space_id": "p", "agent": "other"},
         _check_manage_required),
    Case("mid-write-hidden", "mid_write", {"space_id": "p", "filename": "a.md", "content": "x"},
         _check_mid_write_permission),
    Case("alias-access-denied", "live_note", {**NOTE, "space_id": "q"}, _check_access_denied),
    Case("consolidate-no-client-name", "mid_consolidate", {"space_id": "p"},
         _check_client_name_missing, token=_token(["read", "write"], client_name="")),
    # T020: write-route and internal refusals
    Case("staged-space", "short_note", NOTE, _check_staged_write,
         setup=_registry_setup(sink=object())),
    Case("registry-refused", "short_note", NOTE, _check_write_route_refused,
         setup=_registry_setup(short_raises=_registry_refused())),
    Case("corrupted-state", "short_note", NOTE, _check_write_route_refused,
         setup=_registry_setup(short_raises=_corrupted_state())),
    Case("unexpected-exception", "short_note", NOTE, _check_internal_failure,
         setup=_registry_setup(short_raises=RuntimeError("storage endpoint secret"))),
    # T021: results that are not refusals
    Case("existing-consolidation-job", "mid_consolidate", {"space_id": "p"},
         _check_existing_job, setup=_queued_job_setup, refusal=False),
    Case("compact-conflict", "bank_compact", {"space_id": "p", "dry_run": False},
         _check_conflict, token=MANAGE, setup=_lock_held_setup, refusal=False),
    Case("compact-failure", "bank_compact", {"space_id": "p", "dry_run": False},
         _check_compact_failure, token=MANAGE, setup=_registry_setup(sink=object())),
    Case("not-found", "mid_list", {"space_id": "p"},
         lambda p: _no_kind(p, "not_found"), setup=_storage_setup, refusal=False),
]


async def _run_case(mcp, monkeypatch, case: Case) -> dict:
    cleanup = None
    if case.setup is not None:
        cleanup = await case.setup(monkeypatch, mcp)
    try:
        result = await call(mcp, case.tool, case.arguments, case.token)
    finally:
        if cleanup is not None:
            await cleanup()
    payload = payload_of(result)
    if case.refusal:
        assert result.is_error is True, payload
        assert result.structured_content == payload
    else:
        assert result.is_error is False, payload
    return payload


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
async def test_replay_case(replay_server, monkeypatch, case: Case):
    payload = await _run_case(replay_server, monkeypatch, case)
    case.check(payload)
    if case.refusal or "error_kind" in payload:
        VALIDATOR.validate(payload)
        _no_internal_error(payload) if payload["error_kind"] != "internal_failure" else None


async def test_alias_refusal_names_the_canonical_tool(replay_server, monkeypatch):
    alias = await _run_case(replay_server, monkeypatch, CASES[14])
    canonical = await _run_case(replay_server, monkeypatch, CASES[4])
    assert CASES[14].tool == "live_note" and CASES[4].tool == "short_note"
    assert alias["remediation"]["reason"] == (
        canonical["remediation"]["reason"] + " Canonical tool: short_note."
    )
    alias["remediation"]["reason"] = canonical["remediation"]["reason"]
    assert alias == canonical


async def test_replay_covers_every_error_kind_and_each_next_tool_works(
    replay_server, monkeypatch
):
    """FR-024, SC-001: every kind at least once; no unclassified refusal."""
    seen: dict[str, list[str]] = {}
    follow_ups: list[tuple[Case, dict]] = []
    refused = 0
    for case in CASES:
        payload = await _run_case(replay_server, monkeypatch, case)
        if "error_kind" in payload:
            VALIDATOR.validate(payload)
            seen.setdefault(payload["error_kind"], []).append(case.id)
        if case.refusal:
            refused += 1
            if payload["remediation"].get("next_tool"):
                follow_ups.append((case, payload))
    assert refused >= 20

    excluded = {
        ErrorKind.OPERATOR_TOOLS_REFUSED,  # produced only by specification 002
        ErrorKind.AUDIENCE_UNRESOLVED,  # produced only by specification 002
        ErrorKind.REQUIRED_PRIOR_STEP_MISSING,  # no producer yet (research.md, Q2)
        ErrorKind.UNCLASSIFIED_REFUSAL,
    }
    missing = {kind.value for kind in ErrorKind if kind not in excluded} - set(seen)
    assert missing == set(), missing
    # No refusal site is left unclassified (Q6 decided; tasks.md T067).
    assert ErrorKind.UNCLASSIFIED_REFUSAL.value not in seen, seen

    class Spaces:
        async def list_spaces(self, allowed_space_ids=None, include_counts=True):
            return {"status": "ok", "spaces": [], "total": 0}

    from live_mem.core import space as space_module

    monkeypatch.setattr(space_module, "get_space_service", lambda: Spaces())
    assert follow_ups, "no remediation named a next_tool"
    for case, payload in follow_ups:
        next_tool = payload["remediation"]["next_tool"]
        token = case.token
        if next_tool not in discovery_names_for_token(token):
            continue
        result = await call(
            replay_server, next_tool, payload["remediation"].get("arguments", {}), token
        )
        follow = payload_of(result)
        if result.is_error:
            assert follow["error_kind"] in {k.value for k in ErrorKind}, (case.id, follow)
        else:
            assert follow["status"] == "ok", (case.id, follow)


# ──────────────────────────────────────────────────────────────────────────
# User Story 4: the CLI client over real HTTP (T057)
# ──────────────────────────────────────────────────────────────────────────


async def test_cli_client_receives_the_payload_over_real_http(monkeypatch):
    from scripts.cli.client import MCPClient
    from tests.test_mcp2_transport import _running_http
    from tests.test_operator_tool_guard import AGENT, _install_production_app

    server, _, _ = _install_production_app(monkeypatch)
    async with _running_http(server.create_app()) as url:
        result = await MCPClient(url, AGENT, timeout=5).call_tool("space_info", {"space_id": "q"})
    assert result["status"] == "error"
    assert result["message"] == "Access denied to space 'q'"
    assert result["error_kind"] == "space_access_denied"
    assert result["remediation"]["next_tool"] == "space_list"
    assert result["accessible_spaces"] == ["proj"]


def test_cli_run_tool_shows_the_message_of_a_refused_call(monkeypatch):
    import sys

    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from cli import commands

    payload = {
        "status": "error",
        "message": "Access denied to space 'q'",
        "error_kind": "space_access_denied",
        "accessible_spaces": ["p"],
        "remediation": {"next_tool": "space_list", "reason": "r", "retryable": True},
    }

    class FakeClient:
        def __init__(self, url, token):
            pass

        async def call_tool(self, tool_name, args):
            return payload

    shown: list[str] = []
    monkeypatch.setattr(commands, "MCPClient", FakeClient)
    monkeypatch.setattr(commands, "show_error", shown.append)
    exits: list[int] = []
    ctx = type("Ctx", (), {"obj": {"url": "http://localhost", "token": "t"}, "exit": lambda self, code: exits.append(code)})()
    commands._run_tool(ctx, "space_info", {"space_id": "q"}, on_success=lambda r: None)
    assert exits == [1]
    assert shown == ["Access denied to space 'q'"]
