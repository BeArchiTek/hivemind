# Implementation Plan: Tool guidance and remediation path for agents

**Branch**: `spec/001-tool-guidance-remediation` | **Date**: 2026-10-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-tool-guidance-remediation/spec.md`

## Summary

An agent connected to Hivemind receives `{"status": "error", "message":
"..."}` with no field that names the next step, chooses tools from
descriptions that state no precondition or peer tool, and receives no server
instructions at initialize ([research.md](research.md), "Gaps this
specification must close"). What is needed: every refused call carries a
stable `error_kind` and a structured `remediation` next to the unchanged
`status` and `message`, travels as an MCP error result with `isError=true`
(payload in `structuredContent` and as JSON text), every `agent_core`
description follows a fixed section contract, and the server sends
instructions built from the memory-bank rules. The technical approach from
the plan-stage research: one conversion point in `HivemindMCPServer.call_tool`
turns a refusal dict into the `CallToolResult`, so no handler is edited for
the delivery and the `/api/tool` proxy and the 56 error-asserting test files
stay as they are; `safe_error` and the guard helpers in
`src/live_mem/auth/context.py` gain `error_kind` and `remediation`; the
audit entry and a Prometheus counter receive the kind through the ASGI
request state; a startup setting `MCP_REFUSAL_IS_ERROR` restores today's
delivery. Decided on 2026-10-12 after the plan review: one server-level
conversion (handlers keep returning dicts), `bank_compact`'s string
`remediation` moves into `remediation.reason`, `conflict` stays a successful
result with guidance fields, and `required_prior_step_missing` stays in the
closed set without a producer. Not decided: the byte cap of the instructions
and the `error_kind` of the missing `client_name` refusal (Q4, Q6).

## Technical Context

**Language/Version**: Python 3.13 (`.venv/lib/python3.13`); Go for
`tools/hivemind-ingest`; JavaScript for the admin console
(`src/live_mem/static/js/admin-api.js`)

**Primary Dependencies**: `mcp` 2.3.0 (`uv.lock:714-715`), its
`MCPServer`/`HivemindMCPServer` (`src/live_mem/tools/exposure.py:957-982`),
pydantic-settings (`src/live_mem/config.py:81`), Starlette ASGI middlewares
(`src/live_mem/middleware.py`)

**Storage**: N/A for this feature; no stored shape changes

**Testing**: pytest with path-based primary markers
(`tests/test_quality_policy.py:41-81`; `docs/TESTING.md:30-50`); Go tests
for `hivemind-ingest`

**Target Platform**: Linux server, Streamable HTTP MCP endpoint `/mcp`
(`src/live_mem/server.py:449-452`)

**Project Type**: MCP server with a CLI, an admin console and a Go ingestion
tool as clients

**Performance Goals**: no new latency budget is stated in the sources; the
conversion adds one dict inspection per tool call

**Constraints**: serialized `tools/list` within 64 KiB per discovery profile
(`src/live_mem/tools/exposure.py:442`); refusal text built from server-owned
literals only (FR-010); `status` and `message` unchanged (FR-025, FR-027);
`tests/fixtures/tool_surface.json` unchanged (FR-023); nominal suite without
credentials (`CONTRIBUTING.md:52-55`)

**Scale/Scope**: 60 registry entries, 73 registered names, 24 `agent_core`
tools (`docs/TOOL_EXPOSURE.md:10-19`); about 53 refusal sites in
`src/live_mem/tools/*.py` and 9 in `src/live_mem/auth/context.py`
([research.md](research.md), Item 4); 3 clients; 56 test files that assert
the error dict

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Principles from [`.specify/memory/constitution.md`](../../.specify/memory/constitution.md).

- I. Fail closed on ambiguous state. Pass. A refusal without `error_kind`
  receives `unclassified_refusal`, which the replay test treats as a failure
  and the counter makes visible; `write_route_refused` names the operator
  workflow and never a fallback route (FR-008); the conversion point reads
  only the top-level `status` and changes no authorization decision.
- II. Every tool handler enforces its own authorization. Pass. The
  remediation names a corrective call; it never performs it (spec
  Assumptions). `required_permission` is read from the registry for display;
  the guards in `src/live_mem/auth/context.py:256-413` stay the decision
  point. No bypass is suggested (FR-004).
- III. The tool surface is a frozen, deliberate contract. Pass. No tool is
  added, removed, renamed or re-levelled; aliases keep sharing the handler
  and the description (`src/live_mem/tools/exposure.py:943-954`);
  `tests/fixtures/tool_surface.json` is unchanged (FR-023). Descriptions
  change, which the alias parity guard verifies for both names at once.
- IV. Tool results are structured and never leak internals. Pass. Every
  refusal keeps the `status` field and the documented vocabulary (no new
  status value, FR-027); `safe_error` keeps its debug-mode rule and gains
  `error_kind="internal_failure"`; remediation text comes from literals and
  registry data (FR-010); input-schema failures copy field names only.
- V. Agents write short notes; only consolidation writes the bank. Pass.
  The server instructions carry these rules (FR-013) and are checked for
  consistency with `docs/AGENT_MEMORY_SETUP.md:89-166` and
  `RULES/live-mem.standard.memory.bank.md:117-143` (FR-015, FR-028).
- VI. A behaviour change ships with a failing-then-passing test. Pass. Each
  test in [research.md](research.md), Item 7, is written against 1.6.1 first;
  mutation proofs are named; the SC-004 evaluation is opt-in by environment
  variable so the nominal suite has no credentials; every new test file
  receives one primary marker from its path.
- VII. Security findings go through the private channel. Pass. The audit
  entry and the metric carry only the canonical tool name and `error_kind`;
  no token, endpoint or space identifier enters the instructions, logs or
  metrics (FR-014, FR-030).

Gate result before Phase 0: pass, no violation to justify.

Gate result after Phase 1 design: pass. The design added one closed-set
value (`unclassified_refusal`) beyond the FR-002 minimum and two reserved
values from specification 002 (`operator_tools_refused`,
`audience_unresolved`); all are additive and documented. No principle is
violated; Complexity Tracking stays empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-tool-guidance-remediation/
├── plan.md                                   # This file
├── spec.md                                   # Feature specification
├── research.md                               # Phase 0: specify-stage findings plus the plan-stage items 1-7, risks, open questions
├── data-model.md                             # Phase 1: payload, remediation, error_kind set, outcome record, setting, instructions, description contract
├── quickstart.md                             # Phase 1: validation checks 1-5
├── contracts/
│   ├── guidance-error-payload.md             # Payload, one example per error_kind, wire delivery, proxy delivery
│   ├── guidance-error-payload.schema.json    # JSON Schema of the payload
│   ├── tool-description-contract.md          # Sections, aliases, operator tools, budget, test checks
│   ├── server-instructions.md                # Content, order, consistency checks, opt-in evaluation
│   └── observability-and-rollout.md          # Audit entry, counter, state mechanism, rollout setting
├── checklists/
│   └── requirements.md                       # Specify-stage checklist
└── tasks.md                                  # Phase 2 output (/speckit-tasks), not created here
```

### Source Code (repository root)

```text
src/live_mem/
├── server.py                       # unchanged construction; instructions injected by HivemindMCPServer.__init__
├── config.py                       # Settings.mcp_refusal_is_error (new field, startup warning when false)
├── instructions.py                 # new: rule records and render_instructions()
├── middleware.py                   # AuditMiddleware and MetricsMiddleware read the tool outcome record
├── auth/
│   ├── context.py                  # guards and safe_error gain error_kind and remediation; token_scope_changed helper
│   └── middleware.py               # /api/tool handler writes the tool outcome record
├── tools/
│   ├── __init__.py                 # unchanged registration; call_tool_direct unchanged
│   ├── exposure.py                 # HivemindMCPServer.call_tool override (conversion point), instructions default
│   ├── guidance.py                 # new: ErrorKind enum, Remediation builder, refusal(...) helper, to_call_tool_result(...)
│   ├── live.py, bank.py, admin.py, space.py, backup.py, graph.py, system.py, access.py
│   │                               # descriptions rewritten to the contract; handler refusals gain error_kind through guidance.refusal
│   └── aliases.py                  # unchanged (descriptions follow the canonical tool)
└── core/
    ├── live.py                     # category refusal gains error_kind="invalid_argument"
    └── consolidation_queue.py      # existing-job acknowledgement gains existing_job (FR-007)

scripts/cli/display.py              # show_bank_compact_failure reads remediation.reason instead of the string (lines 707, 778)
tools/hivemind-ingest/              # no change required (research.md, Item 1)
src/live_mem/static/js/admin-api.js # no change required (research.md, Item 1)

docs/
├── MCP_TOOLS_SPEC.md               # payload, closed set with one example each (FR-012); `conflict` added to the statuses
├── DEPLOYMENT.md                   # new subsection under Production for MCP_REFUSAL_IS_ERROR (FR-031)
└── AGENT_MEMORY_SETUP.md           # unchanged block (FR-028); one sentence noting the server instructions
.env.example                        # MCP_REFUSAL_IS_ERROR=true in the MCP server block
CHANGELOG.md                        # [Unreleased] entry (FR-031)

tests/
├── test_tool_guidance_replay_integration.py   # integration: replay through HivemindMCPServer.call_tool
├── test_tool_guidance_surface.py              # contract: descriptions, budget, instructions, docs
├── test_tool_guidance_observability.py        # unit: audit entry, counter, state sharing (risk R1)
└── test_tool_guidance_agent_evaluation.py     # unit + optional: opt-in SC-004 evaluation
```

**Structure Decision**: single Python project with the existing layout; the
feature adds two modules (`src/live_mem/instructions.py`,
`src/live_mem/tools/guidance.py`) and four test files, and edits the
existing server, guard, middleware, configuration and documentation files
named above. The three clients are not edited.

## Phase 0: research outcome

Full evidence is in [research.md](research.md), "Plan-stage research
(2026-10-12)". Outcome per item:

1. FR-029 clients. CLI: parses `content[0].text` whatever `isError` says
   (`scripts/cli/client.py:99-106`) and shows `message`
   (`scripts/cli/commands.py:104-108`); no change. Admin console: the proxy
   calls the handler function directly and sends the dict as HTTP 200
   (`src/live_mem/tools/__init__.py:98-126`;
   `src/live_mem/auth/middleware.py:645-648`); no change, on the condition
   that handlers keep returning dicts. `hivemind-ingest`: maps `isError` to
   `status: error` and keeps every payload field
   (`tools/hivemind-ingest/internal/mcpclient/client.go:420-428`); no
   change.
2. Registration and conversion. Handlers return `dict` with no output
   schema; the SDK converts a dict to one text item
   (`func_metadata.py:653`) and passes a `CallToolResult` through unchanged
   (`func_metadata.py:205-208`). The single conversion point is an override
   of `call_tool` on `HivemindMCPServer`, which covers aliases and hidden
   operator tools and also catches the SDK's input-schema `ToolError`. The
   56 test files reach handlers through `.fn`, through service classes, or
   through clients that parse the text item, so FR-025 holds. Confirmed:
   handlers keep returning dicts and the `/api/tool` path is unchanged.
   `bank_compact`'s string `remediation` moves into `remediation.reason`;
   the CLI reader `show_bank_compact_failure` (`scripts/cli/display.py:707`,
   printed at `display.py:778`) changes in the same pull request.
3. Instructions. `HivemindMCPServer(...)` passes none
   (`src/live_mem/server.py:249-254`); the SDK accepts and sends the string
   (`mcp/server/mcpserver/server.py:163,216`; `mcp/server/lowlevel/server.py:562`).
   The 64 KiB budget covers `tools/list` only; the instructions get their
   own cap. The consistency test locates the document block by heading and
   checks each rule record's markers in the text and in its source.
4. Closed set grounded in the refusal sites: `authentication_missing`,
   `token_scope_changed`, `invalid_space_id`, `space_access_denied`,
   `permission_insufficient`, `missing_argument`, `invalid_argument`,
   `required_prior_step_missing` (no producer today; excluded from the
   replay test until one exists), `operation_in_progress` (`conflict`
   results, which stay successful results with `retryable=true`),
   `write_route_refused`, `internal_failure`, `operator_tools_refused` and
   `audience_unresolved` (reserved for specification 002),
   `unclassified_refusal` (drift indicator).
5. Audit and metrics. Both middlewares are ASGI-level and know no tool
   name; the tool call runs outside the request task, so the value travels
   through `scope["state"]` as the identity binding does
   (`src/live_mem/auth/context.py:33-37,118-130`). `AuditMiddleware` adds
   `tool` and `error_kind`; `MetricsMiddleware` renders
   `livemem_tool_refusals_total{tool,error_kind}`.
6. Setting. `Settings.mcp_refusal_is_error: bool = True`
   (`src/live_mem/config.py:88-92`), environment variable
   `MCP_REFUSAL_IS_ERROR`, pydantic boolean validation, startup warning when
   `false`, documented in `.env.example`, `CHANGELOG.md` and a new
   `docs/DEPLOYMENT.md` subsection.
7. Tests. Four files with path-derived markers (`integration`, `contract`,
   `unit`, `unit` plus `optional`), each failing against 1.6.1 first, with
   named mutation proofs; the SC-004 evaluation skips unless
   `HIVEMIND_AGENT_EVAL=1` and credentials are set.

## Phase 1: design artifacts

- [data-model.md](data-model.md): Guidance error payload, remediation
  object, closed `error_kind` set, tool outcome record, rollout setting,
  server instructions, tool description contract, exposure registry entry.
- [contracts/guidance-error-payload.md](contracts/guidance-error-payload.md)
  and [contracts/guidance-error-payload.schema.json](contracts/guidance-error-payload.schema.json).
- [contracts/tool-description-contract.md](contracts/tool-description-contract.md).
- [contracts/server-instructions.md](contracts/server-instructions.md).
- [contracts/observability-and-rollout.md](contracts/observability-and-rollout.md).
- [quickstart.md](quickstart.md): checks 1 to 5.

Agent context update: the `speckit-plan` skill in this repository lists no
agent-context script and `.specify/scripts/bash/` contains none, so no
agent context file was updated.

## Alignment with specification 002 (operator tool guard)

Specification 002 is implemented on branch `spec/002-operator-tool-guard`
(commit e01154b, read from `/home/user/hivemind-operator`). It adds its own
override of `HivemindMCPServer.call_tool` (its
`src/live_mem/tools/exposure.py:1082-1099`) that returns the operator
refusal through `tool.fn_metadata.convert_result(refusal)` before calling
`super().call_tool`, and a matching check in `call_tool_direct` for
`/api/tool` (its `src/live_mem/tools/__init__.py:107-118`). When the
branches meet:

- this feature's conversion is added to that same override, not to a
  second override;
- the 002 guard runs before the conversion, and its `forbidden` refusal
  dict passes through the conversion like any other refusal, so the agent
  views an MCP error result with `error_kind` `operator_tools_refused` or
  `audience_unresolved` and `remediation.retryable=false`;
- the 002 constants `OPERATOR_TOOLS_REFUSED` and `AUDIENCE_UNRESOLVED`
  (its `exposure.py:973-974`) are the source of the two reserved
  `error_kind` values; this feature does not redefine them.

## Risks and open questions

Recorded in [research.md](research.md), "Risks" (R1 to R5), "Questions
decided on 2026-10-12" (Q1, Q2, Q3, Q5) and "Open questions the plan cannot
settle" (Q4, Q6). In short:

- R1: `scope["state"]` must be the same dict in the SDK task and the
  middleware; the first observability test proves it or the fallback
  applies.
- R2: direct handler calls (proxy, 20 test files) bypass the conversion;
  a missing `error_kind` is caught only by the replay test and the counter.
- R3: the discovery budget usage after the description rewrite is not
  measured yet.
- R4: input-schema failures depend on pydantic error locations.
- R5: `hivemind-ingest` would show a future `forbidden` as `error`.
- Q1 (decided): `bank_compact`'s string `remediation` moves into
  `remediation.reason`; the CLI reader changes in the same pull request.
- Q2 (decided): `required_prior_step_missing` stays in the closed set
  without a producer and outside the replay test.
- Q3 (decided): `conflict` stays a successful result that carries
  `error_kind="operation_in_progress"` and `retryable=true`, and is added to
  the documented statuses.
- Q4 (open): the byte cap of the instructions.
- Q5 (decided): one server-level conversion; handlers keep returning dicts;
  `/api/tool` unchanged.
- Q6 (open): the `error_kind` for the missing `client_name` identity refusal.

## Complexity Tracking

No constitution violation to justify.
