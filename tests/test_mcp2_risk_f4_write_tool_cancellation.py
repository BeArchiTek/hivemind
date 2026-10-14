"""F4 — cancelling the request must not leave write tools half-done.

Under MCP SDK 2 a client disconnect (SSE mode) or ``notifications/cancelled``
cancels the handler's anyio scope. anyio re-delivers the cancellation at every
later checkpoint until the scope exits, so an unshielded "finish the copy" or
"roll back" phase is interrupted at its first storage call.

- backup_restore: after a cancellation during the copy phase the target space
  is either complete (every backup object present) or absent, so a retry of the
  same restore succeeds.
- bank_compact apply: the bank ends fully compacted or fully rolled back; the
  rollback is never cancelled; no ``recovery_required`` /
  ``compaction_rollback_cancelled`` outcome is produced by the cancellation.
- ``asyncio.CancelledError`` still propagates after the protected phase.

Cancellation is injected from a fake storage at a deterministic call (no
sleeps) through an anyio cancel scope, which is the SDK's mechanism. A raw
``Task.cancel()`` is not used: SDK 2 never delivers one to a handler, and anyio
shields do not block it.
"""

from __future__ import annotations

import asyncio

import anyio

from live_mem.core.backup import BackupService
from live_mem.core.write_sink import DirectLocalWriteSink
from tests.fakes.backup_storage import CopyFakeStorage, patch_backup_storage
from tests.test_backup_restore_hivemind import (
    BACKUP_ID,
    BACKUP_PREFIX,
    SPACE,
    _seed_backup,
)
from tests.test_consolidator_compaction import (
    CompactionStorage,
    _prepared_plan_details,
    make_service,
)


class _Canceller:
    """Cancels the operation under test the way MCP SDK 2 does: via its anyio scope."""

    def __init__(self) -> None:
        self.scope: anyio.CancelScope | None = None
        self.fired = False

    async def fire(self) -> None:
        self.fired = True
        assert self.scope is not None
        self.scope.cancel()
        # The cancellation lands on this checkpoint unless the phase is
        # shielded, in which case the storage call simply proceeds.
        await asyncio.sleep(0)

    async def run(self, operation) -> tuple[bool, object]:
        """Run ``operation()`` in a task under a cancel scope.

        Returns ``(cancellation_seen, returned)``. The scope stays cancelled
        after a shielded phase, so a normal return is followed by a checkpoint:
        the cancellation then reaches the caller there, exactly as it would in
        the SDK's handler task.
        """
        returned: list[object] = []

        async def runner():
            with anyio.CancelScope() as scope:
                self.scope = scope
                returned.append(await operation())
                await asyncio.sleep(0)
            return scope.cancelled_caught

        async with asyncio.timeout(5):
            seen = await asyncio.create_task(runner())
        assert self.fired, "the cancellation hook never ran"
        return seen, (returned[0] if returned else None)


# ──────────────────────────────────────────────────────────────────────────
# backup_restore
# ──────────────────────────────────────────────────────────────────────────


class _CancelOnSecondCopyStorage(CopyFakeStorage):
    def __init__(self, canceller: _Canceller) -> None:
        super().__init__()
        self.canceller = canceller
        self.copies = 0

    async def copy_object(self, source_key: str, dest_key: str) -> None:
        await asyncio.sleep(0)
        self.copies += 1
        if self.copies == 2:
            await self.canceller.fire()
        await super().copy_object(source_key, dest_key)


async def test_backup_restore_cancelled_during_copy_is_all_or_nothing(monkeypatch):
    canceller = _Canceller()
    storage = _CancelOnSecondCopyStorage(canceller)
    total = await _seed_backup(storage)
    assert total >= 3
    patch_backup_storage(monkeypatch, storage)
    expected = {
        f"{SPACE}/{key[len(BACKUP_PREFIX) :]}"
        for key in storage.objects
        if key.startswith(BACKUP_PREFIX)
    }

    seen, returned = await canceller.run(lambda: BackupService().restore(BACKUP_ID))

    assert seen is True, "CancelledError did not propagate to the caller"
    if returned is not None:
        # A completed shielded copy may report success, never an error.
        assert returned.get("status") == "ok", returned
    present = {key for key in storage.objects if key.startswith(f"{SPACE}/")}
    assert present in (expected, set()), (
        f"partially restored space: {len(present)} of {len(expected)} objects"
    )

    if not present:
        retry = await BackupService().restore(BACKUP_ID)
        assert retry["status"] == "ok", retry
        assert {
            key for key in storage.objects if key.startswith(f"{SPACE}/")
        } == expected


# ──────────────────────────────────────────────────────────────────────────
# bank_compact apply
# ──────────────────────────────────────────────────────────────────────────


class _CancelBeforeSecondBankPutStorage(CompactionStorage):
    """Every storage call suspends, so SDK-style cancellation can interrupt it."""

    def __init__(self, canceller: _Canceller) -> None:
        super().__init__()
        self.canceller = canceller
        self.bank_puts = 0

    async def get(self, key: str):
        await asyncio.sleep(0)
        return await super().get(key)

    async def put(
        self, key: str, content: str, content_type: str = "text/plain"
    ) -> None:
        await asyncio.sleep(0)
        if key.startswith("space-a/bank/"):
            self.bank_puts += 1
            if self.bank_puts == 2:
                await self.canceller.fire()
        await super().put(key, content, content_type)

    async def copy_object(self, source_key: str, dest_key: str) -> None:
        await asyncio.sleep(0)
        await super().copy_object(source_key, dest_key)


async def test_bank_compact_apply_cancelled_mid_apply_is_all_or_nothing():
    canceller = _Canceller()
    storage = _CancelBeforeSecondBankPutStorage(canceller)
    originals = {"space-a/bank/a.md": "a" * 120, "space-a/bank/b.md": "b" * 120}
    storage.objects = dict(originals)
    service = make_service(max_size=100)

    async def planner(filename, content, max_size, rules):
        return filename[0] * 60, _prepared_plan_details()

    service._plan_single_file_compaction = planner
    batch, failures = await service._prepare_compaction_batch(
        "space-a", await storage.list_and_get("space-a/bank/"), "# Rules"
    )
    assert failures == ()
    assert batch is not None
    compacted = {t.target_key: t.result for t in batch.targets}
    assert set(compacted) == set(originals)

    seen, returned = await canceller.run(
        lambda: service._apply_prepared_compaction_batch(
            "space-a", batch, DirectLocalWriteSink(storage)
        )
    )

    assert seen is True, "CancelledError did not propagate to the caller"
    if returned is not None:
        # A completed shielded apply may report success; never a recovery state.
        assert returned.get("status") == "ok", returned
        assert not returned.get("recovery_required"), returned
    bank = {key: storage.objects.get(key) for key in originals}
    assert bank in (originals, compacted), f"bank left half-compacted: {bank}"


async def test_bank_compact_cancelled_mid_apply_reports_completed_rollback():
    """A rollback that ran to completion is reported as such on the cancellation.

    anyio re-delivers the cancellation while the shielded rollback runs; that
    must not turn a completed rollback into ``compaction_rollback_cancelled``
    (which the MID queue reads as ``recovery_required``).
    """
    canceller = _Canceller()
    storage = _CancelBeforeSecondBankPutStorage(canceller)
    originals = {"space-a/bank/a.md": "a" * 120, "space-a/bank/b.md": "b" * 120}
    storage.objects = dict(originals)
    service = make_service(max_size=100)

    async def planner(filename, content, max_size, rules):
        return filename[0] * 60, _prepared_plan_details()

    service._plan_single_file_compaction = planner
    batch, failures = await service._prepare_compaction_batch(
        "space-a", await storage.list_and_get("space-a/bank/"), "# Rules"
    )
    assert failures == ()
    assert batch is not None
    captured: list[asyncio.CancelledError] = []

    async def apply():
        try:
            return await service._apply_prepared_compaction_batch(
                "space-a", batch, DirectLocalWriteSink(storage)
            )
        except asyncio.CancelledError as error:
            captured.append(error)
            raise

    seen, returned = await canceller.run(apply)
    assert seen is True
    if returned is not None:
        assert returned.get("status") == "ok", returned
        return
    assert captured, "the apply neither returned nor raised CancelledError"
    rollback = getattr(captured[0], "compaction_rollback_failures", None)
    assert rollback == (), rollback
    assert {key: storage.objects.get(key) for key in originals} == originals


# ──────────────────────────────────────────────────────────────────────────
# Shield helper semantics
# ──────────────────────────────────────────────────────────────────────────


async def test_shield_keeps_cancellation_when_protected_work_fails():
    """A failing protected phase does not swallow a pending cancellation."""
    from live_mem.core.cancel_shield import await_shielded

    started = asyncio.Event()

    async def failing_copy():
        started.set()
        await asyncio.sleep(0.05)
        raise RuntimeError("copy failed")

    async def handler():
        await await_shielded(failing_copy())

    task = asyncio.create_task(handler())
    await started.wait()
    task.cancel()
    try:
        async with asyncio.timeout(5):
            await task
    except asyncio.CancelledError as error:
        assert isinstance(error.__cause__, RuntimeError), error.__cause__
    else:
        raise AssertionError("CancelledError did not propagate")


async def test_run_shielded_reports_anyio_cancellation_after_completion():
    """Repeated anyio cancellations are absorbed; the work completes once."""
    from live_mem.core.cancel_shield import run_shielded

    log: list[str] = []

    async def work():
        log.append("start")
        for _ in range(3):
            await asyncio.sleep(0)
        log.append("done")
        return "result"

    outcome: list[object] = []

    async def body():
        with anyio.CancelScope() as scope:
            scope.cancel()
            outcome.append(await run_shielded(work()))
            await asyncio.sleep(0)  # the scope is still cancelled: lands here
        return scope.cancelled_caught

    async with asyncio.timeout(5):
        assert await asyncio.create_task(body()) is True
    assert log == ["start", "done"]
    result, cancelled = outcome[0]
    assert result == "result"
    assert isinstance(cancelled, asyncio.CancelledError)
