from __future__ import annotations

import asyncio

import pytest

from freeaiproxy.engine.errors import QueueFullError
from freeaiproxy.services.queue import RequestQueue


async def test_runs_one_at_a_time():
    q = RequestQueue(max_len=10)
    order: list[str] = []
    running = 0
    max_parallel = 0

    async def job(name: str, delay: float) -> None:
        nonlocal running, max_parallel
        running += 1
        max_parallel = max(max_parallel, running)
        await asyncio.sleep(delay)
        order.append(name)
        running -= 1

    async def worker(name: str, delay: float) -> None:
        await q.run(lambda: job(name, delay))

    await asyncio.gather(
        worker("a", 0.05),
        worker("b", 0.01),
        worker("c", 0.01),
    )
    assert max_parallel == 1
    assert order == ["a", "b", "c"]


async def test_fifo_order_is_preserved():
    q = RequestQueue(max_len=10)
    order: list[int] = []

    async def job(i: int) -> None:
        order.append(i)
        await asyncio.sleep(0.01)

    async def worker(i: int) -> None:
        await q.run(lambda: job(i))

    await asyncio.gather(*(worker(i) for i in range(10)))
    assert order == list(range(10))


async def test_queue_full_raises():
    q = RequestQueue(max_len=2)
    release = asyncio.Event()

    async def blocker() -> None:
        await q.acquire()
        await release.wait()
        q.release()

    async def waiter() -> None:
        await q.acquire()
        q.release()

    task1 = asyncio.create_task(blocker())
    await asyncio.sleep(0.05)
    task2 = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)
    # active=1, waiting=1 -> at capacity
    with pytest.raises(QueueFullError):
        await q.acquire()
    release.set()
    await asyncio.gather(task1, task2)


async def test_stats_track_progress():
    q = RequestQueue(max_len=5)
    assert q.stats()["processed"] == 0
    await q.acquire()
    assert q.active == 1
    q.release()
    stats = q.stats()
    assert stats["processed"] == 1
    assert stats["running"] == 0
    assert stats["waiting"] == 0


async def test_cancelled_waiter_does_not_block_the_queue():
    q = RequestQueue(max_len=5)
    release = asyncio.Event()

    async def blocker() -> None:
        await q.acquire()
        await release.wait()
        q.release()

    task1 = asyncio.create_task(blocker())
    await asyncio.sleep(0.05)

    task2 = asyncio.create_task(q.acquire())
    await asyncio.sleep(0.05)
    task2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task2

    release.set()
    await task1
    # The queue must still be usable afterwards.
    await q.acquire()
    q.release()
    assert q.stats()["processed"] == 2
