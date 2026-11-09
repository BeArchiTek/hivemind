# Contract: token tools and CLI commands that carry `operator_tools`

**Summary**: Three hidden admin tools gain an optional parameter; one
agent-facing tool keeps its parameters and changes its default; list and
identity results gain one field. No tool is added, removed, renamed or
re-levelled, and discovery does not change.

## MCP tools

| Tool | Audience, permission | Change |
|---|---|---|
| `token_create` | `agent_core`, `manage` | No parameter change. The created record has `operator_tools: false`. The response adds `"operator_tools": false`. |
| `admin_create_token` | `operator`, `admin` | New parameter `operator_tools: bool = True`. The response adds `"operator_tools": <value>`. |
| `admin_update_token` | `operator`, `admin` | New parameter `operator_tools: str = ""`; accepted values `"true"`, `"false"` (case-insensitive), empty for no change. It counts as a change for "nothing to update" purposes. When the value changes, the response adds `"operator_tools_before"` and `"operator_tools_after"`. Other text returns `{"status": "error", "message": "Invalid operator_tools: expected 'true' or 'false'"}` before the store is read. |
| `admin_bulk_update_tokens` | `operator`, `admin` | New parameter `operator_tools: str = ""` with the same grammar; it satisfies "at least one operation is required". `operations.operator_tools` reports the parsed boolean or `null`; each token entry's `before` and `after` objects gain `operator_tools`. |
| `admin_list_tokens` | `operator`, `admin` | Each token entry gains `"operator_tools": <bool>`. |
| `system_whoami` | `agent_core`, `read` | The result gains `"operator_tools": <bool>` (the effective value; `true` for the bootstrap key and for records without the attribute). |

The `mcp_reconnect_required` flag is not set for an `operator_tools` change
alone: discovery does not depend on the attribute, so no client needs to
refresh its tool list.

## CLI (`scripts/mcp_cli.py`)

| Command | Change |
|---|---|
| `token create <name> -p <profile> [--operator-tools / --no-operator-tools]` | Sent as `operator_tools` only when the caller is routed to `admin_create_token`. For a manager caller (routed to `token_create`) either flag is a usage error that states the created token refuses operator tools. |
| `token update <hash> --operator-tools true\|false` | Sent as `operator_tools`. Counts as an update for the "Nothing to update" guard. Available in the shell as `token update <hash> --operator-tools true\|false`. |
| `token bulk-update ... --operator-tools true\|false` | Sent as `operator_tools`. |
| `token list` | Shows an `Operator tools` column (`yes` / `no`). |
| `token create` output | Shows `Operator tools over MCP: allowed / refused`. |

## Admin console (`/admin`, Access view)

- Token table: a column "Operator tools" with `allowed` or `refused`.
- Create token form, admin mode only: a checkbox "May call operator tools over
  MCP", checked by default; unchecked sends `operator_tools: false`. In
  manager mode the form shows that the token will refuse operator tools.
- Edit token form: a select with "no change", "allow", "refuse"; sends
  `operator_tools: "true"` or `"false"` when changed.

## Fixture block (`tests/fixtures/tool_surface.json`)

```json
"operator_tool_guard": {
  "_comment": "...",
  "token_attribute": "operator_tools",
  "absent_record_reads_as": true,
  "creation_defaults": {"token_create": false, "admin_create_token": true},
  "tools_with_parameter": ["admin_create_token", "admin_update_token", "admin_bulk_update_tokens"],
  "refusal_status": "forbidden",
  "refusal_cause": "operator_tools_refused"
}
```

A test in `tests/test_operator_tool_guard.py` compares the running code with
this block.
