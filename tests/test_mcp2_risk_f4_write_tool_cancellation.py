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


# ──────────────────────────────────────────────────────────────────────────
# Interaction with the selective-maintenance barrier (1.6.6, #1420)
# ──────────────────────────────────────────────────────────────────────────

import uuid

import pytest

from live_mem.core import selective_maintenance as sm
from live_mem.core.storage import StorageService
from live_mem.tools.maintenance_gate import _wrap as maintenance_gate

OP = str(uuid.uuid4())


class _OwnedWrites:
    """Fake-storage mixin: every write acquires physical ownership.

    ``StorageService._run_write`` retains the caller's mutation ticket or
    admits the write; once the barrier has closed, a write that inherits no
    valid ticket is refused with ``maintenance_admission_closed``.
    """

    controller: sm.SelectiveController

    async def _owned(self, key: str, write):
        ticket = self.controller.acquire_physical(StorageService._write_scope(key))
        try:
            return await write()
        finally:
            self.controller.finish(ticket)


class _OwnedCancelOnSecondCopyStorage(_OwnedWrites, _CancelOnSecondCopyStorage):
    """The second copy closes the barrier, then cancels the handler."""

    def __init__(self, canceller: _Canceller, controller) -> None:
        super().__init__(canceller)
        self.controller = controller

    async def copy_object(self, source_key: str, dest_key: str) -> None:
        await asyncio.sleep(0)
        self.copies += 1
        if self.copies == 2:
            self.controller.pause(OP, ["*"], "background")
            await self.canceller.fire()
        await self._owned(dest_key, lambda: CopyFakeStorage.copy_object(self, source_key, dest_key))


class _OwnedCancelBeforeSecondBankPutStorage(_OwnedWrites, _CancelBeforeSecondBankPutStorage):
    """The second bank write closes the barrier for the space, then cancels."""

    def __init__(self, canceller: _Canceller, controller) -> None:
        super().__init__(canceller)
        self.controller = controller

    async def put(self, key: str, content: str, content_type: str = "text/plain") -> None:
        await asyncio.sleep(0)
        if key.startswith("space-a/bank/"):
            self.bank_puts += 1
            if self.bank_puts == 2:
                self.controller.pause(OP, ["space-a"], "background")
                await self.canceller.fire()
        await self._owned(key, lambda: CompactionStorage.put(self, key, content, content_type))

    async def delete(self, key: str) -> None:
        await asyncio.sleep(0)
        await self._owned(key, lambda: CompactionStorage.delete(self, key))

    async def copy_object(self, source_key: str, dest_key: str) -> None:
        await asyncio.sleep(0)
        await self._owned(dest_key, lambda: CompactionStorage.copy_object(self, source_key, dest_key))


def _assert_cancelled_owner_is_durable_unknown(controller) -> None:
    """The barrier keeps its conservative rule: the cancelled call is an
    unknown effect, durable until an operator clears it, even though the
    shielded phase completed. Nothing is left pending."""
    status = controller.status(OP)
    assert status["state"] == "recovery_required", status
    assert (status["pending"], status["unknown"]) == (0, 1), status
    with pytest.raises(sm.MaintenanceRefused, match="maintenance_unclean"):
        controller.resume(OP)


async def test_backup_restore_cancelled_under_the_gate_completes_through_a_closed_barrier(
    monkeypatch,
):
    """``backup_restore`` is gated as a global mutation (no space argument).

    Its copies run in a shielded task that must retain the handler's ticket:
    a barrier closed during the copy admits them and the space ends complete.
    """
    controller = sm.get_controller()
    canceller = _Canceller()
    storage = _OwnedCancelOnSecondCopyStorage(canceller, controller)
    total = await _seed_backup(storage)
    assert total >= 3
    patch_backup_storage(monkeypatch, storage)
    expected = {
        f"{SPACE}/{key[len(BACKUP_PREFIX) :]}"
        for key in storage.objects
        if key.startswith(BACKUP_PREFIX)
    }
    restore = maintenance_gate(BackupService().restore, "mutation", None)

    seen, returned = await canceller.run(lambda: restore(BACKUP_ID))

    assert seen is True, "CancelledError did not propagate to the caller"
    assert returned is None, returned
    present = {key for key in storage.objects if key.startswith(f"{SPACE}/")}
    assert present == expected, f"copies refused by the barrier: {len(present)} of {len(expected)}"
    _assert_cancelled_owner_is_durable_unknown(controller)


async def test_bank_compact_cancelled_under_the_gate_rolls_back_through_a_closed_barrier():
    """``bank_compact`` is gated on its space; the shielded rollback writes
    retain that ticket after the barrier closed on the space, so the bank is
    fully rolled back (never half-compacted) and the call is then unknown."""
    controller = sm.get_controller()
    canceller = _Canceller()
    storage = _OwnedCancelBeforeSecondBankPutStorage(canceller, controller)
    originals = {"space-a/bank/a.md": "a" * 120, "space-a/bank/b.md": "b" * 120}
    storage.objects = dict(originals)
    service = make_service(max_size=100)

    async def planner(filename, content, max_size, rules):
        return filename[0] * 60, _prepared_plan_details()

    service._plan_single_file_compaction = planner
    batch, failures = await service._prepare_compaction_batch(
        "space-a", await storage.list_and_get("space-a/bank/"), "# Rules"
    )
    assert failures == () and batch is not None
    apply = maintenance_gate(service._apply_prepared_compaction_batch, "mutation", "space_id")

    seen, returned = await canceller.run(
        lambda: apply("space-a", batch, DirectLocalWriteSink(storage))
    )

    assert seen is True, "CancelledError did not propagate to the caller"
    assert returned is None, returned
    assert {key: storage.objects.get(key) for key in originals} == originals
    _assert_cancelled_owner_is_durable_unknown(controller)
