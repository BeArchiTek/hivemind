# Hivemind Constitution

These principles are taken from the repository's own documents. Each principle
names the file and lines it comes from. Where a document does not state a
rule, this constitution does not add one.

## Core Principles

### I. Fail closed on ambiguous state

Missing, ambiguous or corrupted critical Hivemind state is refused; it is never
treated as a local space (`docs/ARCHITECTURE_CONTRACTS.md:19`, ADR-0008). The
tool exposure registry is validated at startup and any ambiguity stops the
server (`src/live_mem/tools/exposure.py:775-786`). Process lifecycle failures
are fail-closed, not self-healing (`docs/SECURITY.md:433`). A new behaviour
that cannot prove its state is safe must refuse, not guess.

### II. Every tool handler enforces its own authorization

The exposure registry is "an exposure and rendering contract, not an
authorization source. Every handler still enforces its own fresh call-time
guards" (`docs/TOOL_EXPOSURE.md:5-6`). Hiding a tool name from discovery is
not authorization (`docs/MCP_TOOLS_SPEC.md:76-78`). The permission model is
the four-level hierarchy `admin ⊃ manage ⊃ write ⊃ read`
(`docs/MCP_TOOLS_SPEC.md:125-130`); `manage` is a transitive provisioning
role and routine agents should use `read,write` tokens
(`docs/ARCHITECTURE_CONTRACTS.md:33`, ADR-0022; `SECURITY.md:109-114`).

### III. The tool surface is a frozen, deliberate contract

`short`, `mid` and `long` are the canonical memory tiers; historical `live_*`,
`bank_*` and `graph_*` names stay callable as compatibility aliases until an
explicitly versioned removal (`docs/ARCHITECTURE_CONTRACTS.md:13,16`,
ADR-0002 and ADR-0005). Any add, remove or rename of a public tool, any
permission change, and any change to the `short_note` token-equals-agent
contract must update `tests/fixtures/tool_surface.json` and be justified in
the pull request (`tests/test_mcp_tool_surface.py:27-35`). Every alias shares
its handler and all metadata, including the description, with its canonical
name (`src/live_mem/tools/exposure.py:943-954`).

### IV. Tool results are structured and never leak internals

Every tool returns a `dict` with a `status` field; the documented values are
`ok`, `error`, `created`, `deleted`, `not_found`, `forbidden`, `queued` and
`partial` (`docs/MCP_TOOLS_SPEC.md:108-121`). Unexpected exceptions are
rendered by `safe_error`, which returns the exception text only when
`MCP_SERVER_DEBUG` is set and otherwise returns "Internal server error"
(`src/live_mem/auth/context.py:415-438`). Every registered tool and every
parameter carries a non-empty description that passes a public-safety check
(`tests/test_mcp_tool_surface.py:581-603`).

### V. Agents write short notes; only consolidation writes the bank

Agents write atomic notes with `short_note`, never write directly to the bank,
and at session end call `mid_consolidate` at most once, then return to the
user without polling (`RULES/live-mem.standard.memory.bank.md:126-143`;
`docs/AGENT_MEMORY_SETUP.md:140-157`). At session start an agent reads
`space_rules`, `mid_read_all` and `short_read`, and stops before any mutation
if a startup call fails (`docs/AGENT_MEMORY_SETUP.md:110-121`). Long memory is
derived and non-authoritative (`docs/ARCHITECTURE_CONTRACTS.md:21`, ADR-0010).
Rules are prompt instructions and must not contain secrets or regulated data
(`RULES/README.md:16-19`).

### VI. A behaviour change ships with a failing-then-passing test

For a behaviour change, add a focused regression that fails without the fix
and passes with it; where feasible, prove a safety guard by weakening it and
showing the test fails; never delete cases, lower expectations or count skips
as proof (`docs/TESTING.md:119-124`). Every test receives exactly one primary
marker from its path (`docs/TESTING.md:30-43`). The nominal suite runs with no
production credentials (`CONTRIBUTING.md:52-55`).

### VII. Security findings go through the private channel

Suspected vulnerabilities are reported through the private GitHub
vulnerability-report form, never in a public issue or pull request
(`SECURITY.md:15-23`). Tokens, endpoints and secrets never appear in project
instructions, notes, long memory, commits or logs
(`docs/AGENT_MEMORY_SETUP.md:154-156`).

## Development workflow

The public `main` branch is a released source snapshot; public issues and pull
requests are community intake for a future release (`CONTRIBUTING.md:5-8`).
Before a pull request, run `uv sync --locked --dev`, `uv run pytest tests/ -q`,
`uv run python scripts/check_doc_links.py` and `git diff --check`
(`CONTRIBUTING.md:26-33`). A pull request includes a concise summary,
reproducible evidence, the commands run, tests for behaviour changes and
documentation updates (`CONTRIBUTING.md:70-72`).

## Governance

This constitution restates the sources above; when it disagrees with them, the
sources win and the constitution is corrected. Specifications written under
`specs/` cite these principles by number. Amendments name the source document
that changed.

**Version**: 1.0.0 | **Ratified**: 2026-10-12 | **Last Amended**: 2026-10-12
