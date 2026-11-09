# Research: how operator tools are exposed, called and authorized in Hivemind today

**Summary**: Hidden operator tools are reachable by exact name over `/mcp`
and through `POST /api/tool`, and the only call-time control is the
permission level inside each handler. The bearer token record has no field
that distinguishes an operator's credential from an agent's. The CLI calls
operator tools over `/mcp`, and the admin console calls handlers in-process
through `/api/tool`. What is needed is one attribute on the token record, read
in one central place on both call paths, keyed on the registry's audience.
Not decided here: the structured refusal shape that spec 001 defines.

All line references are to Hivemind 1.6.1 (commit ff79faf) with the MCP SDK 2
fixes that this specification builds on.

## What stops an agent today

- The exposure registry classifies each of the 60 canonical tools as
  `agent_core` or `operator` and lists its aliases
  (`src/live_mem/tools/exposure.py:47-57,92-439`). 36 entries are
  `operator` (`_FROZEN_OPERATOR_MINIMUMS`, `exposure.py:535-572`), with
  minimum permissions from `read` (`space_export`, `backup_list`) to `admin`
  (`admin_*`).
- `tools/list` returns only `agent_core` names
  (`HivemindMCPServer.list_tools`, `exposure.py:957-982`). The registry is
  "an exposure and rendering contract, not an authorization source"
  (`docs/TOOL_EXPOSURE.md:5-6`), and hidden tools "remain registered and
  callable by exact name, subject to their handler authorization"
  (`docs/TOOL_EXPOSURE.md:17-19`; `docs/MCP_TOOLS_SPEC.md:65-86`).
- Each handler calls `check_access` and one of `check_write_permission`,
  `check_manage_permission` or `check_admin_permission`
  (`src/live_mem/auth/context.py:312-413`); for example `bank_write` checks
  access then `manage` (`src/live_mem/tools/bank.py:1073-1079`). A `manage`
  token therefore passes every operator tool whose minimum is `manage` or
  lower: `mid_write`, `mid_delete`, `bank_compact`, `bank_repair`,
  `space_update`, `space_export`, `space_delete`, `backup_*`, `long_reindex`,
  `inference_self_test` and the rest of the `operator` list.
- The token record has `hash`, `name`, `email`, `permissions`, `space_ids`,
  `created_at`, `expires_at`, `last_used_at`, `revoked`
  (`src/live_mem/core/models.py:259-278`). Nothing records whether the token
  belongs to an operator or an agent.

## How a tool call reaches its handler

Two routes exist and they do not share code:

- `/mcp` (agents, the CLI, `hivemind-ingest`): the MCP SDK's low-level
  `tools/call` handler builds a `Context` and calls `MCPServer.call_tool`,
  which asks the tool manager to run the tool (SDK 2.3.0,
  `MCPServer._handle_call_tool` and `MCPServer.call_tool`). Hivemind
  subclasses `MCPServer` as `HivemindMCPServer` to override `list_tools`
  (`exposure.py:957-982`); `call_tool` is not overridden. The per-request
  identity is bound by the SDK middleware `bind_mcp_request_identity`
  (`src/live_mem/auth/context.py:138-160`) from the ASGI request state that
  `AuthMiddleware` fills on every HTTP request
  (`src/live_mem/auth/middleware.py:204-220`). `get_effective_token_info`
  prefers that per-request identity over the session's contextvar
  (`context.py:212-245`), so a token change takes effect on the next HTTP
  request.
- `POST /api/tool` (admin console): `StaticFilesMiddleware._api_tool_call`
  requires `write`, reads the body, writes an `admin_tool_call` audit line and
  ring event, then calls `call_tool_direct`
  (`src/live_mem/auth/middleware.py:552-656`). `call_tool_direct` finds the
  tool in the SDK tool manager and calls its function in-process,
  "bypassing the MCP protocol layer" (`src/live_mem/tools/__init__.py:83-134`).
  It never goes through `MCPServer.call_tool`. The identity it uses is the
  contextvar set by `AuthMiddleware` plus the fresh store
  (`context.py:212-245`).
- `AuthMiddleware._extract_token` accepts the bearer token from the
  `Authorization` header first, then the console cookie, then the query string,
  on every path (`src/live_mem/auth/middleware.py:223-250`). An agent that
  holds a bearer token can therefore post to `/api/tool` with the header and
  reach the same handlers.

Finding for the feature description's question: `/api/tool` calls handlers
in-process and never passes through `/mcp`. A guard on
`HivemindMCPServer.call_tool` alone would leave `/api/tool` open to any
bearer token with `write`. The guard must be a shared function called from
both `HivemindMCPServer.call_tool` and `call_tool_direct`.

## How the SDK renders a handler result

- A handler returns a `dict`; the SDK converts it to a `CallToolResult` with
  one `TextContent` holding the JSON, through `FuncMetadata.convert_result`
  (SDK 2.3.0). The CLI reads `result.content[0].text` and parses it
  (`scripts/cli/client.py:53-122`). The admin console receives the dict as
  JSON from `/api/tool`.
- A refusal built by the guard can be returned as the same `CallToolResult`
  shape through the tool's own `fn_metadata.convert_result`, so clients see
  exactly what a handler refusal looks like today
  (`{"status": "forbidden", "message": ...}`, `docs/MCP_TOOLS_SPEC.md:108-121`).
- An unknown name raises `ToolError("Unknown tool: ...")` inside the tool
  manager; `_handle_call_tool` turns it into an `is_error` text result.
  `call_tool_direct` returns `{"status": "error", "message": "Unknown tool: <name>"}`
  (`src/live_mem/tools/__init__.py:100-101`).

## Where tokens are created and changed

- `token_create` (`src/live_mem/tools/access.py:21-75`) is `agent_core`,
  discovered at `manage` and `admin` (`exposure.py:470-482`), requires
  `manage`, and calls `TokenService.create_delegated_token`
  (`src/live_mem/core/tokens.py:351-500`), which accepts only the three
  non-admin profiles and starts with `space_ids: []`.
  `docs/DEPLOYMENT.md:154-166` uses it "for routine agent work".
- `admin_create_token` (`src/live_mem/tools/admin.py:41-123`) is `operator`,
  requires `admin`, and calls `TokenService.create_token`
  (`tokens.py:608-820`). `docs/DEPLOYMENT.md:123-146` uses it with the
  bootstrap key to mint the first admin token.
- `admin_update_token` (`admin.py:383-491`) and `admin_bulk_update_tokens`
  (`admin.py:493-629`) require `admin` and call `TokenService.update_token`
  (`tokens.py:1234-1443`) and `bulk_update_tokens` (`tokens.py:1445-1738`).
  Both take `permissions` and `email` as text where "empty leaves it
  unchanged", invalidate the fresh store when authorization changed
  (`tokens.py:1375-1377,1687-1688`), and set `mcp_reconnect_required` when the
  effective permission or scope changed (`tokens.py:104-129`).
- `admin_list_tokens` returns per token: hash, name, email, permissions,
  space_ids, created_at, expires_at, last_used_at, revoked
  (`tokens.py:938-1010`).
- `validate_token` returns `type`, `client_name`, `permissions`,
  `allowed_resources`, `token_hash` (`tokens.py:1790-1848`); the bootstrap key
  returns `type: "bootstrap"` with no hash
  (`src/live_mem/auth/middleware.py:256-296`). `request_token_info_snapshot`
  copies only the fields listed in `_REQUEST_TOKEN_SCALAR_FIELDS`
  (`context.py:39-46,87-115`), so a new field must be added there to reach
  the MCP request identity.
- The token store loader refuses a non-boolean `revoked`
  (`tokens.py:2029-2032`) and validates every record before each save
  (`tokens.py:2063-2071`).
- The CLI routes `token create` to `admin_create_token` for admin and
  bootstrap callers and to `token_create` for managers
  (`scripts/cli/commands.py:119-205`); `token update` and `token bulk-update`
  call `admin_update_token` and `admin_bulk_update_tokens`
  (`commands.py:1121-1172,1385-1410`; shell: `scripts/cli/shell.py:990-1016`).
- The admin console's Access view calls `admin_create_token` in admin mode and
  `token_create` otherwise (`src/live_mem/static/js/admin/views-access.js:884-885`),
  and `admin_update_token` from the edit form (`views-access.js:1713`).

## Which operator tools the CLI calls over `/mcp`

`scripts/cli/client.py:53-122` opens a Streamable HTTP session to `/mcp` for
every call. The commands that name operator tools: `space update`
(`commands.py:381`), `space export` (`commands.py:524`), `bank write`
(`commands.py:891`), `token update`, `token list`, `token bulk-update`,
`token revoke`, `token delete`, `token purge` (`commands.py:1168-1456`),
`backup create`, `backup list`, `backup restore`, `backup download`,
`backup delete` (`commands.py:1504-1554`) and `gc` (`admin_gc_notes`,
`commands.py:1721`). A server-wide refusal of operator tools over `/mcp`
would break all of them.

## How refusals are audited today

- `AuditMiddleware` writes one JSON line per HTTP request to the
  `live_mem.audit` logger with request id, method, path, status, latency,
  client name, auth type and permissions (`src/live_mem/middleware.py:550-619`).
  It does not know the tool name.
- `/api/tool` writes an `admin_tool_call` line with the tool name and
  argument keys (`auth/middleware.py:620-646`) and a ring event; the ring
  accepts only `auth_rejected`, `admin_tool_call`, `login_failed`,
  `login_success` (`src/live_mem/core/audit_ring.py:32-34`).
- `TokenService` writes `token_create` and `bulk_update_tokens` events on the
  same logger (`tokens.py:321-350,1740-1788`).

## Decisions

- Decision: one guard function, keyed on the registry audience, called from
  `HivemindMCPServer.call_tool` (new override) and from `call_tool_direct`.
  Rationale: both routes reach the same handlers; the registry lookup
  `exposure_by_registered_name` already maps aliases to their entry
  (`exposure.py:647-658`), so no name list is kept by hand. Alternatives
  considered: an SDK middleware on `HivemindMCPServer` (covers `/mcp` only and
  has no access to the tool manager's registered set without the server) and
  a check inside each handler (36 handlers, easy to miss one, contradicts
  "one central place").
- Decision: `operator_tools` boolean on `TokenInfo`, default `true`, copied
  into the request identity. Rationale: `revoked` is the model for a boolean
  record field; absent-means-allowed keeps existing stores valid without a
  migration.
- Decision: the refusal is rendered through the tool's own
  `fn_metadata.convert_result`. Rationale: the result is byte-for-byte the
  shape of a handler refusal, so the CLI, the console and `hivemind-ingest`
  need no change.
- Decision: audit on the `live_mem.audit` logger with a dedicated event name.
  Rationale: that logger is the audit log of record; the ring is console-only.
- Decision: with no identity, the guard does not decide; the handler's own
  authentication gate refuses. Rationale: the guard reads a token attribute
  and there is none to read; every registered handler refuses a missing
  identity today, which `tests/test_mcp_tool_surface.py:1110-1135` pins for
  `backup_list` through `call_tool_direct` and
  `tests/test_admin_console_security.py:1066-1130` relies on with a
  synthetic tool. Refusing in the guard would have changed those pinned
  results without granting anything.
