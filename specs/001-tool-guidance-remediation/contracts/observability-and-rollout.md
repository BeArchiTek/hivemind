# Contract: audit entry, Prometheus counter and the rollout setting

**Summary**: Operators cannot view today which kinds of refusals agents hit:
the audit entry records HTTP method, path and status only
(`src/live_mem/middleware.py:491-513`), and the metrics count HTTP errors by
path (`src/live_mem/middleware.py:198-235`). This contract adds the tool name
and `error_kind` to the audit entry, a counter by tool and `error_kind`, and
the startup setting that restores today's delivery. Not decided: the release
that removes the setting (FR-031), and whether the ASGI state dict is shared
between the SDK task and the middleware (risk R1).

## Audit entry (FR-030)

The `request` event written by `AuditMiddleware`
(`src/live_mem/middleware.py:491-513`) gains two fields when the request
carried a refused tool call:

- `tool`: the canonical tool name (alias resolved through `TOOL_EXPOSURES`),
  or `unknown`.
- `error_kind`: one value from the closed set.

Rules:

- Present only for refusals; absent otherwise.
- The entry never contains argument values, remediation text, space
  identifiers or tokens; the existing fields are unchanged.
- The `/api/tool` proxy path writes the same two fields into its `request`
  entry; its separate `admin_tool_call` entry
  (`src/live_mem/auth/middleware.py:622-643`) is unchanged.

## Prometheus counter (FR-030)

Rendered by `MetricsMiddleware._serve_metrics`
(`src/live_mem/middleware.py:198-235`) in the exposition format:

```text
# HELP livemem_tool_refusals_total Refused tool calls by canonical tool and error kind
# TYPE livemem_tool_refusals_total counter
livemem_tool_refusals_total{tool="short_note",error_kind="space_access_denied"} 3
```

Rules:

- Labels are `tool` and `error_kind` only.
- `tool` values are the 60 canonical names plus `unknown`; `error_kind`
  values are the closed set; the label space is bounded.
- The JSON form of `/metrics` (`Accept: application/json`) gains a
  `tool_refusals` object keyed by `"<tool>/<error_kind>"`.

## How the values reach the middlewares

`HivemindMCPServer.call_tool` (and the `/api/tool` handler) writes
`scope["state"]["live_mem.tool_outcome"] = {"tool": ..., "error_kind": ...}`
on the request's ASGI scope; both middlewares read that key in their
`finally` blocks. The mechanism is the one the identity binding uses
(`src/live_mem/auth/context.py:33-37,118-130`). The first test of the
feature proves the entry written in the SDK task is read by the middleware
(risk R1); if it is not, the conversion point writes the audit line and the
counter itself and this contract is amended.

## Rollout setting (FR-031)

- `Settings.mcp_refusal_is_error: bool = True` (`src/live_mem/config.py`,
  after `mcp_server_debug` at line 92); environment variable
  `MCP_REFUSAL_IS_ERROR`.
- `true` (default): refused calls are MCP error results (`isError=true`).
- `false`: refused calls are successful MCP results with the same payload.
  Only the `isError` flag changes; the payload, the audit entry and the
  counter are the same.
- Validation: pydantic boolean parsing at startup; `_validate_config`
  (`src/live_mem/config.py:298-440`) logs one warning when the value is
  `false`, stating that the setting is temporary.
- Documentation: `.env.example:10-17`, `CHANGELOG.md` "[Unreleased]"
  (`CHANGELOG.md:17-19`), and a new subsection under "Production" in
  `docs/DEPLOYMENT.md` (`docs/DEPLOYMENT.md:184`), each stating that a later
  release removes the setting. Which release is not decided.
