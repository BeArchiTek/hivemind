"""Run a write phase to completion even while the calling task is cancelled.

MCP SDK 2 cancels a tool handler when the client disconnects (SSE mode) or
sends ``notifications/cancelled``. A write phase that must not stop half-way
(backup restore copy, compaction rollback) runs here in its own task, shielded
from that cancellation; once it has finished, the cancellation is re-raised so
the caller still observes ``asyncio.CancelledError``.

The SDK cancels through an anyio cancel scope, which re-delivers the
cancellation at every later checkpoint until the scope exits. Both helpers
therefore absorb any number of cancellations while the protected task runs,
and never cancel that task themselves.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


async def run_shielded(
    coro: Coroutine[Any, Any, T],
) -> tuple[T, asyncio.CancelledError | None]:
    """Run ``coro`` to completion and report a cancellation instead of raising it.

    Returns ``(result, cancelled)``: ``cancelled`` is the first
    ``asyncio.CancelledError`` delivered to the caller while the protected
    task ran, or ``None``. The caller decides what to record before it
    propagates that cancellation; the protected work has completed either way.

    Raises ``asyncio.CancelledError`` only when the protected work did NOT
    complete normally while a cancellation was pending: when the protected
    coroutine raised (the cancellation is raised ``from`` that exception) or
    when it was itself cancelled by someone else. Without a pending
    cancellation, the coroutine's own exception is raised unchanged.
    """
    task = asyncio.ensure_future(coro)
    pending_cancel: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as cancelled:
            if task.cancelled():
                # The protected coroutine itself ended with a cancellation
                # (never ours): surface it unchanged.
                raise
            if pending_cancel is None:
                pending_cancel = cancelled
        except BaseException:
            # The protected coroutine raised: ``asyncio.shield`` relays its
            # exception. It is read back from the task below so that a
            # pending cancellation still wins.
            if not task.done():  # pragma: no cover - shield only raises once done
                raise
    if task.cancelled():
        task.result()  # raises the task's own CancelledError
    exc = task.exception()
    if pending_cancel is not None:
        if exc is not None:
            raise pending_cancel from exc
        return task.result(), pending_cancel
    if exc is not None:
        raise exc
    return task.result(), None


async def await_shielded(coro: Coroutine[Any, Any, T]) -> T:
    """Await ``coro`` to completion; defer any cancellation until it has ended.

    The coroutine runs in a dedicated task that is never cancelled by this
    function. A cancellation delivered to the caller while waiting is
    remembered, the wait resumes, and ``asyncio.CancelledError`` is re-raised
    only after the task has finished (``from`` the task's exception when it
    failed). The task's own result or exception is returned/raised when no
    cancellation was requested.
    """
    result, cancelled = await run_shielded(coro)
    if cancelled is not None:
        raise cancelled
    return result
