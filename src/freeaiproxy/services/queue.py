"""FIFO request queue: every browser interaction runs one at a time, in order.

The gateway drives a single browser window, so only one request may touch the
page at any moment. An asyncio.Lock alone would give that guarantee, but it
does not promise fairness: waiters are woken in arbitrary order, so a client
that retries quickly could jump ahead of requests that have been waiting
longer. A FIFO queue makes the ordering explicit and observable, and gives us
a place to enforce the queue length limit and to report the position of a
waiting request.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from freeaiproxy.engine.errors import QueueFullError

logger = logging.getLogger("freeaiproxy.queue")

T = TypeVar("T")


@dataclass
class QueueStats:
    waiting: int = 0
    running: int = 0
    processed: int = 0
    max_wait_ms: int = 0
    enqueued_at: list[float] = field(default_factory=list)


class RequestQueue:
    """Serialises work items in strict arrival order."""

    def __init__(self, max_len: int = 20) -> None:
        self._max_len = max_len
        self._waiters: list[asyncio.Future[None]] = []
        self._running = False
        self._processed = 0
        self._max_wait_ms = 0
        self._active = 0

    @property
    def waiting(self) -> int:
        return len(self._waiters)

    @property
    def active(self) -> int:
        return self._active

    @property
    def processed(self) -> int:
        return self._processed

    def has_capacity(self) -> bool:
        return self._active + len(self._waiters) < self._max_len

    def stats(self) -> dict[str, Any]:
        return {
            "waiting": len(self._waiters),
            "running": self._active,
            "processed": self._processed,
            "max_queue_len": self._max_len,
            "max_wait_ms": self._max_wait_ms,
        }

    async def acquire(self) -> float:
        """Wait for the slot. Returns the number of milliseconds spent waiting."""
        if not self.has_capacity():
            raise QueueFullError(
                f"request queue is full ({self._active + len(self._waiters)}/{self._max_len})"
            )
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        if self._running:
            waiter: asyncio.Future[None] = loop.create_future()
            self._waiters.append(waiter)
            try:
                await waiter
            except BaseException:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                raise
        self._running = True
        waited_ms = int((loop.time() - t0) * 1000)
        self._max_wait_ms = max(self._max_wait_ms, waited_ms)
        self._active += 1
        return waited_ms

    def release(self) -> None:
        self._active = max(0, self._active - 1)
        self._processed += 1
        while self._waiters:
            waiter = self._waiters.pop(0)
            if not waiter.done():
                waiter.set_result(None)
                return
        self._running = False

    async def run(self, coro_factory: Callable[[], Awaitable[T]]) -> T:
        """Run a coroutine factory while holding the queue slot."""
        await self.acquire()
        try:
            return await coro_factory()
        finally:
            self.release()
