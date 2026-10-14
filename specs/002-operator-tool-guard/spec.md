# Feature Specification: Per-bearer-token guard for operator tools over MCP

**Feature Branch**: `spec/002-operator-tool-guard`

**Created**: 2026-10-12

**Status**: Draft

**Input**: User description: "Hivemind hides `audience=operator` tools from
every discovery profile, but they stay registered and callable by exact name;
each handler only checks permission, so an agent holding a `manage` bearer
token can still call them over `/mcp`. The Hivemind CLI legitimately calls
several operator tools over `/mcp`, so a server-wide block is not acceptable.
Add a per-bearer-token control: each bearer token carries an attribute that
allows or refuses operator tools over `/mcp`; existing tokens keep today's
behaviour; tokens created for agents refuse by default; operators set or
change the attribute through the existing token admin paths; enforcement is
central, driven by the registry's audience, covers every alias, fails closed,
keeps every handler's own permission check, returns a server-owned refusal
that names the operator path, and is recorded in the audit log."

**Summary**: Today an agent whose bearer token holds `manage` can call every
hidden operator tool over `/mcp` by exact name, because hiding a tool from
discovery is not authorization and each handler checks only the permission
level ([research.md](research.md), "What stops an agent today"). The Hivemind
CLI uses the same `/mcp` route and the same tools for operator work, so the
server cannot refuse them for everyone. What is needed is one attribute on
each bearer token, `operator_tools`, that the server checks in one place for
every `audience=operator` tool and every alias before the handler runs, with
existing tokens unchanged, tokens created through the agent path refusing by
default, and every refusal audited. Not decided here: the structured error
shape (`error_kind`, `remediation`) that spec 001 will add to every refusal;
this specification names the refusal cause so spec 001 can map it.

Constitution principles cited below are from
[`.specify/memory/constitution.md`](../../.specify/memory/constitution.md).

## Clarifications

No clarification session was held. Each point where the feature description
left a choice open was decided without asking, with the least change in
behaviour for existing deployments and a closed default for agents. Every such
decision is listed under "Assumptions" with its reason.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An agent with a `manage` token cannot call operator tools over MCP (Priority: P1)

An agent connects to `/mcp` with a bearer token that an operator or a manager
created for it. The token holds `read,write,manage` so the agent can create
spaces and invite tokens. The agent calls `mid_write` (or its historical alias
`bank_write`, or any other tool that the exposure registry marks
`audience=operator`) by exact name. The server refuses before the handler
runs, returns a result that states an operator must perform the action through
the admin console or the CLI with an operator token, and writes an audit
entry. The agent's ordinary tools (`short_note`, `mid_consolidate`,
`space_create`, `token_create`) keep working.

**Why this priority**: It closes the gap the feature exists for. Without it,
every other part of the feature (the attribute, the admin paths) has no
effect.

**Independent Test**: Open a real MCP session through the production
application factory with a `manage` bearer token whose `operator_tools`
attribute is `false`; call `mid_write` and `bank_write`; verify both are
refused with the same server-owned message, that the audit log holds one
entry per refusal, and that `short_note` is not refused by the guard.

**Acceptance Scenarios**:

1. **Given** a `read,write,manage` bearer token with `operator_tools: false`
   and access to space `p`, **When** the agent calls `mid_write(space_id="p",
   ...)` over `/mcp`, **Then** the response is `{"status": "forbidden",
   "message": ...}`, the message says an operator must perform the action
   through the admin console or the CLI with an operator token, and the
   handler does not run.
2. **Given** the same token, **When** the agent calls the alias `bank_write`
   with the same arguments, **Then** the response is identical to scenario 1.
3. **Given** the same token, **When** the agent calls `space_export`,
   `backup_list`, `admin_list_tokens` or any other `audience=operator` tool,
   **Then** each call is refused in the same way, whatever permission the
   handler would require.
4. **Given** the same token, **When** the agent calls `short_note`,
   `mid_consolidate`, `space_create` or `token_create`, **Then** the guard
   does not refuse the call and the handler's own checks decide.
5. **Given** any refused call, **When** the operator reads the audit log,
   **Then** one entry records the event, the tool name as called, the
   canonical name, the refusal cause, the token name and the authentication
   type, and no argument value.
6. **Given** a tool name that is not registered, **When** the agent calls it,
   **Then** the response is the SDK's "Unknown tool" result, as today; the
   guard changes nothing for unregistered names.

---

### User Story 2 - An operator's existing tokens, CLI and admin console keep working after the upgrade (Priority: P2)

An operator upgrades Hivemind. Every bearer token in the token store was
created before the attribute existed. The operator runs `mcp_cli.py space
export`, `backup create`, `token list` and `token update` with an existing
`manage` or `admin` token over `/mcp`, and uses the admin console with the
same token. Every call succeeds as before the upgrade. The operator later
creates a token for an agent and that token refuses operator tools without any
extra step.

**Why this priority**: The feature must not break operator workflows; a guard
that forces every operator to edit every token on upgrade would be reverted.

**Independent Test**: Load a token record that has no `operator_tools` key,
validate it, and call an operator tool over `/mcp` and through `/api/tool`;
verify both reach the handler. Run the CLI client's `call_tool` against the
production application with such a token for `space_export` and verify the
handler result comes back.

**Acceptance Scenarios**:

1. **Given** a stored token record without an `operator_tools` key, **When**
   the server validates the bearer token, **Then** the token behaves as
   `operator_tools: true` and operator tools over `/mcp` are not refused.
2. **Given** a `manage` token with `operator_tools: true`, **When** the CLI
   calls `space_export`, `space_update`, `backup_create`, `backup_download`,
   `admin_list_tokens` or `admin_gc_notes` over `/mcp`, **Then** the guard
   lets each call reach its handler.
3. **Given** the bootstrap key, **When** it calls an operator tool, **Then**
   the guard lets the call reach its handler (the bootstrap identity carries
   no token record and is an operator credential).
4. **Given** an operator token, **When** the admin console calls any operator
   tool through `POST /api/tool`, **Then** the call reaches its handler as
   today.
5. **Given** a token with `operator_tools: false`, **When** it calls an
   operator tool through `POST /api/tool`, **Then** the call is refused with
   the same payload as over `/mcp` and audited, because `/api/tool` accepts
   the same bearer token in the `Authorization` header and would otherwise be
   a way around the guard.

---

### User Story 3 - An operator sets or changes the attribute through the token admin paths (Priority: P3)

An operator creates a token for a colleague who runs the CLI, and a token for
an agent. The operator chooses, at creation or later, whether each token may
call operator tools over `/mcp`: through `admin_create_token`,
`admin_update_token`, `admin_bulk_update_tokens`, the CLI `token create`,
`token update` and `token bulk-update` commands, or the admin console's
token forms. The operator views the attribute in `admin_list_tokens`, the CLI
token list and the console token list. The same `admin` permission check that
protects those paths today protects the attribute.

**Why this priority**: Without it, the default decided at creation could not
be corrected, and an agent token could never be turned into an operator token
or the reverse.

**Independent Test**: Call `admin_update_token(token_hash=..., operator_tools=
false)` and `admin_bulk_update_tokens(names=..., operator_tools=true)` with an
admin token; verify the stored records change, that the server invalidates
the cached identity so the next call uses the new value, and that a `manage`
token cannot change the attribute.

**Acceptance Scenarios**:

1. **Given** an admin token, **When** it calls `admin_create_token(name=...,
   permissions="read,write,manage", operator_tools=false)`, **Then** the
   stored record carries `operator_tools: false` and the response reports it.
2. **Given** an admin token, **When** it calls `admin_create_token` without
   `operator_tools`, **Then** the stored record carries `operator_tools: true`
   (operator path, unchanged default).
3. **Given** a manager or admin token, **When** it calls `token_create(...)`,
   **Then** the stored record carries `operator_tools: false`, and
   `token_create` has no parameter that could set it to `true`.
4. **Given** an admin token, **When** it calls `admin_update_token` with
   `operator_tools=true` or `false`, **Then** the record changes, the
   response says the attribute changed, and the token's next MCP call uses the
   new value without reconnecting.
5. **Given** an admin token, **When** it calls `admin_bulk_update_tokens` with
   a selection filter and `operator_tools`, **Then** every selected active
   token changes and the report shows the value before and after.
6. **Given** a `manage` token, **When** it calls `admin_update_token`,
   **Then** the call is refused by the existing admin permission check, as
   today.
7. **Given** any token list (`admin_list_tokens`, CLI `token list`, console
   Access view), **When** the operator views it, **Then** each token shows
   whether it may call operator tools over `/mcp`.

---

### Edge Cases

- A tool is registered on the server but has no entry in the exposure
  registry (cannot happen after startup validation, which refuses to start on
  a partial registry): the guard refuses the call and audits it with the cause
  `audience_unresolved` (constitution principle I).
- The token record's `operator_tools` value is not a boolean: the token store
  refuses to load (as it does today for a non-boolean `revoked`), and the
  guard refuses the call when a synthetic identity carries a non-boolean
  value.
- The MCP request carries no identity (missing or invalid bearer token): there
  is no attribute to read, so the guard leaves the call to the handler, whose
  own authentication check refuses it ("Authentication required"), as today
  (constitution principle II). Nothing is granted on this path.
- A token's attribute is changed while an MCP session is open: the next call
  on that session uses the new value, because the server re-validates the
  bearer token on every HTTP request and the guard reads the per-request
  identity, not the session's cached one.
- An agent calls an operator tool whose handler requires only `read` (for
  example `space_export`) with a `read` token that refuses operator tools:
  the guard refuses before the handler, so the attribute applies at every
  permission level.
- An admin token with `operator_tools: false`: the guard refuses its operator
  tool calls over `/mcp` and `/api/tool` like any other token; the attribute
  does not depend on the permission level. An operator can restore it with
  another admin token or the bootstrap key.
- The refusal message names no other tool and does not say which operator
  tools exist, so an agent cannot enumerate the hidden surface from refusals.
- Discovery (`tools/list`) does not change: operator tools were already not
  advertised, and the guard does not add or remove any name.

## Requirements *(mandatory)*

### Functional Requirements

Token attribute

- **FR-001**: Every stored bearer token record MUST carry a boolean attribute
  `operator_tools`. A record without the attribute MUST be read as `true`, so
  every token created before this feature keeps today's behaviour. A record
  whose attribute is not a boolean MUST make the token store refuse to load,
  as a non-boolean `revoked` does today.
- **FR-002**: The validated identity that the server attaches to each HTTP
  request MUST carry the token's `operator_tools` value, and the identity of
  the bootstrap key MUST carry none (read as allowed).
- **FR-003**: `system_whoami` MUST report the effective value as
  `operator_tools` so an agent or operator can view it without reading the
  token store.

Creation defaults

- **FR-004**: `token_create` (the agent-facing provisioning tool, discovered at
  `manage` and `admin`) MUST create tokens with `operator_tools: false` and
  MUST NOT accept a parameter that sets it, so that a `manage` token refused
  operator tools cannot create a token that is allowed them.
- **FR-005**: `admin_create_token` (admin-only operator path) MUST create tokens
  with `operator_tools: true` unless the caller passes `operator_tools=false`.
- **FR-006**: The CLI `token create` command MUST accept
  `--operator-tools/--no-operator-tools` and pass the value only when the
  caller is routed to `admin_create_token`; when the caller is a manager and
  routed to `token_create`, the command MUST refuse the option with a usage
  error that says the created token refuses operator tools.

Admin paths

- **FR-007**: `admin_update_token` MUST accept an optional `operator_tools`
  parameter (`"true"`, `"false"` or empty for no change), persist the change,
  invalidate the token's cached identity so the next request uses the new
  value, and report `operator_tools_before` and `operator_tools_after` when
  the value changed.
- **FR-008**: `admin_bulk_update_tokens` MUST accept the same parameter as one
  of the operations that satisfies "at least one operation", apply it to every
  selected active token, and report the value before and after per token.
- **FR-009**: `admin_list_tokens` MUST include `operator_tools` for each token.
  The CLI `token list` and the admin console's token list MUST show it.
- **FR-010**: The CLI `token update` and `token bulk-update` commands and the
  admin console's token edit form MUST let an admin set the attribute through
  `admin_update_token` and `admin_bulk_update_tokens`, with no new permission
  check beyond the admin check those tools already perform.

Enforcement

- **FR-011**: The server MUST check the attribute in one place on the MCP tool
  call path, before the tool handler runs, for every registered name whose
  exposure registry entry has `audience=operator`, including every historical
  alias of such a tool. The check MUST read the audience from the exposure
  registry, never from a list of names kept by hand.
- **FR-012**: When the called name is registered but its audience cannot be
  resolved from the registry, the guard MUST refuse the call.
- **FR-013**: When the request carries no identity, the guard MUST leave the
  call to the handler's own authentication check, which refuses it today for
  every registered tool (`tests/test_mcp_tool_surface.py` pins an
  authentication gate on each handler); the guard writes no refusal entry in
  that case. A missing attribute on an identity MUST be read as allowed
  (FR-001); any non-boolean value MUST be read as refused.
- **FR-014**: The guard MUST NOT replace any handler's own permission or space
  check (constitution principle II). A call that the guard lets through is
  still subject to every check the handler performs today.
- **FR-015**: The guard MUST also apply to `POST /api/tool`, because that
  route accepts the same bearer token in the `Authorization` header and calls
  handlers in-process without going through `/mcp`. A token with
  `operator_tools: true` MUST keep every `/api/tool` call it can make today.
- **FR-016**: A name that is not registered MUST be handled exactly as today
  (the SDK's "Unknown tool" result over `/mcp`, `Unknown tool: <name>` on
  `/api/tool`).
- **FR-017**: Discovery (`tools/list`) MUST NOT change.

Refusal

- **FR-018**: A refused call MUST return a server-owned result
  `{"status": "forbidden", "message": <text>}` where the text says that the
  tool is reserved for operators and that an operator must perform the action
  through the admin console or the CLI with an operator token. The text MUST
  be identical for every operator tool and alias and MUST NOT name any other
  tool or say whether other hidden tools exist.
- **FR-019**: The refusal cause MUST be named `operator_tools_refused` in the
  code and in the audit entry. When spec 001 adds `error_kind` to refusals,
  this cause maps to a new value `operator_tools_refused` in spec 001's closed
  set (FR-002 of spec 001 says the set "covers at least" its listed values, so
  a value can be added), with no corrective call for the agent and the
  remediation "an operator must perform the action". It does not map to
  `permission insufficient`, because raising the permission level does not
  lift the refusal.

Audit

- **FR-020**: Every refusal MUST write one JSON entry to the `live_mem.audit`
  logger with `event: "operator_tool_refused"`, the request identifier, the
  name as called, the canonical name, the cause, the route (`mcp` or
  `api_tool`), the token name and the authentication type. The entry MUST NOT
  contain argument values, space identifiers or the bearer token
  (constitution principles IV and VII).

Contract and documentation

- **FR-021**: `tests/fixtures/tool_surface.json` MUST record the attribute
  contract (attribute name, default for records without it, creation-path
  defaults, and the three admin tools that accept the parameter), and a test
  MUST compare the running code with it, so that a later change to the
  contract is a deliberate fixture edit (constitution principle III). No tool
  is added, removed, renamed or re-levelled; the discovery fixture does not
  change.
- **FR-022**: `docs/MCP_TOOLS_SPEC.md`, `docs/TOOL_EXPOSURE.md` (through its
  generator), `docs/DEPLOYMENT.md`, `docs/SECURITY.md` and `CHANGELOG.md`
  MUST describe the attribute, the defaults, the guard, the refusal and the
  audit entry.

### Key Entities

- **Bearer token record** (existing, `_system/tokens.json`): hash, name,
  email, permissions, space_ids, created_at, expires_at, last_used_at,
  revoked. This feature adds `operator_tools` (boolean, default `true` when
  absent).
- **Request identity** (existing): the validated copy of the token record
  attached to each HTTP request and read by the guard and by the handlers.
  This feature adds `operator_tools` to the copied scalar fields.
- **Exposure registry entry** (existing): canonical name, aliases, audience,
  minimum permission, operation, space-scope argument. The guard reads
  `audience` through the lookup that maps every registered name (canonical
  and alias) to its entry.
- **Operator tool refusal**: the result returned by the guard, `status`
  `forbidden` and a fixed `message`; its cause is `operator_tools_refused`
  (or `audience_unresolved` for the fail-closed case).
- **Audit entry**: the JSON line written for each refusal (FR-020).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every one of the 36 `audience=operator` registry entries and
  every one of their aliases, a call over `/mcp` with a token whose attribute
  is `false` is refused with the same payload, and no handler runs; measured
  by a parametrized test over the registry.
- **SC-002**: 100% of the existing test suite passes without changing any
  assertion on `status` or `message`, and every token record created before
  the feature behaves exactly as before (no refusal).
- **SC-003**: The six CLI operator commands that use `/mcp` (`space update`,
  `space export`, `backup create`, `backup download`, `token *`, `gc`) reach
  their handlers with a token whose attribute is `true`; measured with the
  CLI client against the production application.
- **SC-004**: Every refusal produces exactly one audit entry; measured by
  counting `operator_tool_refused` entries in a captured audit log.
- **SC-005**: An operator can turn the attribute on or off for one token or a
  selection of tokens in one call, and the next MCP call by that token uses
  the new value, measured without reconnecting the session.
- **SC-006**: `scripts/check_doc_links.py` passes and `git diff --check`
  reports nothing.

## Assumptions

Each item below is a decision taken in place of a clarification question.

- Attribute name `operator_tools`, boolean, with `true` meaning "may call
  operator tools over `/mcp` and `/api/tool`". Chosen because it names the
  registry audience it controls and fits the record's existing short names
  (`revoked`, `space_ids`).
- Records without the attribute are read as `true`. Reason: the feature
  description requires that upgrading breaks nothing and that existing tokens
  keep today's behaviour.
- Agent path versus operator path. `token_create` is the agent path: it is the
  only provisioning tool advertised in discovery (`manage` and `admin`
  profiles), it is the tool the CLI and the console use when the caller is a
  manager, and `docs/DEPLOYMENT.md:154-160` uses it for "routine agent work".
  `admin_create_token` is an operator path: it is admin-only, hidden from
  discovery, and `docs/DEPLOYMENT.md:123-146` uses it to mint operator
  credentials. The bootstrap key is an operator credential with no record.
  Therefore `token_create` always refuses and `admin_create_token` allows
  unless told otherwise.
- `token_create` takes no `operator_tools` parameter. Reason: a `manage` token
  that is refused operator tools could otherwise create a token that is
  allowed them, which would void the guard.
- The guard also covers `POST /api/tool`. Reason: that route accepts the
  bearer token in the `Authorization` header (`src/live_mem/auth/middleware.py:223-250`)
  and calls handlers in-process (`src/live_mem/tools/__init__.py:83-134`), so
  a guard on `/mcp` alone could be bypassed by an agent that posts to
  `/api/tool`. Operator tokens are allowed, so the console keeps working.
- The refusal payload keeps today's shape (`status`, `message`) and adds no
  field. Reason: the feature description asks for today's convention; spec
  001 owns the structured fields and the cause name is reserved for it.
- The refusal is audited on the `live_mem.audit` logger only, not in the
  admin console's in-memory audit ring. Reason: the ring is documented as
  "console and auth events only" (`src/live_mem/core/audit_ring.py:32-38`,
  `docs/SECURITY.md` 3.10) and its captured-event set is closed.
- The attribute applies regardless of the permission level, including to
  `admin` tokens. Reason: a single rule is easier to audit; an admin token
  that must call operator tools over `/mcp` is created with the default
  (`true`).
- The admin tool parameter is a text value (`"true"`, `"false"`, empty for no
  change) on `admin_update_token` and `admin_bulk_update_tokens`, matching
  their existing "empty leaves it unchanged" convention for `permissions` and
  `email`; `admin_create_token` takes a boolean with default `true`.
- The console's create form offers the choice only in admin mode (the only
  mode that reaches `admin_create_token`); the edit form offers it to admins.
- Spec 001 is not implemented yet, so no `error_kind` field is emitted now.
- With no identity on the request, the guard does not decide and the
  handler's own authentication check refuses the call. Reason: the guard reads
  a token attribute and there is none to read; every registered handler
  refuses a missing identity today, and two existing tests pin that result
  (`tests/test_mcp_tool_surface.py:1110-1135`,
  `tests/test_admin_console_security.py:1066-1130`); refusing in the guard
  would have changed pinned results without granting anything.
