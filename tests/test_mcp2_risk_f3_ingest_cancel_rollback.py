"""F3 — the Graph Memory ingest pipeline rolls back on task cancellation.

``run_ingest_pipeline`` already rolls back on ``IngestCancelled`` and on any
``Exception``. Under MCP SDK 2 a cancelled request (client ``notifications/
cancelled`` or a dropped SSE connection) cancels the handler's anyio scope, so
``asyncio.CancelledError`` (a ``BaseException``) reaches the pipeline. The same
rollback must run to completion, shielded from the cancellation, and the
``CancelledError`` must still propagate to the caller.

The cancellation is delivered the way the SDK delivers it: an anyio cancel
scope cancelled from inside a pipeline step, which re-delivers the cancellation
at every later checkpoint until the scope exits. Every fake step yields to the
event loop so an unshielded rollback is interrupted exactly like the real one.

Graph Memory pipeline tests live in ``tests/`` (vendored ``mcp_memory`` on
``sys.path`` via ``tests/conftest.py``), next to ``test_long_ingest_async.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from hivemind_inference import EmbeddingResult
from tests.fakes.inference_fakes import apply_graph_memory_baseline_env

S3_URI = "s3://bucket/f3-candidate.md"
# 1.6.3+ binds the retained text to the document hash, which must be a real sha256.
DOC_HASH = hashlib.sha256(b"candidate bytes").hexdigest()


@pytest.fixture(autouse=True)
def _graph_memory_env(monkeypatch):
    apply_graph_memory_baseline_env(monkeypatch)


class _Harness:
    """Fake pipeline dependencies with a cancellation hook at one step."""

    def __init__(self, cancel_at: str) -> None:
        self.cancel_at = cancel_at
        self.scope: anyio.CancelScope | None = None
        self.rollback_calls: list[tuple] = []
        self.doc_ids: list[str] = []

        memory = MagicMock()
        memory.ontology = "general"

        self.graph = MagicMock()
        self.graph.get_memory = AsyncMock(return_value=memory)
        self.graph.add_document = self._step("graph_write", self._record_doc)
        self.graph.add_entities_and_relations = self._step(
            "graph_relations", lambda **_: {}
        )
        self.graph.update_document_ingestion = AsyncMock()
        self.graph.promote_candidate_document = AsyncMock()

        self.storage = MagicMock()
        self.storage.upload_document = AsyncMock(
            return_value={"uri": S3_URI, "size_bytes": 100}
        )
        self.storage.delete_document = self._rollback_step("delete_document")
        # 1.6.3+: the exact extracted text is retained beside the candidate and
        # removed with it; both are SDK writes owned by the pipeline.
        self.storage.put_extracted_text = AsyncMock(return_value=True)
        self.storage.delete_extracted_text = self._rollback_step("delete_extracted_text")

        from mcp_memory.core.models import ExtractedEntity, ExtractionResult

        extraction = ExtractionResult(
            entities=[ExtractedEntity(name="E1", type="Concept", description="D1")],
            relations=[],
            summary="S",
            key_topics=[],
        )
        self.extractor = MagicMock()
        self.extractor.extract_with_ontology_chunked = self._step(
            "llm_extract", lambda *_a, **_k: extraction
        )

        # A real passage of the extracted text: 1.6.3+ annotates each chunk
        # with its source placement before embedding.
        from mcp_memory.core.models import Chunk

        chunk = Chunk(text="some text", index=0, total_chunks=1,
                      source_spans=[{"start": 0, "end": len("some text")}])
        self.chunker = MagicMock()
        self.chunker.chunk_document = MagicMock(return_value=[chunk])

        self.embedder = MagicMock()
        self.embedder.embed_texts_result = self._step(
            "embedding",
            lambda *_a, **_k: EmbeddingResult(
                vectors=((0.1,) * 8,),
                configured_model="mock",
                model_evidence="configured_only",
                effective_dimensions=8,
            ),
        )

        self.vector = MagicMock()
        self.vector.store_chunks = self._step("vector_store", lambda **_: 1)

    def _record_doc(self, **kwargs):
        self.doc_ids.append(kwargs["doc_id"])

    def _step(self, name: str, produce):
        async def step(*args, **kwargs):
            await asyncio.sleep(0)
            if name == self.cancel_at:
                assert self.scope is not None
                self.scope.cancel()
                await asyncio.sleep(0)  # the SDK-style cancellation lands here
            return produce(*args, **kwargs)

        return step

    def _rollback_step(self, name: str):
        async def step(*args, **kwargs):
            # A real S3/Neo4j/Qdrant call suspends; an unshielded rollback is
            # cancelled right here under SDK-style cancellation.
            await asyncio.sleep(0)
            self.rollback_calls.append((name, args, kwargs))
            return True

        return step

    async def delete_document_everywhere(self, memory_id, doc_id, **kwargs):
        await asyncio.sleep(0)
        self.rollback_calls.append(
            ("delete_document_everywhere", memory_id, doc_id, kwargs)
        )
        return {
            "neo4j_deleted": True,
            "qdrant_chunks_deleted": 1,
            "s3_deleted": True,
            "errors": [],
        }

    def patches(self):
        base = "mcp_memory.core.ingest_pipeline."
        return [
            patch(base + "_graph", return_value=self.graph),
            patch(base + "_storage", return_value=self.storage),
            patch(base + "_extractor", return_value=self.extractor),
            patch(base + "_chunker", return_value=self.chunker),
            patch(base + "_embedder", return_value=self.embedder),
            patch(base + "_vector_store", return_value=self.vector),
            patch(
                base + "delete_document_everywhere",
                side_effect=self.delete_document_everywhere,
            ),
            patch("mcp_memory.server._extract_text", return_value="some text"),
        ]


async def _run_cancelled(harness: _Harness, *, bind=None) -> tuple[bool, object]:
    """Run the pipeline in a task under an anyio cancel scope.

    Returns ``(cancelled_caught, returned_value)``: a pipeline that swallows the
    cancellation returns a value and leaves ``cancelled_caught`` False.
    ``bind`` runs first inside the task, like the ingest worker's own setup.
    """
    from mcp_memory.core.ingest_pipeline import run_ingest_pipeline

    returned: list[object] = []

    async def runner():
        if bind is not None:
            bind()
        with anyio.CancelScope() as scope:
            harness.scope = scope
            returned.append(
                await run_ingest_pipeline(
                    memory_id="test-mem",
                    content=b"candidate bytes",
                    filename="doc.md",
                    doc_hash=DOC_HASH,
                    source_path="docs/doc.md",
                )
            )
        return scope.cancelled_caught

    patches = harness.patches()
    for p in patches:
        p.start()
    try:
        async with asyncio.timeout(5):
            cancelled_caught = await asyncio.create_task(runner())
    finally:
        for p in reversed(patches):
            p.stop()
    return cancelled_caught, (returned[0] if returned else None)


async def test_cancel_after_upload_removes_s3_object_and_propagates():
    harness = _Harness(cancel_at="llm_extract")
    cancelled_caught, returned = await _run_cancelled(harness)

    assert returned is None, f"CancelledError was swallowed: {returned!r}"
    assert cancelled_caught is True
    assert ("delete_document", ("test-mem", S3_URI), {}) in harness.rollback_calls, (
        harness.rollback_calls
    )


@pytest.mark.parametrize("cancel_at", ["graph_relations", "vector_store"])
async def test_cancel_after_document_node_rolls_back_everywhere_and_propagates(
    cancel_at,
):
    harness = _Harness(cancel_at=cancel_at)
    cancelled_caught, returned = await _run_cancelled(harness)

    assert returned is None, f"CancelledError was swallowed: {returned!r}"
    assert cancelled_caught is True
    assert harness.doc_ids, "the running Document node was never created"
    doc_id = harness.doc_ids[0]
    everywhere = [
        c for c in harness.rollback_calls if c[0] == "delete_document_everywhere"
    ]
    assert everywhere, harness.rollback_calls
    assert everywhere[0][1:3] == ("test-mem", doc_id)
    assert everywhere[0][3].get("uri") == S3_URI
    if cancel_at == "vector_store":
        assert everywhere[0][3].get("delete_vectors") is True


async def test_ingest_cancelled_and_exception_paths_are_unchanged():
    """Regression guard: the existing non-BaseException paths keep returning dicts."""
    from mcp_memory.core.ingest_pipeline import IngestCancelled, run_ingest_pipeline

    harness = _Harness(cancel_at="never")
    harness.extractor.extract_with_ontology_chunked = AsyncMock(
        side_effect=RuntimeError("LLM 500 error")
    )
    patches = harness.patches()
    for p in patches:
        p.start()
    try:
        res = await run_ingest_pipeline(
            memory_id="test-mem",
            content=b"candidate bytes",
            filename="doc.md",
            doc_hash=DOC_HASH,
            source_path="docs/doc.md",
        )
        assert res["status"] == "error"
        assert ("delete_document", ("test-mem", S3_URI), {}) in harness.rollback_calls

        harness.rollback_calls.clear()
        flag = {"cancel": False}
        extraction_step = _Harness(
            cancel_at="never"
        ).extractor.extract_with_ontology_chunked

        async def extract_then_request_cancel(*args, **kwargs):
            flag["cancel"] = True  # observed by cancel_check at the next phase boundary
            return await extraction_step(*args, **kwargs)

        harness.extractor.extract_with_ontology_chunked = extract_then_request_cancel
        res = await run_ingest_pipeline(
            memory_id="test-mem",
            content=b"candidate bytes",
            filename="doc.md",
            doc_hash=DOC_HASH,
            source_path="docs/doc.md",
            cancel_check=lambda: flag["cancel"],
        )
        assert res["status"] == "cancelled", res
        # A candidate doc_id already exists at that boundary, so the existing
        # rollback removes the candidate everywhere (S3 object included).
        assert any(S3_URI in repr(call) for call in harness.rollback_calls), (
            harness.rollback_calls
        )
    finally:
        for p in reversed(patches):
            p.stop()
    assert issubclass(IngestCancelled, Exception)


# ──────────────────────────────────────────────────────────────────────────
# Interaction with the selective-maintenance barrier (1.6.6, #1420)
# ──────────────────────────────────────────────────────────────────────────

import uuid

from mcp_memory.core import selective_maintenance as graph_sm


class _OwnedHarness(_Harness):
    """The cancelling step also closes the barrier; the rollback owns its writes.

    The real S3, Neo4j and Qdrant adapters acquire physical ownership around
    every SDK call (``StorageService._write_call``, ``mutation_session``): a
    call that does not retain the job's ticket is refused once the barrier has
    closed. The rollback double does the same acquisition.
    """

    def __init__(self, cancel_at: str, controller, operation: str) -> None:
        self.controller = controller
        self.operation = operation
        super().__init__(cancel_at)

    def _step(self, name: str, produce):
        inner = super()._step(name, produce)

        async def step(*args, **kwargs):
            if name == self.cancel_at:
                self.controller.pause(self.operation, ["*"], "background")
            return await inner(*args, **kwargs)

        return step

    async def delete_document_everywhere(self, memory_id, doc_id, **kwargs):
        with self.controller.physical(memory_id):
            return await super().delete_document_everywhere(memory_id, doc_id, **kwargs)


async def test_cancelled_pipeline_rolls_back_under_the_workers_maintenance_ticket():
    """The shielded rollback completes through a closed barrier, then the job
    settles as a durable unknown (the barrier's rule for a cancelled owner).

    The ingest worker binds the ticket taken at acceptance and runs the
    pipeline under it. The rollback that a cancellation triggers runs in its
    own shielded task, so it must still find that ticket active and retain
    it; the worker then records the cancelled job as unknown exactly as
    ``IngestQueueService._on_worker_cancelled`` does.
    """
    controller = graph_sm.get_controller()
    operation = str(uuid.uuid4())
    harness = _OwnedHarness("vector_store", controller, operation)
    ticket = controller.hold("mutation", "test-mem")  # acceptance, before the worker

    cancelled_caught, returned = await _run_cancelled(
        harness, bind=lambda: controller.bind(ticket)
    )

    assert returned is None, f"CancelledError was swallowed: {returned!r}"
    assert cancelled_caught is True
    everywhere = [
        c for c in harness.rollback_calls if c[0] == "delete_document_everywhere"
    ]
    assert everywhere and everywhere[0][2] == harness.doc_ids[0], harness.rollback_calls
    # The rollback's physical ownership retained the job ticket, so the
    # accepted job is still the only pending owner of the paused scope.
    status = controller.status(operation)
    assert status["state"] == "background_draining"
    assert (status["pending"], status["unknown"]) == (1, 0)

    controller.finish(ticket, unknown=True)  # what the cancelled worker records
    status = controller.status(operation)
    assert status["state"] == "recovery_required"
    assert (status["pending"], status["unknown"]) == (0, 1)
    with pytest.raises(graph_sm.MaintenanceRefused, match="maintenance_unclean"):
        controller.resume(operation)
