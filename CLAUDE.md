# Hivemind

Hivemind is an MCP memory service for AI agents. Read
[CONTRIBUTING.md](CONTRIBUTING.md) before changing code, and
[.specify/memory/constitution.md](.specify/memory/constitution.md) for the
project rules each change must follow.

## Checks to run before a pull request

These are the public checks from [CONTRIBUTING.md](CONTRIBUTING.md):

```bash
uv sync --locked --dev
uv run pytest tests/ -q
uv run python scripts/check_doc_links.py
git diff --check
```

[docs/TESTING.md](docs/TESTING.md) documents the pytest markers and the
optional suites.

## Which skills to use

- Spec Kit skills (`/speckit-specify`, `/speckit-clarify`, `/speckit-plan`,
  `/speckit-tasks`, `/speckit-implement`) write and carry out a feature
  specification under `specs/`. Use them for any change in behaviour that
  needs a specification.
- gstack skills (for example `/review`, `/investigate`, `/qa`) review code,
  investigate a bug and test the result. Use them alongside Spec Kit, not
  instead of a specification.

## gstack (recommended)

This project uses [gstack](https://github.com/garrytan/gstack) for AI-assisted workflows.
Install it for the best experience:

```bash
git clone --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack
cd ~/.claude/skills/gstack && ./setup --team
```

Skills like /qa, /ship, /review, /investigate, and /browse become available after install.
Use /browse for all web browsing (Aside first, the bundled gstack browser as fallback). Use ~/.claude/skills/gstack/... for gstack file paths.
