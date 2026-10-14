"""Opt-in evaluation of the server instructions with a real model (SC-004).

The test skips unless ``HIVEMIND_AGENT_EVAL=1`` and the model credential
variables are set, so the nominal suite stays credential-free:

- ``HIVEMIND_AGENT_EVAL_API_URL``: an OpenAI-compatible chat completions
  endpoint (the ``/chat/completions`` path is appended when absent);
- ``HIVEMIND_AGENT_EVAL_API_KEY``: its bearer token;
- ``HIVEMIND_AGENT_EVAL_MODEL``: the model name.

The model receives only the rendered server instructions as its system
prompt and the ``write`` discovery profile as its tool list, against an
in-process ``HivemindMCPServer`` with a ``read,write`` token on space ``p``
backed by an in-memory storage double. Ten runs are performed; each run
must call ``space_rules``, ``mid_read_all`` and ``short_read`` before its
first ``short_note``, write notes only in the seven categories, call
``mid_consolidate`` exactly once, and not call ``bank_consolidation_status``
afterwards. The model name and each run's result are written to
``tests/bench/agent_evaluation_sc004.json`` (``tests/bench/`` is ignored by
git), which the pull request quotes.

Specification: ``specs/001-tool-guidance-remediation/spec.md`` (SC-004).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tests" / "bench" / "agent_evaluation_sc004.json"
RUNS = 10
MAX_TURNS = 20
TASK = (
    "You work in the memory space 'p'. Record in memory that the build passes "
    "after the dependency update and that the next step is to review the "
    "authentication module, then end this work block. The user has validated "
    "the work. Reply with the single word DONE when finished."
)
START_CALLS = ("space_rules", "mid_read_all", "short_read")


def _eval_settings() -> dict[str, str] | None:
    if os.environ.get("HIVEMIND_AGENT_EVAL") != "1":
        return None
    values = {
        key: os.environ.get(f"HIVEMIND_AGENT_EVAL_{key}", "")
        for key in ("API_URL", "API_KEY", "MODEL")
    }
    if not all(values.values()):
        return None
    return values


pytestmark = pytest.mark.optional


def _space_storage():
    from tests.test_lm2_11_access_roles import FakeStorage

    storage = FakeStorage()
    storage.objects["p/_meta.json"] = json.dumps(
        {"space_id": "p", "description": "evaluation space", "created_at": "2026-01-01T00:00:00Z"}
    )
    storage.objects["p/rules.md"] = (ROOT / "RULES" / "live-mem.standard.memory.bank.md").read_text(
        encoding="utf-8"
    )
    storage.objects["p/bank/activeContext.md"] = "# Active context\n\nCurrent focus: dependency update.\n"
    storage.objects["p/bank/progress.md"] = "# Progress\n\n- Dependency update started.\n"
    return storage


def _install_in_process_server(monkeypatch):
    from live_mem.core import consolidation_queue as queue_module
    from live_mem.core import engines, storage as storage_module
    from live_mem.core.write_sink import DirectLocalWriteSink

    storage = _space_storage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: storage)

    class Registry:
        async def resolve_sink(self, space_id):
            return DirectLocalWriteSink(storage)

        async def short_engine(self, space_id):
            engine = type("Engine", (), {})()
            engine.write_sink = DirectLocalWriteSink(storage)

            async def write_note(**kwargs):
                from live_mem.core.live import get_live_service

                return await get_live_service().write_note(**kwargs)

            engine.write_note = write_note
            return engine

    monkeypatch.setattr(engines, "get_engine_registry", lambda: Registry())

    async def idle_worker(self, space_id):
        return None

    monkeypatch.setattr(queue_module.ConsolidationQueueService, "_worker", idle_worker)
    queue_module.reset_consolidation_queue_for_tests()


async def _run_once(settings: dict[str, str], monkeypatch) -> dict:
    import httpx

    from live_mem.instructions import render_instructions
    from tests.test_p10_tool_exposure import _identity, _list_result
    from live_mem.auth.context import bind_mcp_request_identity
    from live_mem.tools import register_all_tools
    from live_mem.tools.exposure import HivemindMCPServer
    from tests.test_tool_guidance_replay_integration import call, payload_of

    _install_in_process_server(monkeypatch)
    mcp = HivemindMCPServer("agent-evaluation", middleware=[bind_mcp_request_identity])
    register_all_tools(mcp)
    token = {**_identity("write", allowed=("p",)), "permissions": ["read", "write"]}
    listed = await _list_result(mcp, token)
    tools = [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.inputSchema,
            },
        }
        for tool in listed.tools
    ]
    url = settings["API_URL"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    headers = {"Authorization": f"Bearer {settings['API_KEY']}"}
    messages = [
        {"role": "system", "content": render_instructions()},
        {"role": "user", "content": TASK},
    ]
    calls: list[tuple[str, dict]] = []
    async with httpx.AsyncClient(timeout=120) as client:
        for _turn in range(MAX_TURNS):
            response = await client.post(
                url,
                headers=headers,
                json={"model": settings["MODEL"], "messages": messages, "tools": tools},
            )
            response.raise_for_status()
            choice = response.json()["choices"][0]["message"]
            messages.append(choice)
            tool_calls = choice.get("tool_calls") or []
            if not tool_calls:
                break
            for tool_call in tool_calls:
                name = tool_call["function"]["name"]
                try:
                    arguments = json.loads(tool_call["function"].get("arguments") or "{}")
                except ValueError:
                    arguments = {}
                calls.append((name, arguments))
                result = await call(mcp, name, arguments, token)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(payload_of(result)),
                    }
                )
    return _judge(calls)


def _judge(calls: list[tuple[str, dict]]) -> dict:
    from live_mem.core.live import VALID_CATEGORIES

    names = [name for name, _ in calls]
    first_note = names.index("short_note") if "short_note" in names else len(names)
    before_note = set(names[:first_note])
    consolidations = [i for i, name in enumerate(names) if name == "mid_consolidate"]
    status_after = any(
        name == "bank_consolidation_status" and consolidations and i > consolidations[0]
        for i, name in enumerate(names)
    )
    categories_ok = all(
        arguments.get("category") in VALID_CATEGORIES for name, arguments in calls if name == "short_note"
    )
    checks = {
        "start_calls_before_first_note": all(c in before_note for c in START_CALLS),
        "notes_only_in_the_seven_categories": categories_ok and "short_note" in names,
        "exactly_one_mid_consolidate": len(consolidations) == 1,
        "no_status_call_after_consolidate": not status_after,
    }
    return {"calls": names, "checks": checks, "passed": all(checks.values())}


@pytest.mark.asyncio
async def test_agent_follows_the_instructions_in_ten_runs(monkeypatch):
    settings = _eval_settings()
    if settings is None:
        pytest.skip(
            "opt-in evaluation: set HIVEMIND_AGENT_EVAL=1 and the "
            "HIVEMIND_AGENT_EVAL_API_URL, _API_KEY and _MODEL variables"
        )
    results = []
    for run in range(RUNS):
        results.append({"run": run + 1, **(await _run_once(settings, monkeypatch))})
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps({"model": settings["MODEL"], "runs": results}, indent=2), encoding="utf-8"
    )
    failed = [r["run"] for r in results if not r["passed"]]
    assert not failed, f"runs {failed} did not follow the instructions; see {OUTPUT}"
