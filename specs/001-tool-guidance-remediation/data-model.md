# Data model: tool guidance and remediation path for agents

**Summary**: A refused tool call today returns two fields, `status` and
`message`, and the agent has no field that names the next step. This feature
adds, next to those two fields, a stable `error_kind` and a structured
`remediation` object, and records the kind in the audit log and a Prometheus
counter. The server instructions and the tool description contract are text
entities built from fixed rule records and from the exposure registry. Not
decided: the shape of `bank_compact`'s existing string `remediation`, whether
`conflict` results carry guidance, and the byte cap of the instructions
(see [research.md](research.md), "Open questions the plan cannot settle").

Line references are to commit 3b2db17 unless a path under `.venv/` is given.

## Guidance error payload

The dict a refused tool call returns. It extends today's error dict
(`docs/MCP_TOOLS_SPEC.md:108-121`) and is carried on the wire as described in
[contracts/guidance-error-payload.md](contracts/guidance-error-payload.md).

Fields:

- `status` (existing, string): `error` or `forbidden`. Unchanged value and
  meaning (FR-027). No code returns `forbidden` today; the value stays
  accepted because `docs/MCP_TOOLS_SPEC.md:118` documents it.
- `message` (existing, string): one sentence stating the cause, built from
  server-owned text. Unchanged value for every existing refusal site.
- `error_kind` (new, string): one value from the closed set below.
- `remediation` (new, object): see "Remediation object".
- `required_permission` (new, optional, string): one of `read`, `write`,
  `manage`, `admin`. Present when `error_kind` is `permission_insufficient`.
- `current_permissions` (new, optional, list of strings): the token's
  permissions as `system_whoami` reports them. Present with
  `required_permission`.
- `accessible_spaces` (new, optional, list of strings): the space identifiers
  the token may access, from the token's `allowed_resources`
  (`src/live_mem/auth/context.py:300-307`). Present when `error_kind` is
  `space_access_denied`. An empty list means the token has no space.
- `existing_job` (new, optional, object): the existing consolidation job's
  payload (`src/live_mem/core/consolidation_queue.py:480-500`). Present only
  on the FR-007 acknowledgement, which is not a refusal.

Existing fields that some refusals already carry stay unchanged: `reason`
(`src/live_mem/tools/admin.py:753`), `failure_reason`, `failed_phase`,
`rollback_outcome`, `failures` and the string `remediation` of `bank_compact`
(`src/live_mem/tools/bank.py:60-80`; open question Q1), `recovery_required`
(`src/live_mem/core/memory_id.py:57`), `rejected_volatile`
(`src/live_mem/tools/graph.py:878`), `mcp_reconnect_required`
(`src/live_mem/core/tokens.py:604`).

Validation rules:

- `status` and `message` are required and must keep the value the refusal
  site returned in 1.6.1 (FR-025, SC-005).
- `error_kind` is required on every refusal; a refusal without it receives
  `unclassified_refusal` at the conversion point and fails the replay test.
- `message` and every string inside `remediation` are built from literals in
  Hivemind's source, tool names, argument names, permission names and the
  token's own space identifiers; never from exception text, storage paths or
  provider output (FR-010).
- When `error_kind` is `permission_insufficient`, `required_permission` and
  `current_permissions` are required, and `remediation.next_tool` is absent
  (FR-004: no bypass is suggested).
- When `error_kind` is `space_access_denied`, `accessible_spaces` is required
  and `remediation.next_tool` is `space_list` (FR-005).
- When `error_kind` is `missing_argument` or `invalid_argument`,
  `remediation.missing_arguments` names the arguments and
  `remediation.reason` states the allowed values or format and one example
  (FR-006).

## Remediation object

The structured next step inside the Guidance error payload (FR-003).

Fields:

- `next_tool` (optional, string): a registered tool name the token may call.
  Absent when no corrective call exists for the agent. Never names a tool
  above the token's permission (Edge Cases).
- `arguments` (optional, object): arguments to pass to `next_tool` or to the
  retried call, when known. Values are server-owned (for example
  `{"confirm": true}`), never copied from the agent's input.
- `missing_arguments` (optional, list of strings): argument names that were
  missing or invalid.
- `required_prior_step` (optional, string): the prior step in the agent's
  vocabulary (a tool name or a fixed phrase such as "operator invitation").
- `reason` (required, string): one sentence explaining the step.
- `retryable` (required, boolean): whether the same call may be retried after
  the step.

Validation rules:

- `reason` and `retryable` are always present.
- At least one of `next_tool`, `arguments`, `missing_arguments`,
  `required_prior_step` is present unless the refusal names an operator action
  (`permission_insufficient`, `write_route_refused`, `internal_failure`,
  `operator_tool_refused`), where `reason` carries the operator workflow.

## Closed set of `error_kind` values

Grounded in the refusal sites listed in [research.md](research.md), Item 4.

- `authentication_missing`: no token identity reaches the guard.
- `token_scope_changed`: the token was re-scoped or revoked by an operator
  during the session (`src/live_mem/auth/context.py:237-238`); the agent must
  refresh its tool list (FR-009).
- `invalid_space_id`: malformed identifier or reserved namespace
  (`src/live_mem/auth/context.py:284-288`; `src/live_mem/core/memory_id.py:48-66`).
- `space_access_denied`: the space is outside the token's allowlist
  (`src/live_mem/auth/context.py:300-307`).
- `permission_insufficient`: the token lacks `write`, `manage` or `admin`
  for the operation (`src/live_mem/auth/context.py:355-358,383-386,409-412`).
- `missing_argument`: a required argument or confirmation flag is absent.
- `invalid_argument`: an argument is outside its allowed values or format.
- `required_prior_step_missing`: a prior tool call or operator action has not
  happened (no producer in 1.6.1; open question Q2).
- `operation_in_progress`: an operation on the same space is queued or
  running (open question Q3 for `conflict` results).
- `write_route_refused`: the space is shared, in recovery, or its critical
  state is corrupt (`src/live_mem/tools/admin.py:751-777`;
  `src/live_mem/tools/bank.py:1412-1437`; `src/live_mem/tools/live.py:118-134`).
- `internal_failure`: an unexpected exception rendered by `safe_error`
  (`src/live_mem/auth/context.py:415-438`).
- `operator_tool_refused`: reserved for specification 002; not produced by
  this feature.
- `unclassified_refusal`: assigned by the conversion point to a refusal
  without `error_kind`; a defect indicator, never a designed outcome.

State transitions: none. A refusal has no lifecycle.

## Tool outcome record (audit and metrics)

The entry the conversion point writes into the ASGI request state so the
middlewares can read it (FR-030; [research.md](research.md), Item 5).

Fields:

- `tool` (string): the canonical tool name from `TOOL_EXPOSURES`
  (`src/live_mem/tools/exposure.py:47-57`); `unknown` for a name outside the
  registry.
- `error_kind` (string): one value from the closed set.

Validation rules:

- Never contains argument values, remediation text, space identifiers or
  tokens.
- Written once per tool call, only for refusals.
- Read by `AuditMiddleware`, which adds `tool` and `error_kind` to its audit
  record (`src/live_mem/middleware.py:491-513`), and by `MetricsMiddleware`,
  which increments `livemem_tool_refusals_total{tool, error_kind}`
  (`src/live_mem/middleware.py:86-93,198-235`).

## Rollout setting

- Name: `mcp_refusal_is_error` in `Settings` (`src/live_mem/config.py:81-92`),
  environment variable `MCP_REFUSAL_IS_ERROR`.
- Type and default: boolean, `true`.
- Effect: `true` delivers refusals as MCP error results (`isError=true`);
  `false` delivers them as successful results with the same payload. It
  changes only the `isError` flag (FR-031).
- Validation: pydantic boolean parsing at startup; a startup warning when
  `false`.
- Lifecycle: removed in a later release that is not decided.

## Server instructions

- A tuple of rule records in `src/live_mem/instructions.py`, each with an
  identifier, the sentence sent to the agent, and the source document that
  states the rule (`docs/AGENT_MEMORY_SETUP.md`, `RULES/README.md`,
  `RULES/live-mem.standard.memory.bank.md`, `docs/MCP_TOOLS_SPEC.md`).
- `render_instructions()` joins the sentences in the FR-013 order into the
  text passed to `MCPServer.__init__(instructions=...)`.
- Validation: identical for every token (FR-016); no space identifier, token,
  endpoint or placeholder (FR-014); every FR-013 rule present and consistent
  with its source document (FR-015, FR-028); within a byte cap (open
  question Q4).

## Tool description contract

The fixed sections every `agent_core` description carries, in order
(FR-017). See [contracts/tool-description-contract.md](contracts/tool-description-contract.md).

- Purpose: one sentence.
- When to use.
- When not to use, with the tool to use instead.
- Preconditions: required permission (equal to the registry's
  `minimum_permission`, FR-019), space access when `space_scope_argument` is
  set, prior calls.
- Peer tools: before, after, instead.
- Result: what the agent does with the returned fields.

Relationships:

- Each description is bound to one `ToolExposure` entry
  (`src/live_mem/tools/exposure.py:47-57`) through the canonical name.
- Each alias shares the handler and the description with its canonical name
  (`src/live_mem/tools/exposure.py:943-954`); the description names the
  canonical tool (FR-020).
- Each hidden operator tool's description states that it is an operator
  tool and names the agent tool that replaces it where one exists (FR-021).

## Exposure registry entry (existing, unchanged)

`ToolExposure(canonical_name, aliases, audience, minimum_permission,
operation, space_scope_argument)` (`src/live_mem/tools/exposure.py:47-57`).
The remediation builder reads `minimum_permission` to fill
`required_permission`, `space_scope_argument` to know which argument names
the space, and `canonical_name` to label the audit entry and the counter.
`tests/fixtures/tool_surface.json` is unchanged (FR-023).
