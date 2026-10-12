# Contract: Guidance error payload and its delivery on the MCP wire

**Summary**: A refused tool call returns a dict that keeps `status` and
`message` and adds `error_kind` and `remediation`. On the MCP transport the
dict travels inside a `CallToolResult` with `isError=true`, both as
`structuredContent` and as JSON text in `content[0]`. The `/api/tool` proxy
returns the dict unchanged as an HTTP 200 JSON body. Not decided: whether
`conflict` results carry the guidance fields (open question Q3) and the
shape of `bank_compact`'s string `remediation` (Q1).

The JSON Schema is in
[guidance-error-payload.schema.json](guidance-error-payload.schema.json).

## Payload shape

```json
{
  "status": "error",
  "message": "Access denied to space 'q'",
  "error_kind": "space_access_denied",
  "accessible_spaces": ["p"],
  "remediation": {
    "next_tool": "space_list",
    "reason": "The token has no access to space 'q'. Call space_list to view the spaces it can access, then retry with one of them.",
    "retryable": true
  }
}
```

Rules:

- `status`, `message`, `error_kind`, `remediation` are required.
- `remediation.reason` and `remediation.retryable` are required; the other
  remediation fields are present only when known.
- `required_permission`, `current_permissions`, `accessible_spaces`,
  `existing_job` are present only when they apply (FR-027).
- No other new top-level field is added. Existing per-tool fields stay.

## One example per `error_kind`

Each example keeps the 1.6.1 `message` of its refusal site; `error_kind` and
`remediation` are the additions.

- `authentication_missing`, `message` "Authentication required"
  (`src/live_mem/auth/context.py:280`): `remediation = {"reason": "Send a
  bearer token in the Authorization header; an operator issues tokens with
  token_create.", "retryable": true}`.
- `token_scope_changed`, `message` "Authentication required"
  (`src/live_mem/auth/context.py:237-238,280`): `remediation =
  {"required_prior_step": "refresh the tool list", "reason": "An operator
  changed this token's scope during the session; reconnect so tools/list
  matches the new scope, then retry.", "retryable": true}`.
- `invalid_space_id`, `message` "Invalid space identifier: 'Bad Id'"
  (`src/live_mem/auth/context.py:284-288`): `remediation =
  {"missing_arguments": ["space_id"], "reason": "space_id must match the
  documented format, for example 'project-alpha'.", "retryable": true}`.
  For a reserved identifier the message is the one at
  `src/live_mem/core/memory_id.py:59-63` and the reason gives the format
  only; it does not say whether the reserved space exists.
- `space_access_denied`, `message` "Access denied to space 'q'"
  (`src/live_mem/auth/context.py:303-307`): see the payload shape above;
  `accessible_spaces` lists the token's spaces, `[]` for a fresh token, in
  which case `reason` says a manager must invite the token with
  `space_invite_token` and names `system_whoami`.
- `permission_insufficient`, `message` "The 'manage' permission is required
  for this operation" (`src/live_mem/auth/context.py:383-386`):
  `required_permission = "manage"`, `current_permissions = ["read",
  "write"]`, `remediation = {"reason": "An operator with manage or admin
  must perform this action or issue a token with manage.", "retryable":
  false}`; no `next_tool`.
- `missing_argument`, `message` "Deletion refused: confirm=True is required.
  This operation cannot be undone." (`src/live_mem/tools/space.py:505-514`):
  `remediation = {"missing_arguments": ["confirm"], "arguments": {"confirm":
  true}, "reason": "Pass confirm=true to delete the space; the deletion
  cannot be undone.", "retryable": true}`. A required argument absent from
  the call is reported with the same kind from the input-schema validation
  (see "Input-schema failures").
- `invalid_argument`, `message` "Invalid category: 'note'. Valid values:
  observation, decision, todo, insight, question, progress, issue"
  (`src/live_mem/core/live.py:99-106`): `remediation =
  {"missing_arguments": ["category"], "reason": "category must be one of
  observation, decision, todo, insight, question, progress, issue, for
  example 'observation'.", "retryable": true}`.
- `required_prior_step_missing`: no producer in 1.6.1 (open question Q2).
  Shape: `remediation = {"required_prior_step": "<tool name>",
  "next_tool": "<tool name>", "reason": "...", "retryable": true}`.
- `operation_in_progress`, `message` "Consolidation is in progress for 'p'.
  Try again in a few minutes." (`src/live_mem/tools/bank.py:1365-1372`):
  `remediation = {"reason": "Wait for the running consolidation to finish,
  then retry; do not submit a second consolidation.", "retryable": true}`.
  Delivery of this `conflict` result is open question Q3.
- `write_route_refused`, `message` "Writes to a shared space are not accepted
  on this route" (new server-owned text replacing "Internal server error" for
  `StagedWriteNotImplemented` at `src/live_mem/tools/live.py:118-134`):
  `remediation = {"reason": "The space is shared; an operator applies the
  staged write workflow described in docs/DEPLOYMENT.md. Do not retry on
  another route.", "retryable": false}`.
- `internal_failure`, `message` "Internal server error"
  (`src/live_mem/auth/context.py:438`): `remediation = {"reason": "The
  server logged the failure under the request identifier; report it to an
  operator.", "retryable": false}`.
- `operator_tool_refused`: reserved for specification 002; no example.
- `unclassified_refusal`: never intended; `remediation = {"reason": "This
  refusal has no classification; report it to an operator.", "retryable":
  false}`.

## Delivery on the MCP transport

For a result whose top-level `status` is `error` or `forbidden`,
`HivemindMCPServer.call_tool` returns (SDK field names from
`mcp.types.CallToolResult`: `content`, `structured_content` alias
`structuredContent`, `is_error` alias `isError`):

```json
{
  "content": [{"type": "text", "text": "<the payload serialized as JSON>"}],
  "structuredContent": {"...": "the same payload"},
  "isError": true
}
```

Rules:

- `isError` is `true` when `MCP_REFUSAL_IS_ERROR` is `true` (default) and
  `false` when the operator set it to `false`; nothing else changes.
- The text item and `structuredContent` deserialize to equal objects.
- Every other result keeps the SDK's conversion of a dict (one text item,
  JSON with two-space indentation, no `structuredContent`,
  `mcp/server/mcpserver/utilities/func_metadata.py:653`).
- `not_found` and `partial` results are not refusals; they keep successful
  delivery and may carry a `remediation` object (FR-026).
- A tool name that is not registered receives the SDK's text-only error
  result; this feature does not change it.

## Input-schema failures

When the SDK rejects the arguments before the handler runs
(`mcp/server/mcpserver/tools/base.py:154-157`), `HivemindMCPServer.call_tool`
returns a Guidance error payload built from the pydantic error locations
only: `error_kind` is `missing_argument` for a `missing` error type and
`invalid_argument` otherwise; `remediation.missing_arguments` lists the
field names; `message` is "Invalid arguments for <tool>: <field names>".
The pydantic message text and the rejected values are not copied (FR-010).

## Delivery through the `/api/tool` proxy

The proxy returns the handler's dict as an HTTP 200 JSON body
(`src/live_mem/auth/middleware.py:645-648`; `src/live_mem/tools/__init__.py:83-137`).
It does not go through `call_tool`, so no `isError` flag exists; the body is
the Guidance error payload unchanged (FR-011). The proxy writes the tool
outcome record (data-model.md) so the refusal is audited and counted.
