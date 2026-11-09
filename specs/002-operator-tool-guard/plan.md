# Implementation Plan: Per-bearer-token guard for operator tools over MCP

**Branch**: `spec/002-operator-tool-guard` | **Date**: 2026-10-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-operator-tool-guard/spec.md`

## Summary

An agent whose bearer token holds `manage` can call every hidden operator tool
over `/mcp` and through `/api/tool`, because each handler checks only the
permission level ([research.md](research.md)). The plan adds a boolean
`operator_tools` attribute to the token record (absent means allowed), copies
it into the per-request identity, and checks it in one function that both
call routes invoke before the handler runs, keyed on the exposure registry's
audience so every alias is covered. `token_create` creates refusing tokens;
`admin_create_token` creates allowed tokens unless asked otherwise; the three
admin token tools, the CLI and the console let an admin change it. Each
refusal returns `{"status": "forbidden", "message": ...}` and writes one audit
line.

## Technical Context

**Language/Version**: Python 3.13 (`pyproject.toml`)

**Primary Dependencies**: MCP SDK 2.3.0 (`uv.lock`), Pydantic 2, Starlette,
Click and Rich for the CLI

**Storage**: `_system/tokens.json` on S3 through `StorageService`; tests use
`tests/test_lm2_11_access_roles.py`'s `FakeStorage`

**Testing**: pytest with pytest-asyncio; the production ASGI stack is built
with `live_mem.server.create_app()` and driven with Starlette's `TestClient`
(`tests/test_mcp2_risk_f5_session_limits.py`) or with uvicorn on a local
socket for the real CLI client (`tests/test_mcp2_transport.py:127-150`)

**Target Platform**: Linux server (Docker, see `docs/DEPLOYMENT.md`)

**Project Type**: single Python project with a CLI under `scripts/cli` and a
browser admin console under `src/live_mem/static/js/admin`

**Performance Goals**: one dictionary lookup per tool call; no I/O added on
the call path

**Constraints**: no tool added, removed, renamed or re-levelled; the discovery
response unchanged; the nominal suite runs with no production credentials;
`uv run pytest tests/ -q -p no:cacheprovider`, `scripts/check_doc_links.py`
and `git diff --check` must pass

**Scale/Scope**: 36 operator registry entries with 6 aliases; 3 admin tools
gain a parameter; 2 token creation paths; 2 call routes; CLI (Click and shell)
and console (Access view)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Principle I (fail closed): the guard refuses when the audience cannot be
  resolved, when the identity is missing, and when the attribute is not a
  boolean; the token store refuses to load a non-boolean attribute. Pass.
- Principle II (every handler enforces its own authorization): the guard is
  added before the handler; no `check_*` call is removed or weakened. Pass.
- Principle III (frozen tool surface): no name, alias or permission changes;
  three hidden admin tools gain an optional parameter; the fixture gains an
  `operator_tool_guard` block and a test that enforces it; the change is
  justified in the commit message. Pass.
- Principle IV (structured results, no leakage): the refusal is a `dict` with
  `status` and `message`, built from server-owned text; the audit entry has
  no argument values. Pass.
- Principle V (agent write discipline): unchanged; the guard makes the
  "never write the bank directly" rule enforceable for agent tokens. Pass.
- Principle VI (failing-then-passing test): tests are written first and shown
  to fail on the current code; a mutation check weakens the guard (treats
  `false` as allowed) and shows the test fails. Pass.
- Principle VII (private security channel): no secret in code, docs or logs;
  the audit entry carries the token name, never the bearer token. Pass.

Re-check after Phase 1 design: no violation; the Complexity Tracking table is
empty.

## Project Structure

### Documentation (this feature)

```text
specs/002-operator-tool-guard/
├── plan.md              # This file
├── research.md          # Phase 0: current behaviour with file:line citations
├── data-model.md        # Phase 1: token record and identity fields
├── quickstart.md        # Phase 1: how to verify the feature end to end
├── contracts/
│   ├── refusal.md       # Refusal payload and audit entry
│   └── token-tools.md   # Parameters and results of the token tools
├── checklists/
│   └── requirements.md  # Specification quality checklist
└── tasks.md             # Phase 2: ordered tasks
```

### Source Code (repository root)

```text
src/live_mem/
├── core/
│   ├── models.py            # TokenInfo.operator_tools
│   └── tokens.py            # creation defaults, update paths, validation, list
├── auth/
│   ├── context.py           # identity snapshot carries operator_tools
│   └── middleware.py        # unchanged (bearer extraction, /api/tool)
├── tools/
│   ├── exposure.py          # guard function, HivemindMCPServer.call_tool
│   ├── __init__.py          # call_tool_direct calls the guard
│   ├── admin.py             # admin_create_token, admin_update_token,
│   │                        #   admin_bulk_update_tokens parameters
│   ├── access.py            # token_create unchanged (always refuses)
│   └── system.py            # system_whoami reports operator_tools
└── static/js/admin/
    └── views-access.js      # list column, create and edit form controls

scripts/cli/
├── commands.py              # --operator-tools options
├── shell.py                 # token update / bulk-update option
└── display.py               # token list and result rendering

tests/
├── test_operator_tool_guard.py   # the feature's tests (new)
├── test_mcp_tool_surface.py      # fixture block check
└── fixtures/tool_surface.json    # operator_tool_guard block

docs/
├── MCP_TOOLS_SPEC.md, TOOL_EXPOSURE.md (generated), DEPLOYMENT.md, SECURITY.md
└── CHANGELOG.md
```

**Structure Decision**: single project; the guard lives in
`src/live_mem/tools/exposure.py` next to the registry it reads, so the
audience lookup and the enforcement cannot drift apart. The token attribute
lives in `core/models.py` and `core/tokens.py` with the other record fields.

## Design

### Guard

`exposure.py` gains:

- `OPERATOR_TOOLS_REFUSED = "operator_tools_refused"` and
  `AUDIENCE_UNRESOLVED = "audience_unresolved"` (refusal causes).
- `OPERATOR_TOOL_REFUSAL_MESSAGE`: one fixed text.
- `token_allows_operator_tools(token_info) -> bool`: `None` identity → `False`;
  attribute absent → `True`; `True`/`False` → itself; anything else → `False`.
- `operator_tool_refusal(name, token_info, *, route, registered) -> dict | None`:
  returns `None` when the call may proceed to the handler, else the refusal
  dict after writing the audit line. `registered` tells the guard whether the
  tool manager knows the name; an unregistered name always returns `None` so
  the SDK or `call_tool_direct` answers "Unknown tool" as today. A missing
  identity returns `None` so the handler's own authentication gate refuses
  (FR-013). A registered name without a registry entry returns the refusal
  with cause `audience_unresolved`. An `agent_core` entry returns `None`.
- `HivemindMCPServer.call_tool` override: reads
  `get_mcp_request_token_info()`, calls the guard with `route="mcp"`, and on
  refusal returns `tool.fn_metadata.convert_result(refusal)`; otherwise
  delegates to `super().call_tool`.
- `call_tool_direct` calls the guard with `get_effective_token_info()` and
  `route="api_tool"` after the unknown-name check and before running the
  function.

### Token attribute

- `TokenInfo.operator_tools: bool = True`.
- `_store_from_data` refuses a non-boolean `operator_tools`.
- `validate_token` returns `operator_tools`; `_REQUEST_TOKEN_SCALAR_FIELDS`
  includes it.
- `create_delegated_token` sets `False`; `create_token(operator_tools=True)`
  stores the argument; both responses report it.
- `update_token(operator_tools="")` and `bulk_update_tokens(operator_tools="")`
  parse `"true"`/`"false"` (case-insensitive), refuse other text, persist,
  invalidate the fresh store, and report before/after values.
- `list_tokens` includes `operator_tools`; `system_whoami` reports it.

### Clients

- Click: `token create --operator-tools/--no-operator-tools` (default: not
  sent; refused for manager callers), `token update --operator-tools
  true|false`, `token bulk-update --operator-tools true|false`; shell mirrors
  the update option. `display.py` shows the value in the token list and in
  the created-token panel.
- Console: a column in the token table, a checkbox in the create form (admin
  mode only), a select in the edit form (no change / allow / refuse).

### Tests (written first)

`tests/test_operator_tool_guard.py` covers: refusal over a real MCP session
through `create_app()` for `mid_write` and `bank_write`; success with the
attribute `true`; legacy record without the attribute; `token_create` stores
`false`; `admin_create_token` default and explicit `false`; update and bulk
update paths; `/api/tool` with operator and refused tokens; the CLI client's
`call_tool` for operator tools against the production app; audit entries;
unregistered names; parametrized refusal over every operator entry and
alias; whoami; the fixture block.

## Complexity Tracking

No constitution violation to justify.
