# Research: current tool guidance, errors and server instructions in Hivemind 1.6.1

**Summary**: Hivemind's tool descriptions are Python docstrings that say what a
tool does but not when to use it, what must happen first or which tool to call
instead. Errors are `{"status": "error", "message": "..."}` dicts with no
field that names the next step; three tools go further on their own. The MCP
server sends no `instructions` at initialize, so the memory-bank workflow
reaches agents only through client-side instruction files. What is needed is a
single error shape with a remediation field, descriptions that carry
preconditions and peer tools, and server-side instructions. Not decided at the
specify stage: whether soft errors travel as MCP `isError=true` or as a
successful result, and how much of the current error shape existing clients
require to stay unchanged. Both were decided on 2026-10-12 (spec.md,
"Clarifications"); the plan-stage findings below record the evidence for the
seven open research items of the plan.

All line references are to the worktree at commit ff79faf (Hivemind 1.6.1).

## How tools are declared

- Each category module exposes `register(mcp) -> int` and declares tools with
  `@mcp.tool()` (`src/live_mem/tools/__init__.py:27-57`). The tool name is the
  Python function name; there are no `name=` overrides
  (`docs/MCP_TOOLS_SPEC.md:50-53`).
- The description that an MCP client views is the function docstring, for
  example `live_note` (`src/live_mem/tools/live.py:61-80`), `space_create`
  (`src/live_mem/tools/space.py:71-90`), `bank_consolidate`
  (`src/live_mem/tools/bank.py:377-398`), `graph_push`
  (`src/live_mem/tools/graph.py:248-277`). Two tools pass an explicit
  `description=` to the decorator instead, for example `mid_archive_retry`
  (`src/live_mem/tools/bank.py:1461-1466`).
- Parameters are `Annotated[..., Field(description=...)]`
  (`src/live_mem/tools/live.py:45-60`). Read-only and idempotent hints are set
  through `ToolAnnotations` (`src/live_mem/tools/live.py:44,135`).
- The 13 tier aliases (`short_*`, `mid_*`, `long_*`) are re-registrations of
  the same function; the exposure validator rejects any alias whose
  description, parameters, annotations or title differ from the canonical tool
  (`src/live_mem/tools/exposure.py:916-954`). A description change therefore
  applies to both names at once.

## How tools are exposed and gated

- `TOOL_EXPOSURES` records, for each of 60 canonical names, its aliases,
  audience (`agent_core` or `operator`), minimum permission, operation (`read`
  or `mutation`) and the argument that names the space
  (`src/live_mem/tools/exposure.py:47-57,92-439`).
- `tools/list` returns only `agent_core` names at or below the token's
  effective permission: 17 for `read`, 20 for `write`, 24 for `manage` and
  `admin` (`src/live_mem/tools/exposure.py:685-706,966-982`;
  `docs/TOOL_EXPOSURE.md:10-15`). Hidden operator tools stay callable by exact
  name (`docs/TOOL_EXPOSURE.md:17-19`).
- The serialized discovery response must stay within a 64 KiB budget
  (`src/live_mem/tools/exposure.py:442`; asserted in
  `tests/test_p10_tool_exposure.py:279`). Longer descriptions consume this
  budget.
- Authorization happens inside each handler, in this order: `check_access`
  (space id format, reserved namespace, allowlist, admin bypass), then
  `check_write_permission` / `check_manage_permission` /
  `check_admin_permission` (`src/live_mem/auth/context.py:256-413`; example
  call order in `src/live_mem/tools/live.py:91-97`).

## What the descriptions say today, and what they do not say

Read as an agent that must choose a tool, the current descriptions state the
purpose and the arguments but, with the exceptions listed below, do not state:

- which tool to call before this one (for example that `space_rules` and
  `mid_read_all` precede work, `docs/AGENT_MEMORY_SETUP.md:110-121`);
- which permission the tool needs, although the registry knows it
  (`src/live_mem/tools/exposure.py:92-243`);
- which tool to use instead when the call does not fit (for example `mid_write`
  for direct bank writes, which agents must not use,
  `RULES/live-mem.standard.memory.bank.md:134`).

Exceptions that already carry guidance:

- `bank_consolidate`: "Return control to the user; query
  `bank_consolidation_status` only when the user explicitly asks for status"
  (`src/live_mem/tools/bank.py:394-397`).
- `graph_connect`: "Without an explicit binding, the first non-empty
  `long_push` automatically binds the configured embedded long-memory runtime"
  (`src/live_mem/tools/graph.py:179-181`).
- `space_create` and `space_rules` name `space_update_rules` as the tool that
  changes rules later (`src/live_mem/tools/space.py:75-77,323-325`).
- `long_query`: "Long-term memory is a derived index and is never an authority
  for Hivemind commit state" (`src/live_mem/tools/graph.py:456-457`).

The contract test requires every tool and every parameter to have a non-empty
description that passes the public-safety check
(`tests/test_mcp_tool_surface.py:581-603`), and a mutation test proves that a
description change on `short_note` is detected by the alias parity check
(`tests/test_mcp_tool_surface.py:708-717`).

## How errors are returned today

Documented contract: every tool returns a `dict` with a `status` field whose
error form is `{"status": "error", "message": "..."}`; other documented
statuses are `created`, `deleted`, `not_found`, `forbidden`, `queued` and
`partial` (`docs/MCP_TOOLS_SPEC.md:108-121`).

Observed shapes:

- Guard failures return the dict built by the check helper: "Authentication
  required", "Invalid space identifier: '...'", "Access denied to space '...'",
  "The 'write' permission is required for this operation", and the `manage`
  and `admin` equivalents (`src/live_mem/auth/context.py:280,284-287,303-307,
  349-358,377-386,403-412`). The dict is returned as the tool result, not
  raised (`src/live_mem/tools/live.py:91-97`).
- Argument validation inside handlers returns the same two-key dict with a
  tool-specific message, for example "Filename is required"
  (`src/live_mem/tools/bank.py:125-139`), "backup_id is required"
  (`src/live_mem/tools/backup.py:53`), "archive must be a boolean"
  (`src/live_mem/tools/graph.py:934`), "The 'rules' parameter is required. No
  default rules file is configured" (`src/live_mem/tools/space.py:125-131`).
- Unexpected exceptions go through `safe_error`, which logs the traceback and
  returns the exception text only when `MCP_SERVER_DEBUG` is true; otherwise
  it returns "Internal server error" (`src/live_mem/auth/context.py:415-438`).
  A write to a shared (staged) space raises `StagedWriteNotImplemented`
  (`src/live_mem/core/write_sink.py:100-125`), which reaches the agent through
  `safe_error` (`src/live_mem/tools/live.py:118-134`); outside debug mode the
  agent views only "Internal server error" and cannot tell that the space is
  shared.
- The `/api/tool` proxy used by the admin console returns the same dict shapes
  and routes exceptions through `safe_error`
  (`src/live_mem/tools/__init__.py:98-137`).
- No tool raises an MCP tool error on purpose; no `isError` handling exists in
  `src/` (search for `isError` and `is_error` in `src/` finds only an internal
  consolidator variable, `src/live_mem/core/consolidator.py:4987`).

Richer error payloads that exist today, each specific to one tool:

- `bank_compact` returns `failure_reason`, `failed_phase`,
  `rollback_outcome`, `failures` and a free-text `remediation`
  (`src/live_mem/tools/bank.py:44-80,1412-1460`). Its remediation texts tell
  the agent what not to do ("do not use a DirectLocal fallback") rather than
  the corrective call.
- Consolidation acknowledgements carry
  `next_action="return_to_user_without_polling"` and a `polling` block with
  `recommended`, `mode`, `status_tool` and `instruction`
  (`src/live_mem/core/consolidation_queue.py:35-40,499-500`;
  `docs/MCP_TOOLS_SPEC.md:819-820`).
- Token re-scope responses add `mcp_reconnect_required=true` so a client can
  refresh cached discovery (`src/live_mem/core/tokens.py:604,1397,1735`;
  `docs/MCP_TOOLS_SPEC.md:79-81`).
- Project Mesh pairing evidence carries a `next_action` enumerated value
  (`resync`, `evict`, `resume`) (`src/live_mem/mesh/pairing_state.py:1393-1404`).

## What existing clients expect from an error

- Hivemind CLI (`scripts/cli`): parses the first text content block as JSON
  and, on transport failure, builds `{"status": "error", "message": "MCP
  error: ..."}` itself (`scripts/cli/client.py:100-120`). Errors are shown as
  the `message` string (`scripts/cli/display.py:25-27`); some views read
  per-item `error` keys (`scripts/cli/display.py:633,664,872-873`).
- Admin console (`src/live_mem/static/js/admin-api.js`): expects a JSON body
  with `status` and `message`; it synthesizes `{status: 'error', message}` on
  401, 429, empty or invalid responses (`admin-api.js:17-18,92-103,161-192`)
  and matches the word "write" inside `message` on HTTP 403
  (`admin-api.js:116`).
- `hivemind-ingest` (Go): maps an MCP result with `isError: true` to
  `{"status": "error", "isError": true, "message": ...}` and otherwise parses
  the text block as JSON (`tools/hivemind-ingest/internal/mcpclient/
  client.go:410-448`; tests at `client_test.go:174-207`). It is the only
  client that already understands `isError`.
- Tests: 56 files under `tests/` assert on `"status": "error"` payloads
  (count from a search for the literal); adding keys does not break them,
  changing `status` or `message` would.

## What the server instructions say today

- The server is constructed as `HivemindMCPServer(name=..., version=...,
  lifespan=..., middleware=[...])` with no `instructions` argument
  (`src/live_mem/server.py:249-254`). The installed MCP SDK (version 2.3.0,
  `uv.lock:714-715`) accepts `instructions: str | None` on `MCPServer.__init__`.
  No `instructions` string exists anywhere in `src/live_mem` apart from
  consolidator prompt text (`src/live_mem/core/consolidator.py:2795-2800`).
- The workflow an agent must follow is therefore delivered only client-side:
  the recommended project instruction block (`docs/AGENT_MEMORY_SETUP.md:
  89-166`), the Claude Code template (`CLAUDE_CODE_INTEGRATION.md:315-410`),
  the Codex guide, and the per-space rules returned by `space_rules`
  (`RULES/live-mem.standard.memory.bank.md:117-143`).
- The rules that these sources require an agent to follow:
  - at task start call `space_rules`, `mid_read_all`, `short_read`, and stop
    before any mutation if one fails (`docs/AGENT_MEMORY_SETUP.md:110-121`);
  - during work write atomic `short_note` entries in one of seven categories,
    never pass an agent identity (`docs/AGENT_MEMORY_SETUP.md:123-130`;
    `RULES/live-mem.standard.memory.bank.md:126-134`);
  - never write the bank directly (`RULES/live-mem.standard.memory.bank.md:134`);
  - do not run `long_push` or ingest as routine session-end work
    (`docs/AGENT_MEMORY_SETUP.md:135-138`);
  - at the end, confirm user validation, call `mid_consolidate` at most once,
    return without polling (`docs/AGENT_MEMORY_SETUP.md:140-152`);
  - long memory is non-authoritative; repository files win
    (`docs/AGENT_MEMORY_SETUP.md:105-108`);
  - never put tokens or secrets in notes, instructions or long memory
    (`docs/AGENT_MEMORY_SETUP.md:154-156`).
- The permission model an agent should know: `admin ⊃ manage ⊃ write ⊃ read`
  (`docs/MCP_TOOLS_SPEC.md:125-130`); a fresh non-admin token has access to no
  space until a manager invites it (`src/live_mem/auth/context.py:296-306`);
  `manage` is a provisioning role and routine agents should hold `read,write`
  (`SECURITY.md:109-114`).

## Prior art in infrahub-mcp (read-only reference)

The Infrahub MCP server attaches a remediation hint to every tool error:

- `_log_and_raise_error(ctx, error, remediation)` logs the error, appends
  `"\n\nRemediation: <text>"` to the message and raises `ToolError`, so the
  client receives an MCP error result with the hint in its text
  (`/home/user/infrahub-mcp/src/infrahub_mcp/utils.py:451-457`).
- Each call site names the corrective call, for example "Make at least one
  write (node_upsert / node_delete) before proposing changes" when a required
  prior step is missing (`tools/write.py:380-386`), "Call get_schema() for
  details on any kind" when a kind is unknown (`tools/nodes.py:242`), and
  "Verify the node references with get_nodes or search_nodes" when a lookup
  fails (`tools/traversal.py:20-37`).
- Branch validation lists the allowed alternatives ("Use the session branch or
  a feature branch; merge via propose_changes") (`utils.py:400-440`).
- An oversized response is turned into an error with instructions to narrow
  the query instead of being truncated silently, "keeping the agent in its
  self-correction loop" (`middleware.py:545-592`).
- The infrahub-mcp server passes no `instructions` to its server either (a
  search for `instructions` in `/home/user/infrahub-mcp/src/infrahub_mcp/*.py`
  returns nothing), so it is not prior art for that part.

What this specification borrows: the rule that every refused call names the
corrective call and the missing prerequisite in the agent's own vocabulary
(tool names and argument names). What it changes: infrahub-mcp keeps the hint
as free text inside an error message; Hivemind's clients and tests consume
dict payloads, so the hint is specified as structured fields next to `status`
and `message`.

## Gaps this specification must close

1. No field in any error says which tool to call next, which argument is
   missing, or which prior step was skipped; `bank_compact` and the
   consolidation acknowledgement are the only structured exceptions.
2. A shared-space write refusal is invisible outside debug mode.
3. Descriptions do not state preconditions, required permission, peer tools or
   when not to use the tool; the registry holds the permission and the space
   argument but the description does not use them.
4. The server sends no instructions at initialize, so an agent whose client
   lacks the project instruction block receives no workflow at all.
5. Three existing clients and 56 test files depend on `status` and `message`;
   any new shape must keep both.

## Plan-stage research (2026-10-12)

Line references below are to the worktree at commit 3b2db17. `git diff --stat
ff79faf..HEAD -- src scripts tools docs RULES` is empty, so the references in
the sections above remain valid. SDK references are to the installed `mcp`
2.3.0 package in `.venv/lib/python3.13/site-packages/mcp/`.

### Item 1. What each client does with an MCP error result (FR-029)

What a client receives after this feature: a `CallToolResult` whose
`isError` is `true`, whose `content[0]` is a text item holding the Guidance
error payload as JSON, and whose `structuredContent` holds the same payload.

Hivemind CLI (`scripts/cli`):

- `MCPClient.call_tool` reads `result.content[0].text` and parses it as JSON
  without reading `isError` (`scripts/cli/client.py:99-106`). The Python MCP
  client does not raise on an error result: `ClientSession.call_tool` only
  validates results whose `is_error` is false
  (`mcp/client/session.py:1101`).
- `_run_tool` treats a `status` outside its success list as a failure and
  shows `result.get("message", ...)` (`scripts/cli/commands.py:80-108`);
  `space status` shows `info.get("message", "Space unavailable")`
  (`scripts/cli/commands.py:445-447`).
- Decision: no change is needed for the CLI to show `message` as today. The
  CLI ignores `structuredContent`. An optional later change can show
  `remediation.reason` and `remediation.next_tool` under the message.

Admin console (`/api/tool` proxy):

- The proxy handler calls `call_tool_direct` and sends the dict as JSON with
  HTTP 200 (`src/live_mem/auth/middleware.py:645-648`; `_send_json` default
  status at `src/live_mem/auth/middleware.py:1042`).
- `call_tool_direct` looks the tool up in `_mcp_ref._tool_manager._tools`
  and calls the handler function directly (`tool_obj.fn`), outside the SDK
  call path (`src/live_mem/tools/__init__.py:98-126`). A handler result
  that is not a dict is wrapped as `{"status": "ok", "data": result}`
  (`src/live_mem/tools/__init__.py:126`).
- The console's HTTP 403 "write" match (`admin-api.js:116`) applies to the
  proxy's own `check_write_permission` refusal sent with status 403
  (`src/live_mem/auth/middleware.py:574-577`), not to a tool result.
- Decision: no change is needed for the console to show `message`, on one
  condition: handlers must keep returning dicts, and the conversion to a
  `CallToolResult` must happen in the MCP call path, not inside the
  handlers. If a handler returned a `CallToolResult` object, the proxy would
  wrap it at `src/live_mem/tools/__init__.py:126` and the console would lose
  `status` and `message`. The proxy therefore returns the Guidance error
  payload unchanged (FR-011) with no code change, and the `isError` flag
  never reaches the console because the console does not use MCP.

`hivemind-ingest` (Go):

- `ToolCallResult` has `content` and `isError` only
  (`tools/hivemind-ingest/internal/mcpclient/client.go:60-63`);
  `structuredContent` is not read.
- With `isError: true` and text that parses as JSON, the client returns the
  parsed map with `status` overwritten to `"error"` and `isError` set to
  `true` (`client.go:420-428`), so `message` and every other payload field
  survive. The engine treats `status == "error"` or `isError == true` as a
  failure (`internal/ingest/engine.go:408`) and prints `message`
  (`engine.go:456,460,491,645`).
- Decision: no change is needed. One side effect is recorded: a payload
  whose `status` is `forbidden` would be shown as `error` by this client
  (`client.go:425`). No code in `src/live_mem` returns `"status":
  "forbidden"` today (a search finds only Mesh error codes such as
  `pair_replay_forbidden`), so no displayed value changes.
- The existing test `TestClientCallToolIsError`
  (`client_test.go:174-192`) already covers the mapping.

Other readers of tool results:

- `GraphBridgeService` reads `content[0].text` the same way as the CLI
  (`src/live_mem/core/graph_bridge.py:648-654`). It is a client of a Graph
  Memory server, not of Hivemind tools; `tests/test_mcp2_transport.py:157-170`
  points it at Hivemind only to test the transport. No change.
- Tests reach handlers in three ways; see Item 2.

### Item 2. How tools are registered, how results are converted, and the single conversion point

Registration:

- `register_all_tools` registers eight category modules, then the 13 tier
  aliases, then validates the registry (`src/live_mem/tools/__init__.py:27-80`).
  61 `@mcp.tool` decorators exist in `src/live_mem/tools/*.py`; 62 handler
  signatures are annotated `-> dict` and none `-> dict[str, Any]`.
- A plain `dict` return annotation produces no output schema: the SDK's
  `_create_output_model` reaches the "other class types" branch, where
  `get_type_hints(dict)` is empty and the model stays `None`
  (`mcp/server/mcpserver/utilities/func_metadata.py:525-541`).
  `tests/fixtures/tool_surface.json` contains no `outputSchema` entry. No
  tool sends `structuredContent` today.

Conversion today:

- The SDK's `_handle_call_tool` calls `self.call_tool`
  (`mcp/server/mcpserver/server.py:441-446`); `MCPServer.call_tool` calls
  `self._tool_manager.call_tool(..., convert_result=True)`
  (`server.py:533-548`); `Tool.run` validates arguments, calls the handler
  and converts (`mcp/server/mcpserver/tools/base.py:132-192`).
- `FuncMetadata.convert_result` returns a `CallToolResult` unchanged (and
  validates `structured_content` against the output schema only when a
  schema exists and `is_error` is false, `func_metadata.py:205-208`); a dict
  becomes one text item with the JSON serialized with `indent=2`
  (`func_metadata.py:653`) and no structured content.
- A `ToolError` raised anywhere in the call becomes
  `CallToolResult(content=[TextContent(text=str(exc))], is_error=True)`
  (`server.py:456`); a tool name that is not registered takes this path.
- Argument validation against the input schema happens before the handler
  runs: a `ValidationError` becomes a `ToolError` whose text starts with
  "Error executing tool <name>" (`base.py:154-157`), so a missing required
  argument never reaches a handler and today's client views the SDK's text
  only. The SDK extracts field names from `exc.__cause__.errors()` for its
  log (`server.py:447-450`).

Decision, the single conversion point:

- Override `call_tool` on `HivemindMCPServer`
  (`src/live_mem/tools/exposure.py:957-982`), which already overrides
  `list_tools` for the same reason: `MCPServer.__init__` binds the method,
  so a class override is seen by the low-level handler. The override:
  - calls `self._tool_manager.call_tool(name, arguments, context,
    convert_result=False)` to receive the handler's dict;
  - when the dict's top-level `status` is `error` or `forbidden`, builds
    `CallToolResult(content=[TextContent(json)],
    structured_content=payload, is_error=settings.mcp_refusal_is_error)`;
  - otherwise passes the dict to `tool.fn_metadata.convert_result` so the
    text of successful results stays byte-identical (same `indent=2`);
  - catches a `ToolError` whose `__cause__` is a `ValidationError` and
    builds a Guidance error payload from the field names only
    (`error_kind` `missing_argument` or `invalid_argument`,
    `remediation.missing_arguments` from `err["loc"]`), never from the
    pydantic message text (FR-010);
  - re-raises every other `ToolError` and every `MCPError`, so the
    "Unknown tool" result and protocol errors are unchanged (FR-026).
- This covers every registered name: aliases share the handler function
  (`exposure.py:943-946`) and hidden operator tools are registered
  (`docs/TOOL_EXPOSURE.md:17-19`). No handler is edited for the delivery
  change.
- Rationale against alternatives:
  - An SDK `ServerMiddleware` (`mcp/server/context.py:146-178`) runs before
    parameter validation for every method, so it would see the `CallToolResult`
    as a `HandlerResult` dict after serialization and would have to parse
    the text item again; the override sees the handler's dict directly.
  - Editing the 61 handlers to return `CallToolResult` breaks the `/api/tool`
    proxy (Item 1) and the 20 test files that call `.fn` (below).
- Only the top-level `status` is read. Nested sub-results such as
  `results["s3"] = {"status": "error", ...}` inside `system_health`
  (`src/live_mem/tools/system.py:73,95`) are not refusals.
- `system_about` tests `isinstance(mcp, HivemindMCPServer)`
  (`src/live_mem/tools/system.py:152`); the surface tests build a plain
  `MCPServer` (`tests/test_mcp_tool_surface.py:470-473`). The replay test
  must build a `HivemindMCPServer`, as `tests/test_mcp2_transport.py:34-37`
  does, or the override is not exercised.

Interaction with `safe_error`:

- `safe_error` returns `{"status": "error", "message": "Internal server
  error"}` outside debug mode (`src/live_mem/auth/context.py:415-438`); 56
  call sites exist in `src/live_mem/tools/*.py`. Its dict flows through the
  conversion as a refusal. `safe_error` is the one place to set
  `error_kind="internal_failure"` and a fixed remediation.
- `short_note` raises `StagedWriteNotImplemented` for a shared space and
  lets `safe_error` render it (`src/live_mem/tools/live.py:118-134`), which
  is the FR-008 gap. `admin_gc_notes` already catches
  `StagedWriteNotImplemented`, `RegistryRefused` and `CorruptedStateError`
  before `safe_error` and returns a `reason` field
  (`src/live_mem/tools/admin.py:751-777`); `bank_compact` does the same with
  `failure_reason` (`src/live_mem/tools/bank.py:1412-1437`). Decision: add
  the same three `except` clauses to the write handlers that can raise them
  (`short_note` first), mapping to `error_kind="write_route_refused"`.

Interaction with the 56 test files that assert the error dict:

- 20 files reach handlers through `mcp._tool_manager._tools[name].fn(...)`
  (for example `tests/test_p13_qdrant_reindex.py:2759`,
  `tests/test_p13_inference_self_test.py:155`,
  `tests/test_p5_8_space_rules_guard.py:199`). They receive the handler's
  dict and never pass through `call_tool`.
- 34 files assert dicts returned by service classes or guard helpers
  (`BackupService`, `ConsolidatorService`, `TokenService`, `LiveService`,
  `check_access`), not by MCP.
- 2 files use an MCP client: `tests/test_mcp2_transport.py` parses
  `content[0].text` through the CLI and bridge clients;
  `tests/test_cli_surface_sync.py:123` defines a fake `call_tool`.
- Decision: FR-025 holds because the conversion is in `call_tool` and the
  payload keeps `status` and `message`; no assertion on either field needs
  an edit. The two MCP-client files keep passing because both clients
  parse the text item whatever `isError` says.

### Item 3. Server instructions: construction site, budget and consistency test

- `HivemindMCPServer(...)` is constructed without `instructions`
  (`src/live_mem/server.py:249-254`). `MCPServer.__init__` accepts
  `instructions: str | None` and passes it to the low-level server
  (`mcp/server/mcpserver/server.py:163,216`), which returns it in the
  initialize result (`mcp/server/lowlevel/server.py:562`). The transport test
  constructs its own `HivemindMCPServer` (`tests/test_mcp2_transport.py:34-37`).
- Decision: a new module `src/live_mem/instructions.py` holds the text as a
  tuple of rule records (identifier, sentence, source document) and a
  `render_instructions()` function; `HivemindMCPServer.__init__` passes the
  rendered text to `MCPServer.__init__` when the caller passes none, so
  every construction site (server, tests) carries the same instructions
  (FR-016). The record form lets the consistency test check each rule by
  identifier.
- Budget: the 64 KiB `DISCOVERY_SCHEMA_BUDGET_BYTES`
  (`src/live_mem/tools/exposure.py:442`, asserted at
  `tests/test_p10_tool_exposure.py:279`) measures the serialized
  `tools/list` response only. The initialize result is a separate message
  and is not counted. Decision: the contract test asserts a separate byte
  cap on the rendered instructions; the value is not in any source and is
  set at implementation after measuring the text (open question Q4). The
  first lines carry the start-of-task calls and the write discipline (Edge
  Cases).
- Consistency test design (FR-015, FR-028):
  - the test extracts the fenced block that follows the heading
    "## Recommended project instruction block" in
    `docs/AGENT_MEMORY_SETUP.md` (lines 89-166 today) and the "Recommended
    Agent Workflow" section of `RULES/live-mem.standard.memory.bank.md`
    (lines 117-143 today);
  - for each FR-013 rule record, it asserts that the rule's marker
    phrases (tool names and fixed words such as "at most once", "without
    polling", the seven category names) are present in the rendered
    instructions, and present in the document that is the rule's source;
  - it asserts shared facts are equal across the three texts: the seven
    categories (`src/live_mem/core/live.py:43`), the three start-of-task
    calls, the single `mid_consolidate` call, and the four permission
    levels;
  - it asserts the FR-013 order by comparing the positions of the rule
    markers in the rendered text;
  - FR-014: it asserts the rendered text contains no `http://`,
    `https://`, no `{HIVEMIND_MCP_SERVER}` or `{SPACE}` placeholder (the
    document block uses them, `docs/AGENT_MEMORY_SETUP.md:96-97`), and no
    string that matches the token format.
  - The standard rules file lists only `mid_read_all` at session start
    (`RULES/live-mem.standard.memory.bank.md:119-122`), while the document
    block lists three calls (`docs/AGENT_MEMORY_SETUP.md:110-117`). The test
    checks each source for the rules it states and does not require the
    rules file to list all three calls.

### Item 4. The closed `error_kind` set mapped to today's refusal sites

Refusal sites found by searching for `"status": "error"` in
`src/live_mem/tools/*.py` (53 sites), `src/live_mem/auth/context.py` (17
sites, 8 of them in docstrings) and the service modules that return dicts to
handlers. The mapping below is the decision for the closed set.

- `authentication_missing`: "Authentication required"
  (`src/live_mem/auth/context.py:280,349,377,403`;
  `src/live_mem/tools/backup.py:207,354`; `bank.py:583,712`;
  `space.py:108,267,503`; `system.py:238`).
- `token_scope_changed`: after an operator re-scopes or revokes a token,
  `_get_effective_token_info` returns `None` for the tombstoned hash
  (`src/live_mem/auth/context.py:237-238`), so the agent's next call views
  "Authentication required". The conversion point distinguishes this case
  by checking the tombstone set (`context.py:84,209`) and tells the agent to
  refresh its tool list, consistent with `mcp_reconnect_required` on the
  operator's response (`src/live_mem/core/tokens.py:604,1397,1735`).
- `invalid_space_id`: format check (`context.py:284-288`) and reserved
  namespace (`src/live_mem/core/memory_id.py:56-63`, which also sets
  `recovery_required`).
- `space_access_denied`: `context.py:303-307`.
- `permission_insufficient`: `context.py:355-358,383-386,409-412`;
  `bank.py:462-470` (consolidating another agent's notes without `manage`);
  `graph.py:579-588,868-878` (volatile files need `include_volatile=True`
  and `manage`).
- `missing_argument`: `backup.py:53`; `bank.py:125`; `space.py:124-131`;
  `graph.py:1167`; the "confirm=True is required" refusals
  (`admin.py:363-372`; `backup.py:317,420`; `bank.py:1209-1218`;
  `space.py:505-514`) with `remediation.arguments={"confirm": true}`; and
  the SDK input-schema validation failures caught at the conversion point
  (Item 2).
- `invalid_argument`: `backup.py:63,74,83`; `bank.py:129,134,139,1102`;
  `graph.py:213,857,930,934,996,1001,1046,1102,1107,1161,1173,1178`;
  `admin.py:713`; the note category check
  (`src/live_mem/core/live.py:99-106`).
- `required_prior_step_missing`: no refusal site in 1.6.1 returns this
  cause. Decided: the value stays in the closed set, documented as having
  no producer yet, and the replay test excludes it until a tool produces
  it.
- `operation_in_progress`: the consolidation queue returns the existing
  job's payload instead of an error (`src/live_mem/core/consolidation_queue.py:164-166`,
  FR-007). `bank_compact` returns `"status": "conflict"` while a
  consolidation lock is held (`src/live_mem/tools/bank.py:1365-1372`), and
  `admin_gc_notes` does the same (`src/live_mem/core/gc.py:420,679,700,722`).
  `conflict` is not in the documented status list
  (`docs/MCP_TOOLS_SPEC.md:108-121`) and is not `error` or `forbidden`.
  Decided: `conflict` stays a successful result (not `isError`) that
  carries `error_kind="operation_in_progress"` and a `remediation` with
  `retryable=true`; `conflict` is added to the documented statuses in
  `docs/MCP_TOOLS_SPEC.md`.
- `write_route_refused`: `StagedWriteNotImplemented` from `short_note`
  (`src/live_mem/tools/live.py:118-134`); `admin.py:751-777` (`reason`
  values `route_staged_not_implemented`, `route_refused`, `state_corrupt`);
  `bank.py:1412-1437` (`failure_reason` values `direct_local_route_required`,
  `hivemind_state_corrupt`).
- `internal_failure`: `safe_error` (`context.py:415-438`); the queue's
  crash result (`consolidation_queue.py:432-434`); the proxy-only results
  "Server not initialized", "Unknown tool", "internal error"
  (`src/live_mem/tools/__init__.py:99,105,122`).
- `operator_tools_refused` and `audience_unresolved`: reserved for
  specification 002 (branch `spec/002-operator-tool-guard`, commit
  e01154b). That branch defines the cause constants
  `OPERATOR_TOOLS_REFUSED = "operator_tools_refused"` and
  `AUDIENCE_UNRESOLVED = "audience_unresolved"` (the second for a
  registered tool with no registry entry) and the refusal
  `{"status": "forbidden", "message": "This tool is reserved for operators.
  Ask an operator to perform the action through the admin console or the
  CLI with an operator token."}` (its `src/live_mem/tools/exposure.py:973-983`).
  That refusal names no corrective call; `remediation.retryable` is false.
  This feature reserves both values and does not produce them.
- `unclassified_refusal`: assigned by the conversion point to a refusal
  whose dict carries no `error_kind`. It exists so that drift is visible
  (in the Prometheus counter) and testable (the replay test fails on it),
  instead of a silent wrong value.

Existing field names that overlap: `reason` (`admin.py:753`),
`failure_reason` (`bank.py:63`), free-text `remediation` (`bank.py:72`),
`recovery_required` (`memory_id.py:57`), `rejected_volatile`
(`graph.py:878`). They stay as they are (additive rule, FR-027), with one
exception decided on 2026-10-12: `bank_compact`'s top-level string
`remediation` (`bank.py:72`) moves into `remediation.reason`, so every tool
has the same shape. The CLI reads that string in
`show_bank_compact_failure` (`scripts/cli/display.py:707`, printed at
`display.py:778`); the same pull request changes it to read
`remediation.reason`.

### Item 5. Audit and metrics integration points

- `AuditMiddleware` is an ASGI middleware. It records method, path, HTTP
  status, latency and the token identity from `current_token_info` in a
  `finally` block (`src/live_mem/middleware.py:444-513`). It does not know
  the tool name or the result.
- `MetricsMiddleware` keeps counters in dicts keyed by a bounded path label
  and renders them in Prometheus exposition format in `_serve_metrics`
  (`src/live_mem/middleware.py:75-253`; counters at 86-93, rendering at
  198-235).
- The SDK runs the tool call outside the ASGI request's task, so a
  `ContextVar` set in the handler is not visible in the middleware's
  `finally`. The identity binding solved this by reading
  `scope["state"]`, which the SDK copies into the request context and
  which "follows the current call" (`src/live_mem/auth/context.py:33-37,
  118-130`; `bind_mcp_request_identity` at `context.py:138-148`).
  `AuthMiddleware` creates that state dict (`src/live_mem/auth/middleware.py:211-213`).
- Decision: the conversion point writes one entry
  `scope["state"]["live_mem.tool_outcome"] = {"tool": <canonical name>,
  "error_kind": <value>}` through `context.request_context.request.scope`
  (`mcp/server/mcpserver/context.py:102`; `mcp/server/context.py:47`).
  `AuditMiddleware` copies `tool` and `error_kind` from that entry into its
  audit record; `MetricsMiddleware` increments
  `_refusal_count[(tool, error_kind)]` from the same entry and renders
  `livemem_tool_refusals_total{tool="...",error_kind="..."}`. The `/api/tool`
  proxy writes the same entry after `call_tool_direct` returns, so console
  refusals are counted too (FR-030, "every refused call").
- Bounded labels: `tool` is the canonical name resolved through
  `TOOL_EXPOSURES` (alias to canonical), with a fixed `unknown` label for a
  name outside the registry; `error_kind` comes from the closed set.
- Risk R1 records that the state dict must be the same object in both
  tasks; the first test of the feature proves it.

### Item 6. The startup setting for the rollout (FR-031)

- `Settings` is a pydantic-settings class with a flat list of fields and an
  `_validate_config` model validator that collects errors and fails at
  startup (`src/live_mem/config.py:81-92,298-440`); `get_settings()` is an
  `lru_cache` singleton (`config.py:443-446`). Boolean fields such as
  `mcp_server_debug: bool = False` (`config.py:92`) and `mid_auto_compact:
  bool = True` (`config.py:245`) are parsed by pydantic from the environment.
- Decision: `mcp_refusal_is_error: bool = True`, placed under the "Serveur
  MCP" block after `mcp_server_debug` (`config.py:88-92`), read from the
  environment variable `MCP_REFUSAL_IS_ERROR`. Pydantic's boolean parsing is
  the validation; `_validate_config` logs one warning at startup when the
  value is `false`, naming the setting as temporary. The name is this plan's
  proposal; no source names it.
- Documentation sites: `.env.example:10-17` (the MCP server block),
  `CHANGELOG.md` "[Unreleased]" (`CHANGELOG.md:17-19`), and
  `docs/DEPLOYMENT.md`, which today has no environment-variable table (a
  search for `MCP_SERVER_DEBUG` in `docs/*.md` returns nothing); the
  setting is documented under a new subsection of "Production"
  (`docs/DEPLOYMENT.md:184`). Which release removes the setting is not
  decided (FR-031).

### Item 7. Test strategy under constitution principle VI

- Markers come from the file path (`tests/test_quality_policy.py:41-81,
  152-159`; `docs/TESTING.md:30-43`): `tests/test_*surface*.py` and
  `tests/test_*contract*.py` are `contract`; `tests/test_*integration*.py`
  is `integration`; other paths are `unit`. `slow` and `optional` are
  orthogonal markers (`pyproject.toml:71-81`). The existing opt-in pattern
  skips unless an environment variable is set
  (`tests/test_hivemind_protocol_model.py:350-358`). The nominal suite runs
  without production credentials (`CONTRIBUTING.md:52-55`).
- Decision, test files:
  - `tests/test_tool_guidance_replay_integration.py` (`integration`): builds
    a `HivemindMCPServer` with the real registry and a bound identity, replays
    at least 20 wrong calls through `call_tool`, asserts `is_error`, the
    text and `structuredContent` equality, every `error_kind` at least once,
    `unclassified_refusal` never, and that each `next_tool` the token may
    call succeeds (SC-001, SC-002). With `MCP_REFUSAL_IS_ERROR=false` it
    asserts `is_error` is false and the payload is unchanged.
  - `tests/test_tool_guidance_surface.py` (`contract`): description
    contract per `agent_core` tool and parameter, permission equals the
    registry minimum, alias description identical and naming the canonical
    tool, hidden operator tools name their agent tool, discovery budget per
    profile, instructions contain each FR-013 rule in order, consistency
    with the two documents, no deployment value, `docs/MCP_TOOLS_SPEC.md`
    lists every `error_kind` with an example, `tool_surface.json` unchanged.
  - `tests/test_tool_guidance_observability.py` (`unit`): audit entry
    carries `tool` and `error_kind` and nothing else new; the counter
    increments by canonical tool and kind; the state entry written in the
    SDK task is read by the ASGI middleware (risk R1).
  - `tests/test_tool_guidance_agent_evaluation.py` (`unit` by path, plus
    `optional`): skips unless `HIVEMIND_AGENT_EVAL=1` and the model
    credentials variables are set; runs the SC-004 scenario ten times
    against an in-process server and records the model and each run's
    result to a JSON file the pull request quotes. It is excluded from the
    nominal suite by the skip, not by a marker expression, so a plain
    `uv run pytest tests/ -q` stays credential-free.
- Failing-then-passing order: each test is written against 1.6.1 first and
  must fail (no `error_kind`, `is_error` false, no instructions, description
  sections missing), then pass with the change. Mutation proofs: set
  `is_error` to false in the conversion and show the replay fails; remove
  one rule record and show the consistency test fails; drift one alias
  description and show the existing parity guard fails
  (`tests/test_mcp_tool_surface.py:706-717`).

### Risks

- R1. The audit and metric integration assumes `scope["state"]` is the
  same dict object in the SDK's request context and in the ASGI
  middleware's `finally`. `src/live_mem/auth/context.py:33-37` says the SDK
  copies the Starlette request including `scope["state"]`; whether the copy
  is shallow is not stated in Hivemind's sources. If it is not shared, the
  fallback is for the conversion point to write the audit entry and the
  counter itself, which changes where FR-030's entry is written.
- R2. The `/api/tool` proxy and 20 test files call handler functions
  directly, so no refusal passes the conversion there; a handler that omits
  `error_kind` is only detected by the replay test and the
  `unclassified_refusal` counter.
- R3. Richer descriptions consume the 64 KiB discovery budget; the current
  usage per profile is not measured in this plan. If the `manage` profile
  cannot fit, the budget change is a separate decision (spec Assumptions).
- R4. SDK input-schema failures are converted from pydantic error locations;
  a pydantic version change could alter `err["loc"]` shapes. The replay test
  covers one missing and one mistyped argument.
- R5. `hivemind-ingest` overwrites `status` with `error` on an error result
  (`client.go:425`). Today no tool returns `forbidden`; if one does in the
  future, that client shows `error`.

### Questions decided on 2026-10-12 (spec.md, "Clarifications")

- Q1. `bank_compact`'s top-level string `remediation`
  (`src/live_mem/tools/bank.py:72,1412-1437`) moves into
  `remediation.reason`; the CLI reader (`scripts/cli/display.py:707,778`)
  changes in the same pull request.
- Q2. `required_prior_step_missing` stays in the closed set, documented as
  having no producer yet, and is excluded from the replay test until a tool
  produces it.
- Q3. `"status": "conflict"` (`bank.py:1365-1372`; `gc.py:420,679,700,722`)
  stays a successful result that carries `error_kind="operation_in_progress"`
  and a `remediation` with `retryable=true`; `conflict` is added to the
  documented statuses in `docs/MCP_TOOLS_SPEC.md:108-121`.
- Q5. Confirmed: handlers keep returning dicts and
  `HivemindMCPServer.call_tool` converts every refusal into a
  `CallToolResult(is_error=True)` with `structuredContent` and JSON text;
  the `/api/tool` path stays unchanged.

### Alignment with specification 002 (operator tool guard)

Read from the worktree `/home/user/hivemind-operator`, branch
`spec/002-operator-tool-guard`, commit e01154b:

- It adds its own override of `HivemindMCPServer.call_tool`
  (`src/live_mem/tools/exposure.py:1082-1099` there): it looks the tool up,
  reads the request identity, and when `operator_tool_refusal(...)` returns
  a refusal, returns `tool.fn_metadata.convert_result(refusal)` (a
  successful result with `status: forbidden`); otherwise it calls
  `super().call_tool`.
- It adds the same guard to `call_tool_direct` for `/api/tool`
  (`src/live_mem/tools/__init__.py:107-118` there).
- When the branches meet, this feature's conversion must be added to that
  same override, after the guard: the guard runs first and its `forbidden`
  dict then passes through the conversion like any other refusal, so it is
  delivered as an MCP error result carrying `error_kind` (`operator_tools_refused`
  or `audience_unresolved`) and a `remediation` with `retryable=false`,
  instead of the successful result the 002 override returns on its own.

### Open questions the plan cannot settle

- Q4. The byte cap for the server instructions is not in any source.
- Q6. The sentence "A non-empty client_name identity is required to
  consolidate notes" (`bank.py:441-448`) is a token identity defect, not an
  argument error; its `error_kind` (`authentication_missing` in this plan)
  is a choice, not a source fact.
