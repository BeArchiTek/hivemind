"""F2 — graph push must not run synchronous ``memory_ingest`` under a deadline.

Under MCP SDK 2 a per-call client deadline cancels the server-side ingest, after
the previous mirror copy was already deleted. The push must submit each bank
file with ``memory_ingest_async`` (Graph Memory tool contract: ``source_path``
and ``sha256`` are required), poll ``ingest_job_status`` until a terminal
state, and never lose the previous mirror copy when the new ingest fails.

Driven through the real :class:`GraphBridgeService` over the deterministic
:class:`FakeGraphTransport`.
"""

from __future__ import annotations

import base64
import hashlib
import json
from unittest.mock import patch

from live_mem.core.graph_bridge import GraphBridgeService
from tests.fakes import (
    FakeGraphTransport,
    validate_fake_graph_url,
)
from tests.fakes import (
    GraphLongFakeStorage as FakeStorage,
)

SPACE = "space-f2"
MEM = "mem-f2"
URL = "https://gm.example.com"
TERMINAL = {"succeeded", "failed", "cancelled", "skipped", "changed_skipped"}


def _meta_connected() -> dict:
    return {
        "space_id": SPACE,
        "version": 1,
        "graph_memory": {
            "url": URL,
            "token": "tok-secret",
            "memory_id": MEM,
            "ontology": "general",
            "last_push": None,
            "push_count": 0,
            "files_pushed": 0,
            # Both files were mirrored by a previous push.
            "bank_mirror": ["good.md", "bad.md"],
        },
    }


class _JobBoard:
    """Canned Graph Memory async-ingest behaviour keyed by filename."""

    def __init__(self, outcomes: dict[str, list[str]]) -> None:
        # filename -> successive ``ingest_job_status`` statuses (last repeats).
        self._outcomes = outcomes
        self._jobs: dict[str, str] = {}  # job_id -> filename
        self._polls: dict[str, list[str]] = {}

    def submit(self, arguments: dict) -> dict:
        job_id = f"job-{arguments['filename']}"
        self._jobs[job_id] = arguments["filename"]
        return {"status": "queued", "job_id": job_id, "queue_position": 1}

    def status(self, arguments: dict) -> dict:
        job_id = arguments.get("job_id")
        filename = self._jobs.get(job_id)
        if filename is None:
            return {"status": "not_found", "message": f"Job '{job_id}' not found"}
        sequence = self._outcomes[filename]
        polls = self._polls.setdefault(job_id, [])
        status = sequence[min(len(polls), len(sequence) - 1)]
        polls.append(status)
        payload = {
            "status": status,
            "job_id": job_id,
            "memory_id": MEM,
            "filename": filename,
            "source_path": f"bank/{filename}",
        }
        if status == "succeeded":
            payload["document_id"] = f"new-{filename}"
        if status == "failed":
            payload["error"] = "LLM extraction failed"
        return payload


def _responses(board: _JobBoard) -> dict:
    return {
        "document_list": {
            "status": "ok",
            "documents": [
                {"id": "old-good", "filename": "good.md", "source_path": None},
                {"id": "old-bad", "filename": "bad.md", "source_path": None},
            ],
        },
        "memory_ingest_async": board.submit,
        "ingest_job_status": board.status,
        # A synchronous ingest must never be attempted; make it loud if it is.
        "memory_ingest": {
            "status": "error",
            "message": "synchronous memory_ingest is forbidden on push",
        },
    }


async def _run_push(
    board: _JobBoard, *, wait_budget_seconds: float | None = None
) -> tuple[dict, FakeGraphTransport]:
    storage = FakeStorage()
    await storage.put_json(f"{SPACE}/_meta.json", _meta_connected())
    await storage.put(f"{SPACE}/bank/good.md", "# good\n")
    await storage.put(f"{SPACE}/bank/bad.md", "# bad\n")
    factory = FakeGraphTransport.factory(responses=_responses(board))
    bridge = GraphBridgeService(
        client_factory=factory,
        url_validator=validate_fake_graph_url,
    )
    if wait_budget_seconds is not None:
        bridge.push_ingest_timeout_seconds = wait_budget_seconds
        bridge.push_poll_interval_seconds = 0.0
    with patch("live_mem.core.graph_bridge.get_storage", return_value=storage):
        result = await bridge.push(SPACE)
    calls = [call for inst in factory.instances for call in inst.calls]
    return result, calls


def _last_index_of(calls, predicate) -> int:
    matches = [index for index, call in enumerate(calls) if predicate(call)]
    assert matches, "expected call not found"
    return matches[-1]


async def test_push_submits_async_jobs_and_polls_until_terminal():
    board = _JobBoard(
        {
            "good.md": ["running", "running", "succeeded"],
            "bad.md": ["running", "failed"],
        }
    )
    result, calls = await _run_push(board)
    names = [c.tool_name for c in calls]

    assert "memory_ingest" not in names, names
    submits = [c for c in calls if c.tool_name == "memory_ingest_async"]
    assert sorted(c.arguments["filename"] for c in submits) == ["bad.md", "good.md"]
    for call in submits:
        content = base64.b64decode(call.arguments["content_base64"])
        assert call.arguments["memory_id"] == MEM
        assert call.arguments["sha256"] == hashlib.sha256(content).hexdigest()
        assert call.arguments.get("source_path"), call.arguments

    polls = [c for c in calls if c.tool_name == "ingest_job_status"]
    assert {c.arguments["job_id"] for c in polls} == {"job-good.md", "job-bad.md"}
    # Each job was polled until it reported a terminal state.
    assert board._polls["job-good.md"][-1] == "succeeded"
    assert board._polls["job-bad.md"][-1] == "failed"

    assert result["status"] == "ok", result
    assert result["pushed_files"] == ["good.md"]
    assert result["pushed"] == 1


async def test_push_keeps_previous_mirror_copy_when_new_ingest_fails():
    board = _JobBoard({"good.md": ["succeeded"], "bad.md": ["running", "failed"]})
    result, calls = await _run_push(board)

    deletes = [c for c in calls if c.tool_name == "document_delete"]
    # The old copy of a file whose new ingest failed is still in LONG.
    assert "old-bad" not in {c.arguments.get("document_id") for c in deletes}, deletes

    # A successful file may have its old copy removed, but only after the job
    # reported success (never before the new content is durably ingested).
    good_deletes = [
        i
        for i, c in enumerate(calls)
        if c.tool_name == "document_delete"
        and c.arguments.get("document_id") == "old-good"
    ]
    if good_deletes:
        # The last poll of job-good.md is the one that returned "succeeded".
        last_good_poll = _last_index_of(
            calls,
            lambda c: (
                c.tool_name == "ingest_job_status"
                and c.arguments.get("job_id") == "job-good.md"
            ),
        )
        assert min(good_deletes) > last_good_poll, (good_deletes, last_good_poll)

    # The failed file is reported as failed/pending, not as pushed, and it
    # stays a candidate for the next push.
    assert "bad.md" not in result["pushed_files"]
    assert result.get("errors", 0) >= 1 or result.get("pending", 0) >= 1, result
    reported = json.dumps({k: v for k, v in result.items() if k != "pushed_files"})
    assert "bad.md" in reported, result
    assert result["pushed"] == 1


async def test_push_never_issues_memory_ingest_even_when_nothing_is_mirrored():
    board = _JobBoard({"good.md": ["succeeded"], "bad.md": ["succeeded"]})
    result, calls = await _run_push(board)
    names = [c.tool_name for c in calls]
    assert "memory_ingest" not in names, names
    assert names.count("memory_ingest_async") == 2
    assert result["status"] == "ok"
    assert sorted(result["pushed_files"]) == ["bad.md", "good.md"]
    assert result["errors"] == 0


async def test_push_reports_unfinished_job_as_pending_and_keeps_old_copy():
    """A job still running when the wait budget ends is pending, not lost."""
    board = _JobBoard({"good.md": ["succeeded"], "bad.md": ["running"]})
    result, calls = await _run_push(board, wait_budget_seconds=0.0)

    assert result["status"] == "ok", result
    assert result["pushed_files"] == ["good.md"]
    assert result["pending"] == 1
    assert result["pending_files"] == ["bad.md"]
    assert result["pending_details"] == [{"filename": "bad.md", "job_id": "job-bad.md"}]
    assert result["errors"] == 0
    deleted = {
        c.arguments.get("document_id") for c in calls if c.tool_name == "document_delete"
    }
    # The previous copy of the unfinished file is still in LONG; the finished
    # file's legacy copy was removed after its job succeeded.
    assert "old-bad" not in deleted, deleted
    assert "old-good" in deleted, deleted
    # The file stays in the mirror ledger, so the next push re-checks it.
    assert "bad.md" in json.dumps(result)
