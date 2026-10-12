# Contract: server instructions sent at initialize

**Summary**: The server sends no instructions at initialize today
(`src/live_mem/server.py:249-254`), so an agent whose client has no project
instruction file receives no workflow. This contract fixes the content and
order of the instructions, the properties every token receives the same text
with no deployment value, and the consistency checks against the documents
the rules come from. Not decided: the byte cap (open question Q4).

## Content and order (FR-013)

Each item is one rule record in `src/live_mem/instructions.py` with the
source that states it.

1. Start of every task: call `space_rules`, `mid_read_all` and `short_read`
   on the space the token works in; if any call fails, times out or returns
   a non-OK status, stop before any mutation
   (`docs/AGENT_MEMORY_SETUP.md:110-121`).
2. Write discipline: write atomic `short_note` entries in one of
   `observation`, `decision`, `todo`, `insight`, `question`, `progress`,
   `issue`; the agent identity comes from the token and is never passed;
   never write the bank directly; never put tokens, endpoints or secrets in
   notes (`docs/AGENT_MEMORY_SETUP.md:123-130,154-156`;
   `RULES/live-mem.standard.memory.bank.md:126-134`).
3. End of a work block: write one summary note, confirm user validation,
   call `mid_consolidate` at most once, return without polling;
   `bank_consolidation_status` is only for an explicit status request
   (`docs/AGENT_MEMORY_SETUP.md:140-152`;
   `RULES/live-mem.standard.memory.bank.md:137-143`).
4. Permission model: `admin ⊃ manage ⊃ write ⊃ read`; a token views only the
   tools its permission allows; `system_whoami` shows the token's permissions
   and spaces; a token with no space needs an invitation from a manager
   (`docs/MCP_TOOLS_SPEC.md:125-130`; `src/live_mem/auth/context.py:296-306`).
5. Long memory: derived and non-authoritative; repository files win; no
   routine `long_push` or ingestion (`docs/AGENT_MEMORY_SETUP.md:105-108,
   135-138`).
6. Reading a refusal: a refused call is an MCP error result whose payload
   carries `error_kind` and `remediation`; follow `remediation.next_tool`
   or `required_prior_step`; when `retryable` is false, report the
   `message` to the user ([guidance-error-payload.md](guidance-error-payload.md)).

Rules:

- Items 1 and 2 come first so a client that truncates the text still shows
  them (Edge Cases).
- The text is identical for every token (FR-016); per-space rules stay in
  `space_rules`.
- The text contains no space identifier, token, endpoint, URL or placeholder
  such as `{SPACE}` (FR-014). Where the document block says "space
  `{SPACE}`", the instructions say "the space the token works in".
- The text stays within a byte cap asserted by the contract test (value to
  be set at implementation, open question Q4).

## Relationship to the client-side instruction block (FR-028)

- The recommended project instruction block in
  `docs/AGENT_MEMORY_SETUP.md:89-166` stays; the server instructions
  complement it because some clients do not show server instructions to the
  model.
- Every rule named above that the block states must be present in both,
  with the same facts (tool names, the seven categories, "at most once",
  "without polling").

## Contract test checks (`tests/test_tool_guidance_surface.py`)

- The initialize result of an in-process `HivemindMCPServer` carries the
  rendered text (`mcp/server/lowlevel/server.py:562`).
- Each rule record's marker phrases are present in the rendered text, in
  the order above.
- Each marker phrase is present in the rule's source document, located by
  heading: the fenced block after "## Recommended project instruction
  block" in `docs/AGENT_MEMORY_SETUP.md`, and the "Recommended Agent
  Workflow" section in `RULES/live-mem.standard.memory.bank.md`.
- The seven categories in the text equal `VALID_CATEGORIES`
  (`src/live_mem/core/live.py:43`) and the list in the document block.
- No `http://`, `https://`, `{HIVEMIND_MCP_SERVER}`, `{SPACE}` or token-like
  string is present.
- The rendered size is within the cap.
- A mutation proof removes one rule record and shows the test fails.

## Opt-in evaluation (SC-004, outside the nominal suite)

`tests/test_tool_guidance_agent_evaluation.py` runs only when
`HIVEMIND_AGENT_EVAL=1` and the model credential variables are set; it gives
a real model the rendered instructions and a `read,write` token against an
in-process server and checks, in 10 runs, the call order before the first
`short_note`, the categories, one `mid_consolidate`, and no status call
afterwards. It writes the model name and each run's result to a JSON file
the pull request quotes. See [quickstart.md](../quickstart.md).
