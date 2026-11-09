---

description: "Task list for the tool guidance and remediation path for agents"
---

# Tasks: Tool guidance and remediation path for agents

**Input**: Design documents from `/specs/001-tool-guidance-remediation/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md),
[research.md](research.md), [data-model.md](data-model.md),
[contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: Included. Constitution principle VI requires a failing test before
each behaviour change, and spec.md asks for the replay test (FR-024), the
contract tests (FR-015, FR-019, FR-022, FR-028) and the opt-in evaluation
(SC-004). Each test task comes before the implementation task it covers and
must fail against the 1.6.1 behaviour first.

**Organization**: Tasks are grouped by user story in priority order (P1 to
P4) so each story can be built and tested on its own. Phase 2 holds the three
foundations every story depends on: the `error_kind` registry, the
`HivemindMCPServer.call_tool` conversion and the `MCP_REFUSAL_IS_ERROR`
setting.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an
  unfinished task)
- **[Story]**: The user story the task belongs to (US1 to US4)
- Each description names the file it changes and the FR or SC numbers it
  satisfies

## Path Conventions

Single project: `src/live_mem/`, `scripts/cli/`, `tools/hivemind-ingest/`,
`docs/`, `tests/` at the repository root, as laid out in plan.md, "Source
Code (repository root)".

---

## Phase 1: Setup (shared infrastructure)

**Purpose**: Create the two new modules and the four test files the plan
names, empty, so every later task edits an existing path.

- [X] T001 Create `src/live_mem/tools/guidance.py` with a module docstring
      stating its purpose (closed `error_kind` set, remediation builder,
      conversion helpers) and no behaviour yet
- [X] T002 [P] Create `src/live_mem/instructions.py` with a module docstring
      stating its purpose (server instruction rule records and
      `render_instructions()`) and no behaviour yet
- [X] T003 [P] Create the four test files with a module docstring each and
      no test yet, so their path-derived markers are fixed
      (`tests/test_quality_policy.py:41-81`):
      `tests/test_tool_guidance_replay_integration.py` (integration),
      `tests/test_tool_guidance_surface.py` (contract),
      `tests/test_tool_guidance_observability.py` (unit),
      `tests/test_tool_guidance_agent_evaluation.py` (unit, plus `optional`)
- [X] T004 [P] Add a shared fixture to
      `tests/test_tool_guidance_replay_integration.py` that builds a
      `HivemindMCPServer` with `register_all_tools`, the
      `bind_mcp_request_identity` middleware and a bound token identity for a
      `read,write` token with access to space `p` only, following
      `tests/test_mcp2_transport.py:34-37` (a plain `MCPServer` as in
      `tests/test_mcp_tool_surface.py:470-473` would skip the override,
      research.md Item 2)

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: The `error_kind` registry, the single conversion point, the
audit and metrics plumbing and the rollout setting. No user story task can
start before this phase is complete.

### `error_kind` registry

- [X] T005 Write the failing contract test in
      `tests/test_tool_guidance_surface.py` that imports `ErrorKind` from
      `src/live_mem/tools/guidance.py` and asserts its values equal the
      `error_kind` enum in
      `specs/001-tool-guidance-remediation/contracts/guidance-error-payload.schema.json`:
      `authentication_missing`, `token_scope_changed`, `invalid_space_id`,
      `space_access_denied`, `permission_insufficient`, `missing_argument`,
      `invalid_argument`, `required_prior_step_missing`,
      `operation_in_progress`, `write_route_refused`, `internal_failure`,
      `operator_tools_refused`, `audience_unresolved`,
      `unclassified_refusal` (FR-002)
- [X] T006 Implement in `src/live_mem/tools/guidance.py`: the `ErrorKind`
      `StrEnum` with those 14 values; a `remediation(...)` builder that
      requires `reason` (string) and `retryable` (boolean) and accepts
      `next_tool`, `arguments`, `missing_arguments`, `required_prior_step`
      (data-model.md, "Remediation object"); a `refusal(message, error_kind,
      *, status="error", remediation, **extra)` helper that returns the dict
      with `status` and `message` first; `canonical_tool_name(name)` that
      resolves an alias through `TOOL_EXPOSURES` and returns `unknown` for a
      name outside the registry; the constant
      `TOOL_OUTCOME_STATE_KEY = "live_mem.tool_outcome"` (FR-001, FR-003,
      FR-010, FR-030)

### Conversion point and observability plumbing

- [X] T007 Write the failing unit test in
      `tests/test_tool_guidance_observability.py` that proves risk R1: a
      value written to `scope["state"][TOOL_OUTCOME_STATE_KEY]` from inside a
      tool call (through `context.request_context.request.scope`) is read by
      an ASGI middleware's `finally` block on the same request, using the
      `wire_app` arrangement of `tests/test_mcp2_transport.py:25-60`. If this
      test cannot pass, apply the fallback in
      `contracts/observability-and-rollout.md` (the conversion point writes
      the audit line and the counter itself) and amend that contract before
      continuing
- [X] T008 Write the failing integration test in
      `tests/test_tool_guidance_replay_integration.py`: calling
      `short_note(space_id="q", ...)` through `call_tool` returns a
      `CallToolResult` with `is_error` true, `structured_content` equal to
      `json.loads(content[0].text)`, `status` `"error"`, the 1.6.1 `message`
      "Access denied to space 'q'", and an `error_kind` field; a successful
      `space_list` call keeps one text item serialized with two-space
      indentation and no `structured_content` (FR-026, FR-025)
- [X] T009 Write the failing integration test in
      `tests/test_tool_guidance_replay_integration.py` for input-schema
      failures: `short_note` called without `content` returns
      `error_kind` `missing_argument` with
      `remediation.missing_arguments == ["content"]` and a `message` that
      contains no pydantic text or argument value; `short_read` called with
      `limit="ten"` returns `invalid_argument` (FR-006, FR-010)
- [X] T010 Implement `to_call_tool_result(name, result, *, is_error)` in
      `src/live_mem/tools/guidance.py`: for a dict whose top-level `status`
      is `error` or `forbidden`, set `error_kind` to
      `unclassified_refusal` when absent, and return
      `CallToolResult(content=[TextContent(text=json.dumps(payload))],
      structured_content=payload, is_error=is_error)`; for every other dict
      return `None` so the caller keeps the SDK conversion; and
      `payload_from_validation_error(name, exc)` that builds the
      `missing_argument` or `invalid_argument` payload from `err["loc"]` and
      `err["type"]` only (FR-026, FR-010)
- [X] T011 Override `call_tool` on `HivemindMCPServer` in
      `src/live_mem/tools/exposure.py` (next to `list_tools`,
      `exposure.py:957-982`): call `self._tool_manager.call_tool(name,
      arguments, context, convert_result=False)`; on a refusal dict return
      `to_call_tool_result(...)` with `is_error=get_settings().mcp_refusal_is_error`;
      otherwise return `tool.fn_metadata.convert_result(result)`; catch a
      `ToolError` whose `__cause__` is a `ValidationError` and return the
      payload from T010; re-raise every other `ToolError` and every
      `MCPError`; for a refusal, write
      `{"tool": canonical_tool_name(name), "error_kind": ...}` to
      `context.request_context.request.scope["state"][TOOL_OUTCOME_STATE_KEY]`
      when a request is present (FR-011, FR-026, FR-030). T008 and T009 now
      pass
- [X] T012 Record the merge point with specification 002 in
      `src/live_mem/tools/exposure.py` as a comment above the override and in
      the pull request description: specification 002
      (`specs/002-operator-tool-guard`) already overrides `HivemindMCPServer.call_tool` (its
      `exposure.py:1082-1099`) and guards `call_tool_direct` (its
      `__init__.py:107-118`); when the branches meet, this feature's
      conversion is added to that same override after the 002 guard, so the
      guard's `forbidden` dict is converted like any other refusal with
      `error_kind` `operator_tools_refused` or `audience_unresolved`
      (constants `OPERATOR_TOOLS_REFUSED`, `AUDIENCE_UNRESOLVED` at its
      `exposure.py:973-974`, not redefined here) and
      `remediation.retryable=false` (plan.md, "Alignment with specification
      002")
- [X] T013 Write the failing unit tests in
      `tests/test_tool_guidance_observability.py`: the `request` audit entry
      for a refused `live_note` call carries `tool == "short_note"` and
      `error_kind`, and none of its fields contains an argument value, a
      remediation sentence, a space identifier or a token; `/metrics` shows
      `livemem_tool_refusals_total{tool="short_note",error_kind="space_access_denied"} 1`
      after one refused alias call and the JSON form carries
      `tool_refusals`; a refused `/api/tool` call is counted too (FR-030)
      Deviation: the test compares the audit and metric values against the
      `error_kind` the response carries, because the literal kind is assigned
      by the US1 sites (T023) and pinned by the replay test (T018).
- [X] T014 Implement in `src/live_mem/middleware.py`: `AuditMiddleware`
      reads `scope.get("state", {}).get(TOOL_OUTCOME_STATE_KEY)` in its
      `finally` (`middleware.py:491-513`) and adds `tool` and `error_kind`
      to the entry only when present; `MetricsMiddleware` keeps
      `_refusal_count: dict[tuple[str, str], int]` (`middleware.py:86-93`),
      increments it from the same state entry, and renders
      `livemem_tool_refusals_total{tool,error_kind}` with HELP and TYPE
      lines plus a `tool_refusals` object in the JSON form
      (`middleware.py:198-235`) (FR-030)
- [X] T015 In `src/live_mem/auth/middleware.py` `_api_tool_call`
      (`auth/middleware.py:645-648`), after `call_tool_direct` returns a dict
      whose `status` is `error` or `forbidden`, write the same state entry
      (`tool`, `error_kind`, defaulting to `unclassified_refusal`) so the
      proxy's refusals are audited and counted; leave the response body and
      HTTP status unchanged (FR-011, FR-030). T013 now passes

### Rollout setting

- [X] T016 Write the failing tests: in
      `tests/test_tool_guidance_replay_integration.py` a `rollout` case that,
      with `mcp_refusal_is_error` false on the settings object, asserts
      `is_error` is false and the payload is identical to the default run;
      in `tests/test_tool_guidance_observability.py` a case that
      `Settings(mcp_refusal_is_error="false")` parses to `False` and logs one
      startup warning naming `MCP_REFUSAL_IS_ERROR` as temporary (FR-031)
- [X] T017 Add `mcp_refusal_is_error: bool = True` to `Settings` in
      `src/live_mem/config.py` after `mcp_server_debug` (`config.py:88-92`)
      with a comment stating that a later release removes it, and the
      warning in `_validate_config` (`config.py:298-440`) when the value is
      `False` (FR-031). T016 now passes

**Checkpoint**: every refusal reaches the agent as an MCP error result with
the payload in both forms, is audited and counted, and the setting restores
the 1.6.1 delivery. User story work can begin.

---

## Phase 3: User Story 1 - An agent recovers from a wrong or out-of-order call without human help (Priority: P1)

**Goal**: Every refusal names its cause (`error_kind`) and the next step
(`remediation`), built from server-owned text, for every refusal site and
every tool including aliases and hidden operator tools.

**Independent Test**: `uv run pytest
tests/test_tool_guidance_replay_integration.py -q` replays at least 20 wrong
calls through the in-process server and checks each payload against
`contracts/guidance-error-payload.schema.json`; performing each
`remediation.next_tool` the token may call succeeds (quickstart.md, check 1).

### Tests for User Story 1

> Write these first; each must fail before its implementation task.

- [X] T018 [P] [US1] Write the failing replay cases for the guard helpers in
      `tests/test_tool_guidance_replay_integration.py`: no identity ->
      `authentication_missing`; malformed `space_id` "Bad Id" ->
      `invalid_space_id` with `missing_arguments == ["space_id"]` and an
      example value in `reason`; reserved `_backups` -> `invalid_space_id`
      whose `reason` gives the format and does not say whether the space
      exists; `space_id="q"` -> `space_access_denied` with
      `accessible_spaces == ["p"]` and `next_tool == "space_list"`; a token
      with `space_ids: []` -> `accessible_spaces == []` and a `reason` naming
      `space_invite_token` and `system_whoami`; `space_create` with
      `read,write` -> `permission_insufficient`, `required_permission ==
      "manage"`, `current_permissions == ["read", "write"]`, no `next_tool`,
      and no bypass wording; a tombstoned token hash ->
      `token_scope_changed` with `required_prior_step` "refresh the tool
      list" (FR-004, FR-005, FR-006, FR-009; acceptance scenarios 2, 3, 6;
      Edge Cases)
- [X] T019 [P] [US1] Write the failing replay cases for handler refusals in
      `tests/test_tool_guidance_replay_integration.py`: `short_note` with
      `category="note"` -> `invalid_argument`, `reason` listing the seven
      categories and one example (acceptance scenario 5); `space_delete`
      without `confirm` -> `missing_argument`, `missing_arguments ==
      ["confirm"]`, `arguments == {"confirm": true}`; `backup_restore`
      without `backup_id` -> `missing_argument`; `long_ingest_list` with
      `limit=0` -> `invalid_argument`; `mid_consolidate(agent="other")` with
      `read,write` -> `permission_insufficient`; `mid_write` with
      `read,write` -> `permission_insufficient` whose `reason` names
      `short_note` and reveals nothing else about hidden tools; `live_note`
      (alias) refusal identical to `short_note`'s except that `reason` names
      `short_note` as the canonical tool (FR-006, FR-011, FR-021; Edge Cases)
- [X] T020 [P] [US1] Write the failing replay cases for write-route and
      internal refusals in `tests/test_tool_guidance_replay_integration.py`:
      with `MCP_SERVER_DEBUG` off and a shared (staged) space, `short_note`
      -> `write_route_refused`, `message` "Writes to a shared space are not
      accepted on this route", `retryable` false, no "Internal server error"
      and no fallback route in `reason`; a forced `RegistryRefused` and a
      forced `CorruptedStateError` -> `write_route_refused` naming the
      operator action; a forced unexpected exception -> `internal_failure`
      with `message` "Internal server error" (FR-008, FR-010; acceptance
      scenario 4; SC-002)
- [X] T021 [P] [US1] Write the failing cases for results that are not
      refusals in `tests/test_tool_guidance_replay_integration.py`:
      `mid_consolidate(space_id="p")` while a job for `p` is queued returns
      `is_error` false, `existing_job` with the job identifier and status,
      `next_action == "return_to_user_without_polling"`, and no second job
      in the queue (FR-007; acceptance scenario 1); `bank_compact` while the
      consolidation lock is held returns `is_error` false, `status ==
      "conflict"`, `error_kind == "operation_in_progress"` and
      `remediation.retryable` true; a `bank_compact` failure carries
      `remediation.reason` and no top-level string `remediation`; a
      `not_found` result keeps `is_error` false (FR-026, FR-027)
- [X] T022 [US1] Write the failing coverage assertion in
      `tests/test_tool_guidance_replay_integration.py`: over all replay
      cases, every `ErrorKind` value appears at least once except
      `operator_tools_refused`, `audience_unresolved`,
      `required_prior_step_missing` (no producer yet) and
      `unclassified_refusal`, which must never appear; at least 20 cases
      ran; every payload validates against the JSON schema; for every
      payload whose `next_tool` the token may call, that call with the given
      `arguments` succeeds or is itself a documented refusal (FR-024,
      SC-001)
      Deviation: the existing whole-dict equalities on guard refusals in
      `tests/test_operator_tool_guard.py`, `tests/test_p10_tool_exposure.py`,
      `tests/test_admin_audit_ring.py`, `tests/test_admin_gc_tool_contract.py`,
      `tests/test_gc_safety.py` and `tests/test_security_hardening_v2.py`
      were changed to assert the same `status`, `message` and existing
      fields as subsets, because the payload now carries `error_kind` and
      `remediation`; no asserted value changed.

### Implementation for User Story 1

- [X] T023 [US1] In `src/live_mem/auth/context.py` make the guard helpers
      return Guidance error payloads through `guidance.refusal`, keeping
      every `message` unchanged: `_evaluate_access` (`context.py:256-310`)
      returns `authentication_missing`, `invalid_space_id` (with
      `missing_arguments=["space_id"]` and an example), and
      `space_access_denied` (with `accessible_spaces` from
      `allowed_resources`, `next_tool="space_list"`, and for an empty list a
      `reason` naming `space_invite_token` and `system_whoami`);
      `check_write_permission`, `check_manage_permission`,
      `check_admin_permission` (`context.py:328-413`) return
      `permission_insufficient` with `required_permission`,
      `current_permissions` and a `reason` stating that an operator with
      `manage` or `admin` can act, and no `next_tool` (FR-004, FR-005,
      FR-006)
- [X] T024 [US1] In `src/live_mem/auth/context.py` add
      `token_scope_changed_for_current_request()` that reports whether the
      current token hash is in `_invalidated_token_hashes`
      (`context.py:84,209,237-238`), and make the four guard helpers return
      `token_scope_changed` instead of `authentication_missing` when it is
      true, with `required_prior_step` "refresh the tool list" and a
      `reason` consistent with `mcp_reconnect_required` (FR-009)
- [X] T025 [P] [US1] In `src/live_mem/core/memory_id.py`
      `reserved_space_error` (`memory_id.py:48-66`) add `error_kind`
      `invalid_space_id` and a `remediation` giving the allowed format only,
      keeping `message` and `recovery_required` (FR-006; Edge Cases)
- [X] T026 [P] [US1] In `src/live_mem/auth/context.py` `safe_error`
      (`context.py:415-438`) add `error_kind` `internal_failure` and the
      fixed `remediation` from `contracts/guidance-error-payload.md`, in
      both debug and non-debug modes, without changing either `message`
      (FR-010)
- [X] T027 [US1] In `src/live_mem/tools/live.py` `live_note`
      (`live.py:91-134`) catch `StagedWriteNotImplemented`,
      `RegistryRefused` and `CorruptedStateError` before the generic
      `except`, as `src/live_mem/tools/admin.py:751-777` does, and return
      `write_route_refused` payloads with server-owned messages ("Writes to
      a shared space are not accepted on this route" for the staged case)
      and a `reason` naming the operator workflow and no fallback route
      (FR-008; constitution principle I)
- [X] T028 [P] [US1] In `src/live_mem/tools/admin.py` (`admin.py:363-372,
      713, 751-777`) add `error_kind` and `remediation` to each refusal
      through `guidance.refusal`, keeping `message` and the existing
      `reason` field: the three route refusals become
      `write_route_refused`, the confirm refusal `missing_argument` with
      `arguments={"confirm": true}`, the `max_age_days` refusal
      `invalid_argument` (FR-006, FR-008)
- [X] T029 [P] [US1] In `src/live_mem/tools/backup.py` (`backup.py:53, 63,
      74, 83, 317, 420`) add `error_kind` and `remediation`:
      `missing_argument` for the absent `backup_id` and the two confirm
      refusals, `invalid_argument` for the three format refusals, each
      `reason` giving the format and one example (FR-006)
- [X] T030 [P] [US1] In `src/live_mem/tools/bank.py` add `error_kind` and
      `remediation`: filename refusals (`bank.py:125-143, 1102`)
      `missing_argument` or `invalid_argument`; the `client_name` refusal
      (`bank.py:441-448`) stays blocked on Q6 (T067); the other-agent
      consolidation refusal (`bank.py:462-470`) `permission_insufficient`
      with `required_permission="manage"`; the confirm refusal
      (`bank.py:1209-1218`) `missing_argument` with `arguments={"confirm":
      true}`; the `conflict` result (`bank.py:1365-1372`) gains
      `error_kind="operation_in_progress"` and `remediation` with
      `retryable=true` while keeping `status="conflict"` (FR-006, FR-027)
- [X] T031 [US1] In `src/live_mem/tools/bank.py`
      `_safe_bank_compact_failure` (`bank.py:44-80`) replace the top-level
      string `remediation` with the `remediation` object whose `reason` is
      the former string and whose `retryable` is false, keeping
      `failure_reason`, `failed_phase`, `rollback_outcome` and `failures`;
      set `error_kind` `write_route_refused` for
      `direct_local_route_required` and `hivemind_state_corrupt`, and
      `internal_failure` for `compaction_tool_failure` (`bank.py:1412-1460`)
      (FR-003, FR-008; spec.md Clarifications, `bank_compact`)
- [X] T032 [P] [US1] In `src/live_mem/tools/graph.py` (`graph.py:213, 579-588,
      857, 868-878, 930, 934, 996, 1001, 1046, 1102, 1107, 1161-1178`) add
      `error_kind` and `remediation`: the two volatile-file refusals
      `permission_insufficient` with `required_permission="manage"` and
      `arguments={"include_volatile": true}`; the rest `invalid_argument` or
      `missing_argument` with the allowed values or format and one example
      (FR-006)
- [X] T033 [P] [US1] In `src/live_mem/tools/space.py` (`space.py:124-131,
      505-514`) add `error_kind` and `remediation`: the missing `rules`
      refusal `missing_argument` with `missing_arguments=["rules"]`; the
      confirm refusal `missing_argument` with `arguments={"confirm": true}`
      (FR-006)
- [X] T034 [P] [US1] In `src/live_mem/core/live.py` (`live.py:99-106`) add
      `error_kind` `invalid_argument` and a `remediation` with
      `missing_arguments=["category"]` and a `reason` listing
      `VALID_CATEGORIES` (`live.py:43`) and the example `observation`,
      keeping `message` (FR-006; acceptance scenario 5)
- [X] T035 [P] [US1] In `src/live_mem/core/gc.py` (`gc.py:420, 679, 700,
      722`) add `error_kind="operation_in_progress"` and a `remediation`
      with `retryable=true` to each `conflict` result, keeping `status` and
      `message` (FR-027)
- [X] T036 [US1] In `src/live_mem/core/consolidation_queue.py`
      (`consolidation_queue.py:164-166`) return the existing job's payload
      with an added `existing_job` object (job identifier and status) and
      the existing `next_action`; in `src/live_mem/tools/bank.py`
      `bank_consolidate` keep passing it through unchanged; in
      `consolidation_queue.py:432-434` add `error_kind="internal_failure"`
      and the fixed `remediation` to the crash result (FR-007; acceptance
      scenario 1)
      Note: `src/live_mem/tools/__init__.py:99,105,135` (proxy-only "Server
      not initialized", "Unknown tool", "internal error") are unchanged
      because the specification 002 tests pin "Unknown tool" as a two-key
      dict; they never pass the conversion point. The "non-empty client_name
      identity is required to write a note" site in `src/live_mem/core/live.py`
      is left untouched for the same reason as T067 (Q6).
- [X] T037 [US1] In `src/live_mem/tools/guidance.py` add the operator-tool
      name map used by `permission_insufficient` reasons (`mid_write` and
      `bank_write` name `short_note` and `mid_consolidate`; other operator
      tools name no agent tool) and use it in the conversion point to
      complete the `reason` of a hidden operator tool's permission refusal
      without revealing more than the permission cause; make the conversion
      point add "canonical tool: <name>" to `reason` when the called name is
      an alias (FR-011, FR-021; Edge Cases). T018 to T022 now pass
- [X] T038 [US1] Run the mutation proof and record its output in the pull
      request: set `is_error=False` in `to_call_tool_result` and show
      `tests/test_tool_guidance_replay_integration.py` fails; restore it
      (constitution principle VI)

**Checkpoint**: User Story 1 is complete when quickstart.md check 1 passes
with every `error_kind` covered except the three documented exclusions.

---

## Phase 4: User Story 2 - An agent chooses the right tool from its description (Priority: P2)

**Goal**: Every `agent_core` description follows the six-section contract,
every parameter states its allowed values and default, aliases keep an
identical description that names the canonical tool, hidden operator tools
say so, and each discovery profile stays within the 64 KiB budget.

**Independent Test**: `uv run pytest tests/test_tool_guidance_surface.py
tests/test_mcp_tool_surface.py tests/test_p10_tool_exposure.py -q`
(quickstart.md, check 2).

### Tests for User Story 2

- [X] T039 [P] [US2] Write the failing contract tests in
      `tests/test_tool_guidance_surface.py`: for each `agent_core` canonical
      name in `TOOL_EXPOSURES`, the description contains the six section
      headings "Use when", "Do not use when", "Before calling", "Peer
      tools", "Result" after a one-sentence purpose, in that order; the
      permission named under "Before calling" equals the entry's
      `minimum_permission`; the space argument is named when
      `space_scope_argument` is set; `short_note`'s description lists the
      seven categories, says the identity comes from the token, and says
      `mid_write` is not an alternative; `mid_consolidate`'s says "at most
      once", "after user validation" and "without polling" (FR-017, FR-019;
      acceptance scenarios 1 to 3; SC-003)
- [X] T040 [P] [US2] Write the failing contract tests in
      `tests/test_tool_guidance_surface.py`: every parameter of every
      registered tool has a description that states allowed values or
      format, and for `category`, `permissions` and other enumerated
      arguments lists the values; each alias pair's descriptions are equal
      and name the canonical tool; each operator tool's description starts
      with "Operator tool:" and names its agent tool where
      `contracts/tool-description-contract.md` lists one; the serialized
      `tools/list` for each of the four profiles is within
      `DISCOVERY_SCHEMA_BUDGET_BYTES`; `tests/fixtures/tool_surface.json`
      is byte-identical to the committed file (FR-018, FR-020, FR-021,
      FR-022, FR-023; acceptance scenarios 4 and 5)

### Implementation for User Story 2

- [X] T041 [P] [US2] Rewrite the descriptions and parameter descriptions of
      the tools in `src/live_mem/tools/system.py` to the contract
- [X] T042 [P] [US2] Rewrite the descriptions and parameter descriptions of
      the tools in `src/live_mem/tools/space.py` to the contract, keeping the
      `space_update_rules` cross-reference (`space.py:75-77,323-325`)
- [X] T043 [P] [US2] Rewrite the descriptions and parameter descriptions of
      the tools in `src/live_mem/tools/live.py` to the contract, including
      the three `short_note` statements of acceptance scenario 2 and the
      `space_rules`, `mid_read_all`, `short_read` prior calls
- [X] T044 [P] [US2] Rewrite the descriptions and parameter descriptions of
      the tools in `src/live_mem/tools/bank.py` to the contract, including
      the `mid_consolidate` statements of acceptance scenario 3 and the
      operator statements for `bank_write`, `bank_delete`, `bank_repair`,
      `bank_compact`, `mid_archive_retry` (`bank.py:1461-1466`)
- [X] T045 [P] [US2] Rewrite the descriptions and parameter descriptions of
      the tools in `src/live_mem/tools/graph.py` to the contract, keeping the
      `graph_connect` binding note (`graph.py:179-181`) and the
      `long_query` non-authority statement (`graph.py:456-457`)
- [X] T046 [P] [US2] Rewrite the descriptions and parameter descriptions of
      the tools in `src/live_mem/tools/backup.py`, `src/live_mem/tools/admin.py`
      and `src/live_mem/tools/access.py` to the contract with the operator
      statement on each operator tool
- [X] T047 [US2] Measure and record in the pull request the serialized
      `tools/list` size per profile after T041 to T046 (risk R3); if a
      profile exceeds the budget, shorten descriptions rather than raise
      `DISCOVERY_SCHEMA_BUDGET_BYTES` (`src/live_mem/tools/exposure.py:442`)
      (FR-022). T039 and T040 now pass
      Measured serialized `tools/list` size per profile (bytes, compact JSON):
      before the rewrite read 12266, write 16867, manage 21875, admin 21875;
      after the rewrite read 17813, write 22538, manage 28404, admin 28404;
      budget 65536. No description was shortened and the budget is unchanged.
- [X] T048 [US2] Run `uv run python scripts/generate_tool_exposure.py` and
      confirm `docs/TOOL_EXPOSURE.md` is unchanged (it carries no
      descriptions), and run the alias-drift mutation proof
      `tests/test_mcp_tool_surface.py::test_alias_metadata_parity_guard_rejects_a_mutated_field`
      (constitution principles III and VI)

**Checkpoint**: User Story 2 is complete when quickstart.md check 2 passes
for the description and budget assertions.

---

## Phase 5: User Story 3 - An agent receives the Hivemind rules from the server at connection time (Priority: P3)

**Goal**: The initialize result carries instructions built from the FR-013
rules in order, identical for every token, free of deployment values, and
consistent with the two rule documents.

**Independent Test**: `uv run pytest tests/test_tool_guidance_surface.py -q
-k instructions` (quickstart.md, check 2); the opt-in evaluation
(quickstart.md, check 5).

### Tests for User Story 3

- [X] T049 [P] [US3] Write the failing contract tests in
      `tests/test_tool_guidance_surface.py`: the initialize result of an
      in-process `HivemindMCPServer` carries a non-empty `instructions`
      string; the rendered text contains, in this order, the marker phrases
      of the six rule records (`space_rules`, `mid_read_all`, `short_read`,
      "stop before"; `short_note`, the seven categories, "identity", "never
      write the bank", "secrets"; `mid_consolidate`, "at most once",
      "without polling"; "admin", "manage", "write", "read",
      `system_whoami`, "invitation"; `long_query`, "non-authoritative",
      `long_push`; `error_kind`, `remediation`); the text is identical for a
      `read` token and an `admin` token (FR-013, FR-016; acceptance
      scenarios 1 and 2)
- [X] T050 [P] [US3] Write the failing consistency tests in
      `tests/test_tool_guidance_surface.py`: the fenced block after the
      heading "## Recommended project instruction block" in
      `docs/AGENT_MEMORY_SETUP.md` and the "Recommended Agent Workflow"
      section of `RULES/live-mem.standard.memory.bank.md` each contain the
      marker phrases of the rules they are the source of
      (`contracts/server-instructions.md`); the seven categories in the
      rendered text equal `VALID_CATEGORIES` (`src/live_mem/core/live.py:43`)
      and the list in the document block; the rendered text contains no
      `http://`, `https://`, `{HIVEMIND_MCP_SERVER}`, `{SPACE}` or string
      matching the token format (FR-014, FR-015, FR-028; acceptance
      scenarios 3 and 4)
- [X] T051 [US3] Withdrawn on 2026-10-14 with question Q4: the server
      instructions have no byte cap and no cap test. The skipped test was
      removed from `tests/test_tool_guidance_surface.py`. For reference,
      `render_instructions()` renders 2698 bytes (405 words).

### Implementation for User Story 3

- [X] T052 [US3] Implement `src/live_mem/instructions.py`: a frozen tuple
      of rule records `(identifier, sentence, source_document)` for the six
      FR-013 items in `contracts/server-instructions.md`, and
      `render_instructions()` that joins them in order with items 1 and 2
      first; sentences say "the space the token works in" instead of a
      placeholder and name no endpoint, token or space (FR-013, FR-014,
      FR-016)
- [X] T053 [US3] In `src/live_mem/tools/exposure.py` give
      `HivemindMCPServer` an `__init__` that passes
      `instructions=render_instructions()` to `MCPServer.__init__` when the
      caller passes none, so `src/live_mem/server.py:249-254` and
      `tests/test_mcp2_transport.py:34-37` both carry the text without
      edits. T049 and T050 now pass
- [X] T054 [P] [US3] Add one sentence to `docs/AGENT_MEMORY_SETUP.md` after
      the block (near line 166) stating that the server sends the same rules
      as instructions at initialize and that the block stays for clients
      that do not show server instructions (FR-028)
- [X] T055 [US3] Write `tests/test_tool_guidance_agent_evaluation.py`:
      marked `optional`; skips unless `HIVEMIND_AGENT_EVAL=1` and the
      model-credential variables it names are set, following
      `tests/test_hivemind_protocol_model.py:350-358`; runs a real model
      given only the rendered instructions against an in-process server with
      a `read,write` token ten times and asserts, per run, `space_rules`,
      `mid_read_all` and `short_read` before the first `short_note`, notes
      only in the seven categories, exactly one `mid_consolidate`, and no
      `bank_consolidation_status` afterwards; writes the model name and each
      run's result to a JSON file named in the test docstring (SC-004)
      Note: the evaluation was not run in this implementation (no model
      credentials; the nominal suite skips it). The SC-004 record is not
      available for the pull request until an operator runs check 5.
- [X] T056 [US3] Run the mutation proof and record it in the pull request:
      remove one rule record from `src/live_mem/instructions.py` and show
      the consistency test fails; restore it (constitution principle VI)
      Result: with the `long_memory` record removed, both instruction tests
      fail (`('long_memory', 'long_query')` marker missing); restored.

**Checkpoint**: User Story 3 is complete when the instruction tests pass
and the opt-in evaluation result is recorded in the pull request.

---

## Phase 6: User Story 4 - An operator views the same error structure in every client (Priority: P4)

**Goal**: The CLI, the admin console and `hivemind-ingest` show `message`
exactly as in 1.6.1 after refused calls become MCP error results, and the
CLI shows `bank_compact`'s remediation from the new object.

**Independent Test**: quickstart.md, check 4 (suite, `git diff --check`, and
one refused call per client).

### Tests for User Story 4

- [X] T057 [P] [US4] Write the failing integration test in
      `tests/test_tool_guidance_replay_integration.py`: over the real HTTP
      app (`tests/test_mcp2_transport.py:128-170` arrangement), the CLI
      `MCPClient.call_tool` (`scripts/cli/client.py:99-106`) returns the
      payload dict with `status`, `message`, `error_kind` and `remediation`
      for a refused `space_info` on `q`, and the CLI's `_run_tool` prints
      the 1.6.1 message through `show_error`
      (`scripts/cli/commands.py:104-108`) (FR-029; acceptance scenario 1;
      SC-005)
- [X] T058 [P] [US4] Write the failing test in
      `tests/test_tool_guidance_observability.py`: `POST /api/tool` with
      `{"tool": "space_info", "arguments": {"space_id": "q"}}` returns HTTP
      200 and a body equal to the handler's payload including `error_kind`
      and `remediation`; a `read` token still receives HTTP 403 with "write"
      in `message` from the proxy's own check
      (`src/live_mem/auth/middleware.py:574-577`) (FR-011, FR-029;
      acceptance scenario 2)
- [X] T059 [P] [US4] Write the failing CLI display test (in
      `tests/test_tool_guidance_observability.py`, which imports
      `scripts.cli.display`): `show_bank_compact_failure` prints
      `remediation.reason` for a payload whose `remediation` is an object
      and prints `<missing>` when it is absent (spec.md Clarifications,
      `bank_compact`)

### Implementation for User Story 4

- [X] T060 [US4] In `scripts/cli/display.py` `show_bank_compact_failure`
      (`display.py:700-712`, printed at `display.py:778`) read
      `result.get("remediation", {}).get("reason", "<missing>")` when
      `remediation` is a dict, so the field shown is the reason sentence;
      no other CLI file changes (research.md, Item 1). T059 now passes
- [X] T061 [US4] Confirm the CLI and proxy tests of T057 and T058 pass
      without a change to `scripts/cli/client.py`,
      `scripts/cli/commands.py`, `src/live_mem/static/js/admin-api.js` or
      `src/live_mem/tools/__init__.py`, and record the three `git diff
      --stat` lines in the pull request (FR-029)
      Result: `git diff --stat` for `scripts/cli/client.py`,
      `scripts/cli/commands.py`, `src/live_mem/static/js/admin-api.js` and
      `src/live_mem/tools/__init__.py` is empty (no change); the CLI and
      proxy tests pass with `scripts/cli/display.py` as the only client edit.
- [X] T062 [US4] Run `cd tools/hivemind-ingest && go test ./...` and record
      the result; `TestClientCallToolIsError`
      (`tools/hivemind-ingest/internal/mcpclient/client_test.go:174-192`)
      covers the mapping that keeps `message` (`client.go:420-428`); no Go
      change (FR-029; acceptance scenario 3)
      Result: `go test ./...` passes `internal/mcpclient` (with
      `TestClientCallToolIsError`), `internal/ingest`, `internal/ontology`,
      `internal/scanner` and `internal/tui`; `internal/config` fails on
      `TestConfigLoadFailsClosedOnStatAccessError`, a file-permission check
      that cannot fail when the tests run as root in this container. No Go
      file changed.

**Checkpoint**: User Story 4 is complete when quickstart.md check 4 passes.

---

## Phase 7: Polish and cross-cutting concerns

**Purpose**: Documentation, rollout notes, the two blocked decisions and
the final checks.

- [X] T063 Write the failing documentation contract test in
      `tests/test_tool_guidance_surface.py`: `docs/MCP_TOOLS_SPEC.md`
      contains every `ErrorKind` value with one example each, shows the
      payload shape, and lists `conflict` in the "Standardized Return
      Format" block (`docs/MCP_TOOLS_SPEC.md:108-121`); `docs/DEPLOYMENT.md`
      and `.env.example` mention `MCP_REFUSAL_IS_ERROR` (FR-012, FR-031,
      SC-006)
- [X] T064 [P] Update `docs/MCP_TOOLS_SPEC.md`: add `conflict` to the
      return-format block, document the Guidance error payload, the
      `remediation` object, the closed `error_kind` set with one example per
      value (from `contracts/guidance-error-payload.md`), the two reserved
      values, the `isError` delivery and the `structuredContent` copy, and
      the `bank_compact` `remediation.reason` change (FR-012)
- [X] T065 [P] Add a subsection under "Production" in `docs/DEPLOYMENT.md`
      (`docs/DEPLOYMENT.md:184`) describing the MCP error delivery,
      `MCP_REFUSAL_IS_ERROR`, its default `true`, the effect of `false`, and
      that a later release removes it; add `MCP_REFUSAL_IS_ERROR=true` with
      a comment to the MCP server block of `.env.example`
      (`.env.example:10-17`) (FR-031)
- [X] T066 [P] Add the `[Unreleased]` entry to `CHANGELOG.md`
      (`CHANGELOG.md:17-19`): the Guidance error payload, the MCP error
      delivery, the setting and its later removal, the `bank_compact`
      `remediation` shape change, the new audit fields and counter, the
      server instructions, the description rewrite (FR-031). T063 now passes
- [X] T067 Assign the `error_kind` of the "A non-empty client_name identity
      is required to consolidate notes" refusal
      (`src/live_mem/tools/bank.py:441-448`). Q6 decided on 2026-10-14:
      `authentication_missing` with `retryable=false`, because the agent
      cannot correct a bearer token without a `client_name`; an operator must
      issue one. The same decision applies to the note-writing refusal "A
      non-empty client_name identity is required to write a note"
      (`src/live_mem/core/live.py`), left untouched by T030 for this reason.
      Both sites use `client_name_missing()` in `src/live_mem/auth/context.py`.
      Tests: the replay case `consolidate-no-client-name` and
      `tests/test_tool_guidance_client_name.py` failed before the change
      (`unclassified_refusal`, `KeyError: 'error_kind'`) and pass after; the
      T022 coverage assertion now requires that no refusal is
      `unclassified_refusal`
- [X] T068 Run quickstart.md checks 1 to 4: `uv run pytest tests/ -q`,
      `uv run python scripts/check_doc_links.py`, `git diff --check`, and
      confirm no assertion on `status` or `message` changed in the 56 files
      that assert the error dict (`git diff --stat` on `tests/` shows only
      the four new files) (SC-005, SC-006; constitution "Development
      workflow")
      Result: `uv run pytest tests/ -q -p no:cacheprovider`: 7331 passed, 19
      skipped in 3 min 29 s; `scripts/check_doc_links.py` OK (after fixing a
      dangling `research.md` link in `contracts/tool-description-contract.md`
      to `../research.md`); `git diff --check` clean. `git diff --stat` on
      `tests/` shows the four new files plus the six files listed under T022
      (subset assertions on the same values); no assertion on `status` or
      `message` changed its value.
- [X] T069 Run `uv run pytest -m contract -q` and `uv run pytest -m
      integration -q` separately and record runtime and skips in the pull
      request; confirm `tests/test_tool_guidance_agent_evaluation.py` skips
      without `HIVEMIND_AGENT_EVAL` (constitution principle VI;
      `docs/TESTING.md:25-28`)
      Result: `-m contract`: 725 passed, 10 skipped in 22.2 s; `-m
      integration`: 163 passed, 2 skipped in 14.2 s;
      `tests/test_tool_guidance_agent_evaluation.py` skips with "opt-in
      evaluation: set HIVEMIND_AGENT_EVAL=1 ..." without the variable.
- [X] T070 Write the pull request description with the summary, the
      commands run, the three mutation proofs (T038, T048, T056), the
      measured discovery sizes (T047), the instruction size (T051), the
      SC-004 record (T055), the client evidence (T061, T062) and the merge
      note for specification 002 (T012) (`CONTRIBUTING.md:70-72`)
      Written in the pull request description; the SC-004 record waits for
      an operator run of quickstart check 5.

---

## Dependencies and execution order

### Phase dependencies

- Setup (Phase 1): no dependency.
- Foundational (Phase 2): depends on Phase 1; blocks every user story. Within
  it: T005 before T006; T007 before T011 (its outcome decides the
  observability design); T008 and T009 before T010 and T011; T011 before
  T012; T013 before T014 and T015; T016 before T017. The override of T011
  reads the setting at call time, so T011 does not wait for T017, but the
  rollout case of T016 passes only after T017.
- User stories (Phases 3 to 6): each depends on Phase 2 only.
  - US1 (Phase 3) has no dependency on another story.
  - US2 (Phase 4) edits the same tool modules as US1 (T028 to T033 versus
    T041 to T046); run the two stories sequentially or coordinate per file.
  - US3 (Phase 5) depends on T011 only (the override class) and on T051's
    decision for its last test.
  - US4 (Phase 6) depends on US1's T031 for the CLI display test (T059,
    T060); its other tasks depend on Phase 2 only.
- Polish (Phase 7): T063 before T064 to T066; T067 waits for the Q6
  decision; T068 to T070 last.

### Tasks blocked on an open decision

None. T051 was withdrawn with Q4 and T067 was completed after Q6 was
decided (both on 2026-10-14).

### Parallel opportunities

- Phase 1: T002, T003, T004 together after T001.
- Phase 2: T005 and T007 and T008 and T009 together (different test files
  and cases); T013 and T016 together.
- US1 tests T018 to T021 together; implementations T025, T026, T028, T029,
  T030, T032, T033, T034, T035 together after T023 and T024.
- US2 tests T039 and T040 together; implementations T041 to T046 together.
- US3 tests T049 and T050 together; T054 with T052.
- US4 tests T057 to T059 together.
- Polish: T064, T065, T066 together after T063.

### Parallel example: User Story 1

```bash
# Tests first (one file, separate cases; run them in one pytest call)
Task: "T018 guard helper replay cases"
Task: "T019 handler refusal replay cases"
Task: "T020 write-route and internal refusal cases"
Task: "T021 non-refusal cases"

# Then the per-file refusal updates
Task: "T025 src/live_mem/core/memory_id.py"
Task: "T026 safe_error in src/live_mem/auth/context.py"
Task: "T028 src/live_mem/tools/admin.py"
Task: "T029 src/live_mem/tools/backup.py"
Task: "T030 src/live_mem/tools/bank.py"
Task: "T032 src/live_mem/tools/graph.py"
Task: "T033 src/live_mem/tools/space.py"
Task: "T034 src/live_mem/core/live.py"
Task: "T035 src/live_mem/core/gc.py"
```

---

## Implementation strategy

### Smallest useful increment (User Story 1 only)

1. Phase 1 and Phase 2: every refusal is an MCP error result with the
   payload in both forms, audited and counted, with the setting.
2. Phase 3: every refusal site carries `error_kind` and `remediation`.
3. Stop and validate with quickstart.md check 1; the CLI, the console and
   `hivemind-ingest` already show `message` as before (research.md, Item 1).

### Incremental delivery

1. Phase 1 and 2, then US1: the remediation path (P1).
2. US2: descriptions and budget (P2).
3. US3: server instructions and the opt-in evaluation (P3).
4. US4: client evidence and the CLI display change (P4).
5. Phase 7: documentation, rollout notes, final checks.

### Suggested order for one implementer

T001 to T004, T005 to T017 (Phase 2 in listed order, with T007 first among
the tests because its outcome may change T011, T014 and T015), T018 to
T038, T039 to T048, T049 to T056 (T051 withdrawn), T057 to T062, T063
to T070.

---

## Notes

- Every refusal `message` keeps its 1.6.1 text (FR-025, SC-005); a task
  that would change one is out of scope.
- No tool is added, removed, renamed or re-levelled;
  `tests/fixtures/tool_surface.json` stays byte-identical (FR-023).
- Remediation sentences come from literals in the source, tool names,
  argument names, permission names and the token's own space identifiers,
  never from exception text (FR-010).
- Commit after each task or logical group; verify each test fails before
  its implementation task.
