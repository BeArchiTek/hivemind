# Quickstart: validating the tool guidance and remediation path

**Summary**: This guide lists the runnable checks that prove the feature
end-to-end: the replay of wrong calls through an in-process MCP server, the
description and instructions contract, the observability checks, the three
client checks, and the opt-in real-model evaluation. What is needed before
running them: the repository's development environment and, for the
evaluation only, model credentials. Not decided: the byte cap of the
instructions and the release that removes the rollout setting.

## Prerequisites

- `uv sync --locked --dev` (`CONTRIBUTING.md:26-33`).
- No production credentials for the nominal checks (`CONTRIBUTING.md:52-55`).
- For the `hivemind-ingest` check: a Go toolchain
  (`tools/hivemind-ingest`).
- For the opt-in evaluation: a model account whose credentials are set in
  the environment variables the test names; they never enter the repository.

## Check 1. Replay of wrong calls through the MCP server (SC-001, SC-002)

```bash
uv run pytest tests/test_tool_guidance_replay_integration.py -q
```

Expected outcome:

- At least 20 calls, each refused with `isError=true`, a text item and a
  `structuredContent` that deserialize to equal objects, and a payload that
  validates against
  [contracts/guidance-error-payload.schema.json](contracts/guidance-error-payload.schema.json).
- Every `error_kind` value appears at least once, except
  `operator_tools_refused` and `audience_unresolved` (reserved for
  specification 002), `required_prior_step_missing` (no producer yet,
  excluded until a tool produces it) and `unclassified_refusal`, which never
  appears.
- A `conflict` result (`bank_compact` while a consolidation lock is held)
  is delivered with `isError=false`, `status="conflict"`,
  `error_kind="operation_in_progress"` and `remediation.retryable=true`.
- A `bank_compact` failure carries `remediation.reason` and no top-level
  string `remediation`; `uv run python -m scripts.cli` shows the reason
  through `show_bank_compact_failure` (`scripts/cli/display.py:707,778`).
- For every payload whose `remediation.next_tool` the token may call, the
  named call with the given arguments succeeds.
- No payload contains "Internal server error" for a cause other than
  `internal_failure`, with `MCP_SERVER_DEBUG` off.
- The six User Story 1 scenarios pass: existing consolidation job
  (`existing_job`, `next_action="return_to_user_without_polling"`, no second
  job), inaccessible space (`accessible_spaces`, `space_list`), `space_create`
  with `read,write` (`required_permission`, no bypass), shared-space
  `short_note` (`write_route_refused`, not "Internal server error"), invalid
  `category` (allowed values and example), re-scoped token (refresh the tool
  list).
- Before the change, the same test fails on the missing `error_kind` and on
  `isError` being false (constitution principle VI).

Rollout setting check:

```bash
MCP_REFUSAL_IS_ERROR=false uv run pytest tests/test_tool_guidance_replay_integration.py -q -k rollout
```

Expected: `isError` is false and the payload is identical to the default
run.

## Check 2. Descriptions, instructions and documentation (SC-003, SC-006)

```bash
uv run pytest tests/test_tool_guidance_surface.py tests/test_mcp_tool_surface.py tests/test_p10_tool_exposure.py -q
uv run python scripts/check_doc_links.py
```

Expected outcome:

- Every `agent_core` description carries the six sections in order and
  states the registry's minimum permission
  ([contracts/tool-description-contract.md](contracts/tool-description-contract.md)).
- Every alias description equals its canonical tool's and names it; every
  hidden operator tool states that it is an operator tool.
- Each discovery profile stays within the 64 KiB budget
  (`tests/test_p10_tool_exposure.py:279`).
- The initialize result carries the instructions; each FR-013 rule is
  present in order and consistent with `docs/AGENT_MEMORY_SETUP.md:89-166`
  and `RULES/live-mem.standard.memory.bank.md:117-143`; no deployment value
  is present ([contracts/server-instructions.md](contracts/server-instructions.md)).
- `docs/MCP_TOOLS_SPEC.md` lists every `error_kind` with one example.
- `tests/fixtures/tool_surface.json` is unchanged.

## Check 3. Audit entry and counter (FR-030)

```bash
uv run pytest tests/test_tool_guidance_observability.py -q
```

Expected outcome:

- The `request` audit entry for a refused call carries `tool` and
  `error_kind` and no argument value, remediation text, space identifier or
  token.
- `/metrics` shows `livemem_tool_refusals_total{tool="...",error_kind="..."}`
  with the canonical tool name for an alias call.
- The first test proves the state entry written during the tool call is
  read by the middleware (risk R1); if it fails, the fallback in
  [contracts/observability-and-rollout.md](contracts/observability-and-rollout.md)
  applies.

## Check 4. The existing suite and the three clients (SC-005)

```bash
uv run pytest tests/ -q
git diff --check
```

Expected: the suite passes with no edit to any assertion on `status` or
`message` in the 56 files that assert the error dict.

Client checks, each against a running server with a `read,write` token that
has no access to space `q`:

- CLI: `uv run python -m scripts.cli space info q` shows the 1.6.1 message
  "Access denied to space 'q'" (`scripts/cli/commands.py:104-108`); with
  `--json` the printed payload carries `error_kind` and `remediation`.
- Admin console: a `POST /api/tool` with `{"tool": "space_info",
  "arguments": {"space_id": "q"}}` returns HTTP 200 and the payload
  unchanged; the console shows `message`
  (`src/live_mem/auth/middleware.py:645-648`).
- `hivemind-ingest`: `cd tools/hivemind-ingest && go test ./...` passes
  (`internal/mcpclient/client_test.go:174-192`), and a run against space `q`
  reports `status: error` with the message (`internal/ingest/engine.go:460`).

## Check 5. Opt-in evaluation with a real model (SC-004)

```bash
HIVEMIND_AGENT_EVAL=1 uv run pytest tests/test_tool_guidance_agent_evaluation.py -q -m optional
```

Expected outcome:

- Without `HIVEMIND_AGENT_EVAL=1` the test skips, so the nominal suite stays
  credential-free.
- With it, 10 runs each show `space_rules`, `mid_read_all` and `short_read`
  before the first `short_note`, notes only in the seven categories, one
  `mid_consolidate`, and no `bank_consolidation_status` call afterwards.
- The test writes the model name and each run's result to a JSON file that
  the pull request quotes.
