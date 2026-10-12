# Feature Specification: Tool guidance and remediation path for agents

**Feature Branch**: `spec/001-tool-guidance-remediation`

**Created**: 2026-10-12

**Status**: Draft

**Input**: User description: "Improve Hivemind tool descriptions (purpose,
when to use, when not to, preconditions, peer tools and call order), make tool
errors consistent with a clear cause, send MCP server instructions at
initialize so agents follow the memory-bank rules, the permission model and
the write discipline, and add a remediation path: when an agent calls a tool
wrongly or out of order, the tool returns a recoverable, structured error that
tells the agent the corrective tool call, the missing argument or the required
prior step, so the agent can fix itself and the rules are enforced."

**Summary**: An agent connected to Hivemind today chooses tools from
docstrings that do not state preconditions or peer tools, receives
`{"status": "error", "message": "..."}` with no field that names the next step,
and receives no server instructions at initialize
([research.md](research.md), "Gaps this specification must close"). What is
needed is one error shape with a structured remediation, descriptions that
carry purpose, preconditions, required permission and peer tools, and server
instructions that carry the memory-bank workflow. Decided on 2026-10-12 (see
Clarifications): a refused call travels as an MCP error result with
`isError=true` carrying the JSON payload; `status` and `message` keep their
current values and every new field is additive; the server instructions
complement the client-side instruction block, which stays.

Constitution principles cited below are from
[`.specify/memory/constitution.md`](../../.specify/memory/constitution.md).

## Clarifications

### Session 2026-10-12

- Q: Is a refused call delivered as a successful MCP result whose content is
  the payload, or as an MCP error result with `isError=true`? → A: As an MCP
  error result with `isError=true`, carrying the same JSON payload in its
  content. The planning stage must check every client that reads tool results
  so none of them breaks (FR-026, FR-029).
- Q: Must the payload keep only `status` and `message` unchanged with every
  new field additive, or may the `status` vocabulary gain a value such as
  `needs_action`? → A: Additive only. `status` and `message` keep their
  current values and meaning; every new field (for example `recoverable`,
  `next_action`, `missing_arguments`) is added alongside. No new status value
  (FR-027).
- Q: Do the server instructions replace or complement the project instruction
  block in `docs/AGENT_MEMORY_SETUP.md`? → A: Complement. The block stays, the
  server instructions are added, and a test checks that the two stay
  consistent, because some MCP clients do not show server instructions to the
  model, so the block still covers them (FR-028).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An agent recovers from a wrong or out-of-order call without human help (Priority: P1)

An agent working in a Hivemind space calls a tool with a missing argument, in
the wrong order, against a space it cannot access, or with a token that lacks
the permission. Instead of a bare failure, the agent views a structured error
that states the cause and the next step: the tool to call, the arguments to
pass, or the prior step to complete. The agent makes the corrective call and
continues. When no corrective call exists for the agent (for example the token
lacks `manage`), the error says so and names who can act (an operator with a
`manage` or `admin` token).

**Why this priority**: Every other improvement depends on it. Descriptions and
instructions reduce wrong calls; the remediation path is what lets an agent
fix the wrong calls that still happen, and it is the only one of the four
improvements that enforces the rules at call time.

**Independent Test**: With the current descriptions and no server
instructions, replay a fixed set of wrong calls (see Edge Cases) through an
MCP client with a `read,write` token and verify that each response carries the
remediation fields of the Guidance error payload and that performing the named
corrective call succeeds or is itself a documented refusal.

**Acceptance Scenarios**:

1. **Given** a `read,write` token with access to space `p`, **When** the agent
   calls `mid_consolidate(space_id="p")` while a consolidation for `p` is
   already queued or running, **Then** the response states that a job exists,
   gives its job identifier, and names `return_to_user_without_polling` as the
   next action, so the agent does not submit a second job.
2. **Given** a `read,write` token with no access to space `q`, **When** the
   agent calls `short_note(space_id="q", ...)`, **Then** the response states
   that the token has no access to `q`, lists the spaces the token can access,
   and names `space_list` as the call that shows them.
3. **Given** a `read,write` token, **When** the agent calls
   `space_create(...)`, **Then** the response states that `manage` permission
   is required, that the current token holds `read,write`, and that an operator
   must create the space or issue a `manage` token; the response does not
   suggest a way around the permission.
4. **Given** a space `p` that is shared (staged write route), **When** the
   agent calls `short_note(space_id="p", ...)`, **Then** the response states
   that writes to a shared space are not accepted on this route and names the
   workflow the operator must use, instead of "Internal server error".
5. **Given** any tool, **When** the agent omits a required argument or passes a
   value outside the allowed set (for example a `category` outside the seven
   note categories), **Then** the response names the argument, the allowed
   values or format, and an example value.
6. **Given** a token whose scope was changed by an operator, **When** the
   agent's next call is refused because of the change, **Then** the response
   tells the agent to refresh its tool list before retrying.

---

### User Story 2 - An agent chooses the right tool from its description (Priority: P2)

An agent lists the tools and reads each description before calling. From the
description alone it learns what the tool is for, when to use it, when not to
use it (and which tool to use instead), what must be true before calling it
(permission, space access, prior calls), and which tools it is normally paired
with. The agent then follows the memory workflow without having read the
project documentation.

**Why this priority**: It removes wrong calls before they happen and costs no
protocol change, but it cannot enforce anything on its own.

**Independent Test**: For every `agent_core` tool, a reviewer checks the
description against the Tool description contract (Key Entities) and an
automated check verifies that every required section is present and that the
discovery response stays within the size budget.

**Acceptance Scenarios**:

1. **Given** the 24 `agent_core` tools, **When** an agent views `tools/list`,
   **Then** every description states purpose, when to use, when not to use (with
   the alternative), preconditions, required permission, and peer tools in a
   fixed order.
2. **Given** `short_note`, **When** the agent reads its description, **Then**
   it learns the seven categories, that the agent identity comes from the token
   and must not be passed, and that `mid_write` is not an alternative for
   agents.
3. **Given** `mid_consolidate`, **When** the agent reads its description,
   **Then** it learns to call it at most once at the end of a work block, after
   user validation, and to return to the user without polling.
4. **Given** a historical alias such as `live_note`, **When** an agent views
   it, **Then** the description is identical to `short_note` and names
   `short_note` as the canonical name (constitution principle III).
5. **Given** the `write` discovery profile, **When** the serialized tool list
   is measured, **Then** it stays within the documented discovery budget.

---

### User Story 3 - An agent receives the Hivemind rules from the server at connection time (Priority: P3)

When an agent's MCP client initializes a session, the server returns
instructions. The agent reads them before its first tool call and learns the
memory workflow (start-of-task reads, atomic notes, single consolidation at
the end, no polling), the permission model and what its own token can do, and
the write discipline (never write the bank directly, long memory is derived
and non-authoritative, no secrets in notes). An agent whose client has no
project instruction file still follows the rules.

**Why this priority**: It closes the gap for clients without an instruction
file and makes the rules the same for every client, but the agent can already
get the rules from `docs/AGENT_MEMORY_SETUP.md` and `space_rules`.

**Independent Test**: Initialize an MCP session with a `read,write` token and
verify that the returned instructions contain each rule listed in FR-013, and
that an agent given only these instructions performs the start-of-task calls
before its first mutation in a scripted session.

**Acceptance Scenarios**:

1. **Given** a `read,write` token, **When** the client initializes, **Then**
   the instructions name the start-of-task calls (`space_rules`,
   `mid_read_all`, `short_read`), the note categories, the end-of-block rule
   (`mid_consolidate` at most once, no polling) and the "stop before mutation
   if a startup call fails" rule.
2. **Given** any token, **When** the client initializes, **Then** the
   instructions state the four permission levels, that a token sees only the
   tools its permission allows, and that `system_whoami` shows the token's
   permissions and spaces.
3. **Given** the instructions, **When** they are compared with
   `docs/AGENT_MEMORY_SETUP.md` and `RULES/live-mem.standard.memory.bank.md`,
   **Then** no rule contradicts those documents.
4. **Given** the instructions, **When** they are searched for a space
   identifier, a token or an endpoint, **Then** none is present.

---

### User Story 4 - An operator views the same error structure in every client (Priority: P4)

An operator using the Hivemind CLI, the admin console or `hivemind-ingest`
receives errors with the same fields as an agent does: the cause, a stable
error kind, and the remediation. Existing displays that show `message`
continue to work after refused calls become MCP error results; displays can
additionally show the remediation.

**Why this priority**: Consistency across clients lowers support effort, but
the existing clients already work with `status` and `message`.

**Independent Test**: Run one refused call through each client (CLI `space
info` on an inaccessible space, console `/api/tool` proxy, `hivemind-ingest`
against a space without access) and verify each displays `message` as today
and that the raw payload carries the same remediation fields.

**Acceptance Scenarios**:

1. **Given** the CLI, **When** a tool call is refused, **Then** the CLI shows
   `message` as today and the payload it parsed contains the remediation
   fields.
2. **Given** the admin console, **When** a `/api/tool` call is refused,
   **Then** the console shows `message` as today and the HTTP 403 handling that
   matches the word "write" in `message` still matches.
3. **Given** `hivemind-ingest`, **When** a call is refused, **Then** the
   command reports `status: error` and the `message`, as today.

---

### Edge Cases

- An agent calls a historical alias (`live_note`): the remediation names the
  canonical tool (`short_note`) but the alias keeps working (constitution
  principle III).
- An agent calls a hidden operator tool by exact name (`mid_write`) with a
  `read,write` token: the response refuses with the permission cause and says
  that agents write through `short_note`; it does not reveal more about hidden
  tools than the permission cause.
- An agent's token has `space_ids: []` (fresh token): every space-scoped call
  is refused; the remediation says that a manager must invite the token with
  `space_invite_token` and names `system_whoami` to view the current scope.
- The space identifier is malformed or reserved (`_system`, `_backups`): the
  remediation gives the allowed format; it does not say whether the reserved
  space exists.
- Critical Hivemind state is corrupt or the route is in recovery: the response
  refuses, names the operator action, and never suggests a fallback route
  (constitution principle I; `src/live_mem/tools/bank.py:1412-1460`).
- `MCP_SERVER_DEBUG` is off: the remediation must be built from server-owned
  text, never from exception text, so it cannot leak provider prompts or
  storage details (constitution principle IV).
- A consolidation job exists for the space: the response is not an error; it
  returns the existing job and `next_action="return_to_user_without_polling"`.
- The remediation names a tool the token cannot see in `tools/list` (for
  example `space_create` for a `read,write` token): the response says the tool
  needs a higher permission and does not ask the agent to call it.
- A description longer than the discovery budget allows: the build fails; the
  budget is not raised silently.
- The server instructions are longer than a client displays: the first lines
  carry the start-of-task calls and the write discipline so a truncated view
  still shows them.
- A client that ignores server instructions: the tool descriptions and the
  remediation path still apply; the instructions are not the only carrier of
  the rules.

## Requirements *(mandatory)*

### Functional Requirements

Guidance error payload

- **FR-001**: Every refused tool call MUST return a Guidance error payload (Key
  Entities) that keeps `status` and `message` with their current meaning and
  adds `error_kind` and `remediation`.
- **FR-002**: `error_kind` MUST be one value from a documented closed set that
  covers at least: authentication missing, space access denied, permission
  insufficient, invalid argument, missing argument, invalid space identifier,
  required prior step missing, operation already in progress, write route
  refused (shared space, recovery or corrupt state), token scope changed, and
  internal failure.
- **FR-003**: `remediation` MUST state, in the agent's vocabulary, the next
  step as structured fields: the tool to call (`next_tool`), the arguments to
  pass when known (`arguments`), the missing or invalid argument names
  (`missing_arguments`), the required prior step (`required_prior_step`), a
  one-sentence explanation (`reason`), and whether the same call may be
  retried after the step (`retryable`).
- **FR-004**: When the cause is a permission the token does not hold, the
  payload MUST name the required permission and the token's current
  permissions, MUST state that an operator with `manage` or `admin` can act,
  and MUST NOT suggest any way to bypass the check (constitution principle II).
- **FR-005**: When the cause is a space the token cannot access, the payload
  MUST list the spaces the token can access (as `system_whoami` reports them)
  and name `space_list`.
- **FR-006**: When the cause is a missing or invalid argument, the payload
  MUST name the argument, the allowed values or format, and one example.
- **FR-007**: When a consolidation job already exists for the space, the
  response MUST return the existing job's identifier and status with
  `next_action="return_to_user_without_polling"` and MUST NOT queue a second
  job for the same request.
- **FR-008**: When a write is refused because the space is shared, in
  recovery, or its state is corrupt, the payload MUST say so in server-owned
  text and name the operator workflow; it MUST NOT return "Internal server
  error" for this cause and MUST NOT suggest a fallback route (constitution
  principle I).
- **FR-009**: When a call is refused because the token's scope changed since
  the last discovery, the payload MUST tell the agent to refresh its tool list,
  consistent with the existing `mcp_reconnect_required` field.
- **FR-010**: Remediation text MUST be built only from server-owned literals
  and registry data (tool names, argument names, permission names, space
  identifiers the token may access); it MUST NOT include exception text,
  storage paths, provider output or secrets, in any debug mode (constitution
  principle IV).
- **FR-011**: Every tool, including historical aliases and hidden operator
  tools, MUST use the same payload for its refusals; the `/api/tool` proxy MUST
  return it unchanged.
- **FR-012**: The documented return format in `docs/MCP_TOOLS_SPEC.md` MUST be
  updated to show the payload, the closed set of `error_kind` values and one
  example per value.

Server instructions

- **FR-013**: The MCP server MUST send instructions at initialize that contain,
  in this order: the start-of-task calls and the rule to stop before mutation
  if one fails; the write discipline (atomic `short_note` entries in the seven
  categories, agent identity comes from the token, never write the bank
  directly, no secrets in notes); the end-of-block rule (`mid_consolidate` at
  most once after user validation, return without polling); the permission
  model (`admin ⊃ manage ⊃ write ⊃ read`, a token views only the tools its
  permission allows, `system_whoami` shows its scope, a token with no spaces
  needs an invitation); the long-memory rule (derived, non-authoritative,
  repository files win, no routine `long_push` or ingestion); and how to read a
  Guidance error payload.
- **FR-014**: The instructions MUST NOT contain a space identifier, a token, an
  endpoint or any deployment-specific value.
- **FR-015**: The instructions MUST agree with `docs/AGENT_MEMORY_SETUP.md`,
  `RULES/README.md` and `RULES/live-mem.standard.memory.bank.md`; a contract
  test MUST fail when a rule named in FR-013 is absent.
- **FR-016**: The instructions MUST be the same for every token; per-space
  rules stay in `space_rules`.

Tool descriptions

- **FR-017**: Every `agent_core` tool description MUST follow the Tool
  description contract (Key Entities): purpose, when to use, when not to use
  with the alternative, preconditions (permission, space access, prior calls),
  peer tools, and the result the agent should act on.
- **FR-018**: Every parameter description MUST state the allowed values or
  format and, where a default exists, the default.
- **FR-019**: The required permission stated in a description MUST equal the
  registry's minimum permission for that tool, verified by a contract test.
- **FR-020**: Each historical alias MUST keep a description identical to its
  canonical tool, and that description MUST name the canonical tool
  (constitution principle III).
- **FR-021**: Hidden operator tools MUST state in their description that they
  are operator tools and name the agent tool that replaces them where one
  exists (for example `mid_write` names `short_note` and `mid_consolidate`).
- **FR-022**: The serialized `tools/list` response for each discovery profile
  MUST stay within the documented discovery budget; a contract test MUST fail
  otherwise.
- **FR-023**: `tests/fixtures/tool_surface.json` MUST NOT need a change for
  this feature, because no tool is added, removed, renamed or re-levelled
  (constitution principle III).

Verification

- **FR-024**: A replay test MUST exercise every `error_kind` value at least
  once through a real in-process MCP server and assert the payload fields
  (constitution principle VI).
- **FR-025**: Existing tests that assert `{"status": "error", "message": ...}`
  MUST keep passing without edits to their assertions on `status` and
  `message`.
- **FR-026**: A refused call MUST be delivered as an MCP error result with
  `isError=true` whose content carries the Guidance error payload as JSON
  text. A successful call keeps today's delivery.
- **FR-027**: The payload MUST keep `status` and `message` with their current
  values and meaning; every new field (for example `recoverable`,
  `next_action`, `error_kind`, `remediation`, `missing_arguments`) MUST be
  added alongside them. The `status` vocabulary MUST NOT gain a new value.
- **FR-028**: The server instructions MUST complement, not replace, the
  project instruction block in `docs/AGENT_MEMORY_SETUP.md:89-166`. The block
  stays because some MCP clients do not show server instructions to the model.
  A contract test MUST fail when a rule named in FR-013 is present in one of
  the two and absent or contradicted in the other.
- **FR-029**: Before implementation, the planning stage MUST check every
  client that reads tool results and record the result for each:
  - the Hivemind CLI, which reads `content[0].text` whatever `isError` says
    (`scripts/cli/client.py:100-108`);
  - the admin console, which calls tools through the `/api/tool` proxy, not
    through MCP (`src/live_mem/tools/__init__.py:83-137`), so the proxy's
    rendering of an error result must be checked;
  - `hivemind-ingest`, which maps `isError: true` to `status: error` and must
    still expose the payload's fields
    (`tools/hivemind-ingest/internal/mcpclient/client.go:410-448`).
  No client may lose the `message` it displays today.

### Key Entities

- **Guidance error payload**: the dict a refused tool call returns. Fields:
  `status` (existing, `error`), `message` (existing, one sentence stating the
  cause), `error_kind` (one value from the closed set in FR-002),
  `remediation` (object: `next_tool`, `arguments`, `missing_arguments`,
  `required_prior_step`, `reason`, `retryable`), and, when they apply,
  `required_permission`, `current_permissions`, `accessible_spaces`,
  `existing_job`. It extends the existing `bank_compact` failure object
  (`src/live_mem/tools/bank.py:44-80`) and the consolidation acknowledgement's
  `next_action` and `polling` fields (`src/live_mem/core/consolidation_queue.py:35-40`).
- **Tool description contract**: the fixed sections every `agent_core`
  description carries, in order: purpose (one sentence), when to use, when not
  to use and the alternative, preconditions (required permission, space
  access, prior calls), peer tools (before, after, instead), and what to do
  with the result. The required permission and space argument come from the
  exposure registry entry (`src/live_mem/tools/exposure.py:47-57`).
- **Server instructions**: the text sent at initialize, built from the rules
  in FR-013, identical for every token, free of deployment values.
- **Exposure registry entry** (existing): canonical name, aliases, audience,
  minimum permission, operation and space-scope argument
  (`src/live_mem/tools/exposure.py:47-57`). The remediation and the description
  contract read from it so that the stated permission cannot drift from the
  enforced one.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a replay of at least 20 wrong or out-of-order calls covering
  every `error_kind`, 100% of responses carry a Guidance error payload, and for
  every response whose `remediation.next_tool` is a tool the token may call,
  performing that call with the given arguments succeeds.
- **SC-002**: 0 responses in the replay contain "Internal server error" for a
  cause listed in FR-002 other than internal failure, with debug mode off.
- **SC-003**: 100% of the 24 `agent_core` tool descriptions and 100% of their
  parameters pass the description contract check; every discovery profile
  stays within the documented discovery budget.
- **SC-004**: An agent given only the server instructions, in a scripted
  session with a `read,write` token, performs `space_rules`, `mid_read_all`
  and `short_read` before its first `short_note`, writes notes only in the
  seven categories, calls `mid_consolidate` once, and makes no status call
  afterwards, in 10 of 10 scripted runs.
- **SC-005**: The existing nominal test suite passes with no change to any
  assertion on `status` or `message`, and the CLI, admin console and
  `hivemind-ingest` display refused calls exactly as they do in 1.6.1.
- **SC-006**: `docs/MCP_TOOLS_SPEC.md` documents every `error_kind` with one
  example, and `scripts/check_doc_links.py` passes.

## Assumptions

- The MCP SDK in use (2.3.0, `uv.lock:714-715`) accepts an `instructions`
  argument at server construction (`MCPServer.__init__` signature) and sends it
  at initialize; no SDK change is needed.
- The 64 KiB discovery budget (`src/live_mem/tools/exposure.py:442`) stays as
  it is; richer descriptions must fit within it. If they cannot, the budget
  change is a separate decision.
- Hidden operator tools keep their current visibility rules; this feature
  changes their descriptions and refusals, not their discovery.
- No tool is added, removed, renamed or re-levelled, so
  `tests/fixtures/tool_surface.json` is unchanged (constitution principle
  III).
- The remediation never performs the corrective call on the agent's behalf; it
  only names it. Authorization stays in each handler (constitution principle
  II).
- Per-space consolidation rules (`space_rules`) remain the place for
  project-specific structure; the server instructions carry only the rules
  common to every space.
- The remediation vocabulary (tool names, argument names, permission names)
  comes from the exposure registry and the handlers' own validation; no new
  source of truth is introduced.
- The free-text remediation convention is borrowed from the Infrahub MCP
  server (`/home/user/infrahub-mcp/src/infrahub_mcp/utils.py:451-457` and the
  call sites listed in [research.md](research.md)); the structured fields are
  Hivemind's own, because its clients consume dict payloads.
