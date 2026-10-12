# Research: current tool guidance, errors and server instructions in Hivemind 1.6.1

**Summary**: Hivemind's tool descriptions are Python docstrings that say what a
tool does but not when to use it, what must happen first or which tool to call
instead. Errors are `{"status": "error", "message": "..."}` dicts with no
field that names the next step; three tools go further on their own. The MCP
server sends no `instructions` at initialize, so the memory-bank workflow
reaches agents only through client-side instruction files. What is needed is a
single error shape with a remediation field, descriptions that carry
preconditions and peer tools, and server-side instructions. Not decided: whether
soft errors travel as MCP `isError=true` or as a successful result, and how
much of the current error shape existing clients require to stay unchanged.

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
