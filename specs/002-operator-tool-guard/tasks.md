# Tasks: Per-bearer-token guard for operator tools over MCP

**Input**: Design documents from `/specs/002-operator-tool-guard/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Requested (test-driven). Test tasks precede implementation tasks and
must fail on the current code for the stated reason before implementation.

**Organization**: Tasks are grouped by user story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)

## Phase 1: Setup

- [X] T001 Create `tests/test_operator_tool_guard.py` with the shared
  harness: production app through `live_mem.server.create_app()` with
  `_validate_token` patched to return three identities (operator token with
  `operator_tools: true`, agent token with `false`, legacy token without the
  key), `FakeStorage` for `TokenService`, and a `live_mem.audit` log capture.

## Phase 2: Foundational

- [X] T002 Add the `operator_tool_guard` block to
  `tests/fixtures/tool_surface.json` as in contracts/token-tools.md.

## Phase 3: User Story 1 - An agent with a `manage` token cannot call operator tools over MCP (P1)

**Goal**: refuse every `audience=operator` tool and alias over `/mcp` for a
token with `operator_tools: false`, audit it, leave agent-core and unknown
names unchanged.

**Independent Test**: `tests/test_operator_tool_guard.py::TestMcpRoute`.

### Tests for User Story 1

- [X] T003 [US1] Test: `mid_write` and `bank_write` over a real MCP session
  with the agent token return `{"status": "forbidden", ...}` with the fixed
  message and the handler does not run (fails today: the handler's manage
  check passes and the mocked handler runs).
- [X] T004 [US1] Test: parametrized over every `operator` registry entry and
  alias, the agent token is refused with the same payload (fails today).
- [X] T005 [US1] Test: `short_note` with the agent token is not refused by
  the guard (passes today; guards against over-blocking).
- [X] T006 [US1] Test: an unregistered name returns the SDK "Unknown tool"
  result (passes today).
- [X] T007 [US1] Test: each refusal writes one `operator_tool_refused` audit
  line with tool, canonical_tool, cause, client, auth_type, route and no
  argument value (fails today).
- [X] T008 [US1] Test: a synthetic identity with a non-boolean attribute is
  refused; a registered name with no registry entry is refused with cause
  `audience_unresolved` (fails today).

### Implementation for User Story 1

- [X] T009 [US1] Add `operator_tools` to `_REQUEST_TOKEN_SCALAR_FIELDS` in
  `src/live_mem/auth/context.py`.
- [X] T010 [US1] Add the guard (`token_allows_operator_tools`,
  `operator_tool_refusal`, constants, audit line) and the
  `HivemindMCPServer.call_tool` override in `src/live_mem/tools/exposure.py`.

## Phase 4: User Story 2 - Existing tokens, CLI and admin console keep working (P2)

**Goal**: records without the attribute, operator tokens and the bootstrap
key keep every call; `/api/tool` keeps working for operators and refuses
agent tokens.

**Independent Test**: `tests/test_operator_tool_guard.py::TestCompatibility`
and `::TestApiToolRoute`.

### Tests for User Story 2

- [X] T011 [US2] Test: the operator token and the legacy token call
  `mid_write` over `/mcp` and the (mocked) handler runs (passes today).
- [X] T012 [US2] Test: the real CLI client (`scripts/cli/client.py`) calls
  `space_export` and `admin_list_tokens` against the production app served by
  uvicorn with the operator token and receives the handler result; with the
  agent token it receives the forbidden payload (second half fails today).
- [X] T013 [US2] Test: `call_tool_direct("admin_list_tokens")` with the
  operator identity reaches the handler; with the agent identity it returns
  the refusal and writes an audit line with `route: "api_tool"` (second half
  fails today).
- [X] T014 [US2] Test: a stored record without `operator_tools` validates to
  `operator_tools: true` and a record with a non-boolean value refuses the
  store load (fails today: field unknown, `validate_token` omits it).

### Implementation for User Story 2

- [X] T015 [US2] Add `operator_tools: bool = True` to `TokenInfo` in
  `src/live_mem/core/models.py`; validate the type in `_store_from_data` and
  return it from `validate_token` in `src/live_mem/core/tokens.py`.
- [X] T016 [US2] Call the guard from `call_tool_direct` in
  `src/live_mem/tools/__init__.py`.

## Phase 5: User Story 3 - An operator sets or changes the attribute (P3)

**Goal**: creation defaults per path, admin update paths, lists and identity
show the value, CLI and console expose it.

**Independent Test**: `tests/test_operator_tool_guard.py::TestTokenPaths`
and `::TestCli`.

### Tests for User Story 3

- [X] T017 [US3] Test: `create_delegated_token` stores `false` and reports
  it; `create_token` stores `true` by default and `false` on request (fails
  today).
- [X] T018 [US3] Test: `update_token(operator_tools="false")` persists,
  invalidates the fresh store and reports before/after; invalid text is
  refused; `bulk_update_tokens(operator_tools="true")` applies to the
  selection and reports per token (fails today).
- [X] T019 [US3] Test: `list_tokens` and `system_whoami` include
  `operator_tools` (fails today).
- [X] T020 [US3] Test: the registered `token_create` has no `operator_tools`
  parameter and the three admin tools have it; the fixture block matches
  (fails today).
- [X] T021 [US3] Test: Click `token create` sends `operator_tools` only for
  admin callers and refuses the flag for managers; `token update` and
  `token bulk-update` send `operator_tools`; `show_token_list` renders the
  column (fails today).

### Implementation for User Story 3

- [X] T022 [US3] Set `operator_tools=False` in `create_delegated_token`, add
  the `operator_tools` argument to `create_token`, `update_token`,
  `bulk_update_tokens` and the field to `list_tokens` in
  `src/live_mem/core/tokens.py`.
- [X] T023 [US3] Add the parameters to `admin_create_token`,
  `admin_update_token`, `admin_bulk_update_tokens` in
  `src/live_mem/tools/admin.py`; report `operator_tools` in `system_whoami`
  in `src/live_mem/tools/system.py`.
- [X] T024 [P] [US3] Add the CLI options in `scripts/cli/commands.py` and
  `scripts/cli/shell.py`; render the value in `scripts/cli/display.py`.
- [X] T025 [P] [US3] Add the column, the create-form checkbox (admin mode)
  and the edit-form select in `src/live_mem/static/js/admin/views-access.js`.

## Phase 6: Polish and documentation

- [X] T026 [P] Document the attribute, defaults, guard, refusal and audit
  entry in `docs/MCP_TOOLS_SPEC.md`, `docs/DEPLOYMENT.md`, `docs/SECURITY.md`;
  regenerate `docs/TOOL_EXPOSURE.md` with `scripts/generate_tool_exposure.py`
  after updating its hidden-tools paragraph.
- [X] T027 [P] Add the `[Unreleased]` entry in `CHANGELOG.md`.
- [X] T028 Run `uv sync`, `uv run pytest tests/ -q -p no:cacheprovider`,
  `uv run python scripts/check_doc_links.py`, `git diff --check`; commit with
  the required trailers.

## Dependencies & Execution Order

- Phase 1 and 2 first (harness and fixture).
- US1 before US2: the `/api/tool` tests reuse the guard function.
- US3 depends on US2's token model change (T015).
- Polish last.

## Implementation Strategy

Write all tests (T003-T008, T011-T014, T017-T021), run them and record the
failures, then implement T009-T010, T015-T016, T022-T025 until every test
passes, then T026-T028.
