# Contract: tool descriptions for `agent_core` tools, aliases and operator tools

**Summary**: Today a description states what a tool does and its arguments
(`src/live_mem/tools/live.py:61-80`) but not when to use it, what must be
true first or which tool to use instead ([research.md](../research.md), "What
the descriptions say today"). This contract fixes the sections every
`agent_core` description carries, how aliases and hidden operator tools are
described, and the checks the contract test runs. Not decided: how much of
the 64 KiB discovery budget the new text uses (risk R3).

## Sections, in order, for every `agent_core` tool (FR-017)

The description is the function docstring (or the decorator's
`description=`); the MCP client views it in `tools/list`.

1. Purpose: one sentence.
2. "Use when": the situations the tool is for.
3. "Do not use when": the situations it is not for, each naming the tool to
   use instead.
4. "Before calling": the required permission (`read`, `write`, `manage` or
   `admin`), the space access when the tool takes a space argument, and the
   prior calls (for example `space_rules`, `mid_read_all` and `short_read`
   before `short_note`).
5. "Peer tools": the tools normally called before, after, or instead.
6. "Result": what the agent does with the returned fields (for example
   "return to the user; do not poll `bank_consolidation_status`").

Rules:

- Section 4's permission equals `minimum_permission` of the tool's
  `ToolExposure` entry (`src/live_mem/tools/exposure.py:92-243`), verified by
  the contract test (FR-019).
- Section 4 names the space argument given by `space_scope_argument` when it
  is set.
- Every parameter `Field(description=...)` states the allowed values or
  format and the default when one exists (FR-018), for example the seven
  categories for `short_note.category` (`src/live_mem/core/live.py:43`).
- The description passes the existing public-safety check
  (`tests/test_mcp_tool_surface.py:581-603`).
- Fixed statements required by User Story 2:
  - `short_note`: the seven categories; the agent identity comes from the
    token and is never passed; `mid_write` is not an alternative for agents.
  - `mid_consolidate`: at most once at the end of a work block, after user
    validation; return to the user without polling.

## Aliases (FR-020)

- Each historical alias (`live_*`, `bank_*`, `graph_*`, 13 pairs,
  `src/live_mem/tools/aliases.py`) keeps a description identical to its
  canonical tool; the registry validator rejects any difference
  (`src/live_mem/tools/exposure.py:943-954`).
- The shared description names the canonical tool in its first section, for
  example "Canonical name: `short_note` (also registered as `live_note`)".

## Hidden operator tools (FR-021)

- The first section states "Operator tool: not advertised in `tools/list`."
- Where an agent tool replaces it, the section names it, for example
  `mid_write` names `short_note` and `mid_consolidate`.
- Visibility rules are unchanged (`docs/TOOL_EXPOSURE.md:17-19`).

## Discovery budget (FR-022)

- The serialized `tools/list` response for each discovery profile (`read`
  17 tools, `write` 20, `manage` and `admin` 24, `docs/TOOL_EXPOSURE.md:10-15`)
  stays within `DISCOVERY_SCHEMA_BUDGET_BYTES` (64 KiB,
  `src/live_mem/tools/exposure.py:442`); the existing test asserts it
  (`tests/test_p10_tool_exposure.py:279`).
- A description that pushes a profile over the budget fails the test; the
  budget is not raised by this feature.

## Contract test checks (`tests/test_tool_guidance_surface.py`)

- For each `agent_core` canonical name: the six section headings are present
  in order; the stated permission equals the registry minimum; the space
  argument is named when the registry sets one.
- For each parameter of each registered tool: the description is non-empty
  and, for an enumerated argument, lists the allowed values.
- For each alias pair: descriptions are equal and name the canonical tool.
- For each operator tool: the operator statement is present.
- Per profile: the serialized `tools/list` size is within the budget.
- `tests/fixtures/tool_surface.json` is unchanged by this feature (FR-023);
  the existing fixture test covers it.
