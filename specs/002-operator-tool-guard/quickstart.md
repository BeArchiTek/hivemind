# Quickstart: verifying the operator tool guard

**Summary**: Run the feature's test module, then exercise the guard by hand
against a running server with the CLI.

## Prerequisites

- `uv sync --locked --dev`
- No production credentials; the tests use fakes.

## Automated verification

```bash
uv run pytest tests/test_operator_tool_guard.py -q -p no:cacheprovider
uv run pytest tests/ -q -p no:cacheprovider
uv run python scripts/check_doc_links.py
git diff --check
```

Expected: every test passes. The feature module covers the scenarios in
[spec.md](spec.md) (refusal of `mid_write` and `bank_write` over a real MCP
session, allowed operator token, legacy record, creation defaults, admin
update paths, `/api/tool`, CLI client, audit entries, unregistered names,
every operator entry and alias, the fixture block).

## Manual verification against a running server

1. Start the stack (`docs/DEPLOYMENT.md`, Quickstart) and export `MCP_URL`
   and an admin `MCP_TOKEN`.
2. Create a token for an agent and one for an operator:

   ```bash
   uv run python scripts/mcp_cli.py token create agent-worker -p read,write,manage --json
   uv run python scripts/mcp_cli.py token create ops-cli -p read,write,manage --operator-tools --json
   ```

   With an admin caller both go through `admin_create_token`; the first has
   `"operator_tools": true` unless `--no-operator-tools` is given, so for an
   agent token prefer `--no-operator-tools` or create it with a manager token
   (`token_create`), which always stores `false`.
3. With `MCP_TOKEN` set to the agent token, run
   `uv run python scripts/mcp_cli.py space export <space>`: the CLI shows the
   forbidden message. With the operator token the export is returned.
4. Change the attribute and retry without reconnecting:

   ```bash
   uv run python scripts/mcp_cli.py token update sha256:<hash> --operator-tools true
   ```

5. Read the server log: each refusal is one `live_mem.audit` line with
   `"event": "operator_tool_refused"`.
