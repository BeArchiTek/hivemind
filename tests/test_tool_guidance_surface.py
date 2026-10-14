"""Contract tests for the tool guidance feature.

Covers the closed ``error_kind`` set, the tool description contract, the
discovery budget, the server instructions and the documentation.
Specification: ``specs/001-tool-guidance-remediation/spec.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = ROOT / "specs" / "001-tool-guidance-remediation"
SCHEMA = json.loads(
    (SPEC_DIR / "contracts" / "guidance-error-payload.schema.json").read_text(encoding="utf-8")
)


# ──────────────────────────────────────────────────────────────────────────
# error_kind registry (T005)
# ──────────────────────────────────────────────────────────────────────────


def test_error_kind_values_equal_the_contract_schema_enum():
    from live_mem.tools.guidance import ErrorKind

    assert [kind.value for kind in ErrorKind] == SCHEMA["properties"]["error_kind"]["enum"]
    assert [kind.value for kind in ErrorKind] == [
        "authentication_missing",
        "token_scope_changed",
        "invalid_space_id",
        "space_access_denied",
        "permission_insufficient",
        "missing_argument",
        "invalid_argument",
        "required_prior_step_missing",
        "operation_in_progress",
        "write_route_refused",
        "internal_failure",
        "operator_tools_refused",
        "audience_unresolved",
        "unclassified_refusal",
    ]


# ──────────────────────────────────────────────────────────────────────────
# Tool description contract (T039, T040)
# ──────────────────────────────────────────────────────────────────────────

import hashlib
import re

from mcp.server.mcpserver import MCPServer

from live_mem.core.live import VALID_CATEGORIES
from live_mem.tools import register_all_tools
from live_mem.tools.exposure import (
    DISCOVERY_NAMES_BY_PERMISSION,
    DISCOVERY_SCHEMA_BUDGET_BYTES,
    TOOL_EXPOSURES,
    HivemindMCPServer,
    ToolAudience,
)
from tests.test_p10_tool_exposure import _identity, _list_result

SECTION_HEADINGS = ("Use when", "Do not use when", "Before calling", "Peer tools", "Result")
PERMISSION_RE = re.compile(r"\bpermission (public|read|write|manage|admin)\b")
AGENT_CORE = [e for e in TOOL_EXPOSURES if e.audience is ToolAudience.AGENT_CORE]
OPERATOR = [e for e in TOOL_EXPOSURES if e.audience is ToolAudience.OPERATOR]
# Operator tools replaced by an agent tool (contracts/tool-description-contract.md).
OPERATOR_AGENT_ALTERNATIVES = {"mid_write": ("short_note", "mid_consolidate")}


@pytest.fixture(scope="module")
def registered_tools() -> dict:
    mcp = MCPServer("guidance-surface")
    register_all_tools(mcp)
    return mcp._tool_manager._tools


def _sections(description: str) -> dict[str, str]:
    """Split a description into its sections by heading; the purpose is ''."""
    positions = [(description.find(f"{h}:"), h) for h in SECTION_HEADINGS]
    assert all(pos >= 0 for pos, _ in positions), description
    assert [h for _, h in sorted(positions)] == list(SECTION_HEADINGS), description
    out = {"": description[: positions[0][0]]}
    ordered = sorted(positions)
    for index, (pos, heading) in enumerate(ordered):
        end = ordered[index + 1][0] if index + 1 < len(ordered) else len(description)
        out[heading] = description[pos + len(heading) + 1 : end].strip()
    return out


@pytest.mark.parametrize("entry", AGENT_CORE, ids=[e.canonical_name for e in AGENT_CORE])
def test_agent_core_description_follows_the_section_contract(registered_tools, entry):
    description = registered_tools[entry.canonical_name].description or ""
    sections = _sections(description)
    purpose = sections[""].strip()
    assert purpose and purpose.count(". ") <= 2, purpose
    match = PERMISSION_RE.search(sections["Before calling"])
    assert match is not None, sections["Before calling"]
    assert match.group(1) == entry.minimum_permission.value
    if entry.space_scope_argument:
        assert entry.space_scope_argument in sections["Before calling"]
    for heading in SECTION_HEADINGS:
        assert sections[heading], (entry.canonical_name, heading)


def test_short_note_and_mid_consolidate_carry_the_fixed_statements(registered_tools):
    note = registered_tools["short_note"].description
    for category in VALID_CATEGORIES:
        assert category in note
    assert "identity" in note and "token" in note
    assert "mid_write" in note and "not an alternative" in note
    consolidate = registered_tools["mid_consolidate"].description
    assert "at most once" in consolidate
    assert "after user validation" in consolidate
    assert "without polling" in consolidate


def _states_allowed_values(name: str, schema: dict, description: str) -> list[str]:
    problems = []
    lowered = description.lower()
    kind = schema.get("type")
    if kind is None and isinstance(schema.get("anyOf"), list):
        kinds = {item.get("type") for item in schema["anyOf"]} - {"null"}
        kind = next(iter(kinds)) if len(kinds) == 1 else None
    if "enum" in schema:
        for value in schema["enum"]:
            if str(value) not in description:
                problems.append(f"enum value {value!r} not listed")
    elif kind == "boolean":
        if "true" not in lowered or "false" not in lowered:
            problems.append("boolean must name true and false")
    elif kind == "integer":
        if not re.search(r"\b(between|at least|at most|whole number|or more|\d+\.\.\d+|\d+-\d+)\b", lowered):
            problems.append("integer must state its range")
    else:
        if not any(marker in lowered for marker in ("for example", "format", "one of", "|")):
            problems.append("must state the format or give an example")
    if "default" in schema and "default" not in lowered:
        problems.append("must state the default")
    return problems


def test_every_parameter_states_allowed_values_or_format_and_default(registered_tools):
    findings = []
    for name, tool in registered_tools.items():
        for field_name, schema in tool.parameters.get("properties", {}).items():
            description = schema.get("description") or ""
            if not description.strip():
                findings.append(f"{name}.{field_name}: empty description")
                continue
            for problem in _states_allowed_values(field_name, schema, description):
                findings.append(f"{name}.{field_name}: {problem}")
    assert not findings, "\n".join(findings)
    assert all(c in registered_tools["short_note"].parameters["properties"]["category"]["description"]
               for c in VALID_CATEGORIES)


@pytest.mark.parametrize(
    "canonical,alias",
    [(e.canonical_name, a) for e in TOOL_EXPOSURES for a in e.aliases],
)
def test_alias_description_equals_the_canonical_one_and_names_it(registered_tools, canonical, alias):
    assert registered_tools[alias].description == registered_tools[canonical].description
    assert f"Canonical name: {canonical}" in registered_tools[alias].description
    assert alias in registered_tools[alias].description


@pytest.mark.parametrize("entry", OPERATOR, ids=[e.canonical_name for e in OPERATOR])
def test_operator_tool_description_states_it_is_an_operator_tool(registered_tools, entry):
    description = registered_tools[entry.canonical_name].description or ""
    assert description.startswith("Operator tool:"), description[:80]
    for agent_tool in OPERATOR_AGENT_ALTERNATIVES.get(entry.canonical_name, ()):
        assert agent_tool in description


@pytest.mark.asyncio
async def test_each_discovery_profile_stays_within_the_budget():
    mcp = HivemindMCPServer("guidance-budget")
    register_all_tools(mcp)
    sizes = {}
    for permission in ("read", "write", "manage", "admin"):
        result = await _list_result(mcp, _identity(permission))
        wire = {"jsonrpc": "2.0", "id": 1,
                "result": result.model_dump(by_alias=True, exclude_none=True)}
        sizes[permission] = len(
            json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        assert len(result.tools) == len(DISCOVERY_NAMES_BY_PERMISSION[permission])
    assert max(sizes.values()) <= DISCOVERY_SCHEMA_BUDGET_BYTES, sizes


# SHA-256 of tests/fixtures/tool_surface.json as specification 002 left it.
TOOL_SURFACE_FIXTURE_SHA256 = "7887bec06f01cf201313b226999a196255effdf0897a8b374875081ecda94ca4"


def test_tool_surface_fixture_is_unchanged_by_this_feature():
    """FR-023: no tool is added, removed, renamed or re-levelled."""
    path = ROOT / "tests" / "fixtures" / "tool_surface.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == TOOL_SURFACE_FIXTURE_SHA256


# ──────────────────────────────────────────────────────────────────────────
# Server instructions (T049, T050, T051)
# ──────────────────────────────────────────────────────────────────────────

from starlette.testclient import TestClient

INIT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
# Marker phrases of the six FR-013 rule records, in order.
RULE_MARKERS = [
    ("start_of_task", ["space_rules", "mid_read_all", "short_read", "stop before"]),
    ("write_discipline", ["short_note", *VALID_CATEGORIES, "identity", "never write the bank", "secrets"]),
    ("end_of_block", ["mid_consolidate", "at most once", "without polling"]),
    ("permission_model", ["admin", "manage", "write", "read", "system_whoami", "invitation"]),
    ("long_memory", ["long_query", "non-authoritative", "long_push"]),
    ("reading_a_refusal", ["error_kind", "remediation"]),
]


def _initialize(client, token: str) -> dict:
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
                "clientInfo": {"name": "guidance-surface", "version": "1"},
            },
        },
    )
    assert response.status_code == 200, (response.status_code, response.text[:300])
    if response.headers["content-type"].startswith("text/event-stream"):
        return next(
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ") and json.loads(line[6:]).get("id") == 1
        )["result"]
    return response.json()["result"]


def test_initialize_carries_the_rules_in_order_for_every_token(monkeypatch):
    from tests.test_operator_tool_guard import ADMIN_OPERATOR, AGENT, _install_production_app

    server, _, _ = _install_production_app(monkeypatch)
    with TestClient(server.create_app(), base_url="http://localhost:8002") as client:
        agent = _initialize(client, AGENT)["instructions"]
        admin = _initialize(client, ADMIN_OPERATOR)["instructions"]
    assert isinstance(agent, str) and agent.strip()
    assert agent == admin

    from live_mem.instructions import render_instructions

    text = render_instructions()
    assert agent == text
    last = -1
    for identifier, markers in RULE_MARKERS:
        first_marker = text.find(markers[0])
        assert first_marker > last, (identifier, markers[0])
        last = first_marker
        for marker in markers:
            assert marker in text, (identifier, marker)


def _fenced_block_after(markdown: str, heading: str) -> str:
    start = markdown.index(heading)
    open_fence = markdown.index("```", start)
    open_end = markdown.index("\n", open_fence) + 1
    close_fence = markdown.index("```", open_end)
    return markdown[open_end:close_fence]


def _section(markdown: str, heading: str) -> str:
    start = markdown.index(heading)
    level = heading.split(" ")[0]
    next_heading = markdown.find(f"\n{level} ", start + len(heading))
    return markdown[start: next_heading if next_heading > 0 else len(markdown)]


def test_instructions_agree_with_the_two_rule_documents():
    from live_mem.instructions import RULES, render_instructions

    setup = (ROOT / "docs" / "AGENT_MEMORY_SETUP.md").read_text(encoding="utf-8")
    standard = (ROOT / "RULES" / "live-mem.standard.memory.bank.md").read_text(encoding="utf-8")
    block = _fenced_block_after(setup, "## Recommended project instruction block")
    workflow = _section(standard, "## Recommended Agent Workflow")
    sources = {
        "docs/AGENT_MEMORY_SETUP.md": block,
        "RULES/live-mem.standard.memory.bank.md": workflow,
    }
    text = render_instructions()

    assert [rule.identifier for rule in RULES] == [identifier for identifier, _ in RULE_MARKERS]
    for rule in RULES:
        for marker in rule.markers:
            assert marker in text, (rule.identifier, marker)
        for document, markers in rule.source_markers.items():
            source = sources.get(document)
            if source is None:
                source = (ROOT / document).read_text(encoding="utf-8")
            for marker in markers:
                assert marker.lower() in source.lower(), (rule.identifier, document, marker)

    # The seven categories agree between the code, the text and the block.
    for category in VALID_CATEGORIES:
        assert f"`{category}`" in block
    categories_in_block = sorted(re.findall(r"`(observation|decision|todo|insight|question|progress|issue)`", block))
    assert categories_in_block == sorted(VALID_CATEGORIES)
    assert all(category in text for category in VALID_CATEGORIES)

    # FR-014: no endpoint, placeholder, space identifier or token-like string.
    for forbidden in ("http://", "https://", "{HIVEMIND_MCP_SERVER}", "{SPACE}"):
        assert forbidden not in text
    assert not re.search(r"sha256:[0-9a-f]{8,}", text)
    assert not re.search(r"\b[A-Za-z0-9_-]{32,}\b", text)
    assert "the space the token works in" in text



# ──────────────────────────────────────────────────────────────────────────
# Documentation contract (T063)
# ──────────────────────────────────────────────────────────────────────────


def test_docs_list_every_error_kind_with_an_example_and_the_rollout_setting():
    from live_mem.tools.guidance import ErrorKind

    spec = (ROOT / "docs" / "MCP_TOOLS_SPEC.md").read_text(encoding="utf-8")
    for kind in ErrorKind:
        # One list item per value: "- `<kind>`" followed by an example payload or message.
        match = re.search(rf"^- `{kind.value}`[^\n]*\n(?:[^\n]*\n){{0,8}}", spec, re.MULTILINE)
        assert match is not None, kind.value
        assert "example" in match.group(0).lower(), kind.value
    assert '"error_kind"' in spec and '"remediation"' in spec
    assert '"retryable"' in spec and '"next_tool"' in spec
    block = _section(spec, "### Standardized Return Format")
    assert '"status": "conflict"' in block
    assert "isError" in spec and "structuredContent" in spec
    assert "remediation.reason" in spec

    deployment = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "MCP_REFUSAL_IS_ERROR" in deployment
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "MCP_REFUSAL_IS_ERROR=true" in env_example
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "MCP_REFUSAL_IS_ERROR" in changelog and "error_kind" in changelog
