from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from chatto_threaddump.contracts import DumpRequest


class RequestInbox:
    """Hold independent dump requests for one sequential async consumer."""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._requests: asyncio.Queue[DumpRequest] = asyncio.Queue(maxsize=capacity)

    def admit(self, request: DumpRequest) -> bool:
        """Enqueue a request immediately, returning false when the inbox is full."""
        try:
            self._requests.put_nowait(request)
        except asyncio.QueueFull:
            return False
        return True

    async def run(self, process: Callable[[DumpRequest], Awaitable[None]]) -> None:
        """Process admitted requests in FIFO order, one at a time."""
        while True:
            request = await self._requests.get()
            try:
                await process(request)
            finally:
                self._requests.task_done()
