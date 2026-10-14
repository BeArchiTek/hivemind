# Contract: operator tool refusal and audit entry

**Summary**: A refused operator tool call returns today's result shape with a
fixed message and writes one audit line. The refusal cause is named for spec
001's `error_kind`.

## Result returned to the caller

Over `/mcp` the result is a successful `CallToolResult` whose single text
content is the JSON below (same rendering as a handler returning the dict);
over `POST /api/tool` the HTTP body is the JSON below with status 200, as for
any handler result.

```json
{
  "status": "forbidden",
  "message": "This tool is reserved for operators. Ask an operator to perform the action through the admin console or the CLI with an operator token."
}
```

Rules:

- The `message` text is identical for every `audience=operator` tool and
  every alias, and for the fail-closed case where the audience cannot be
  resolved.
- The text names no other tool and does not say whether other hidden tools
  exist.
- No field other than `status` and `message` is present. Spec 001 will add
  `error_kind` and `remediation`; the value reserved for this refusal is
  `error_kind: "operator_tools_refused"`, with `remediation.next_tool` absent,
  `remediation.retryable: false` and `remediation.reason` carrying the same
  message.

## When the refusal is returned

| Condition | Route `/mcp` | Route `/api/tool` |
|---|---|---|
| Name not registered | SDK "Unknown tool" result (unchanged) | `{"status": "error", "message": "Unknown tool: <name>"}` (unchanged) |
| Name registered, no registry entry, identity present | refusal, cause `audience_unresolved` | refusal, cause `audience_unresolved` |
| Identity missing (any registered name) | handler runs its own authentication check and refuses | handler runs its own authentication check and refuses |
| Registry audience `agent_core` | handler runs | handler runs |
| Registry audience `operator`, `operator_tools` absent or `true` | handler runs | handler runs |
| Registry audience `operator`, `operator_tools` `false` or not a boolean | refusal, cause `operator_tools_refused` | refusal, cause `operator_tools_refused` |

## Audit entry

One JSON line on the `live_mem.audit` logger at INFO level per refusal:

```json
{
  "event": "operator_tool_refused",
  "request_id": "<request id or '-'>",
  "route": "mcp",
  "tool": "bank_write",
  "canonical_tool": "mid_write",
  "cause": "operator_tools_refused",
  "client": "agent-worker",
  "auth_type": "token"
}
```

Rules:

- `route` is `mcp` or `api_tool`.
- `tool` is the name as called; `canonical_tool` is the registry's canonical
  name, or `null` when the audience could not be resolved.
- `cause` is `operator_tools_refused` or `audience_unresolved`.
- The entry carries no argument value, no space identifier and no bearer
  token.
