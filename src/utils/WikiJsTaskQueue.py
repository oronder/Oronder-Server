import asyncio
import time
from asyncio import Future
from typing import Awaitable, Any

from integrations import wikijs
from utils import getLogger
import psutil

logger = getLogger(__name__)


class WikiJsTaskQueue:
    """Singleton queue that starts tasks only when CPU < 50% and 5s have passed since the last start."""

    _instance: "WikiJsTaskQueue | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(WikiJsTaskQueue, cls).__new__(cls)
            cls._instance._queue = asyncio.Queue()
            cls._instance._last_start = 0.0
            cls._instance._worker_task = None
            cls._instance._valid = wikijs.valid
        return cls._instance

    async def add_task(self, coro: Awaitable[Any], *args, **kwargs) -> None:
        """Enqueue a coroutine (callable returning an awaitable)."""
        if self._instance._valid:
            await self._queue.put((coro, args, kwargs))
            logger.warning(f"{len(self._queue)} {coro=} {args=} {kwargs=}")

    async def _worker(self) -> None:
        """Background worker that pulls tasks from the queue and launches them."""
        while True:
            try:
                coro, args, kwargs = await self._queue.get()
            except asyncio.CancelledError:
                break

            # Wait until we meet the CPU and timing constraints
            while True:
                cpu = psutil.cpu_percent(interval=None)
                elapsed_since_last = time.monotonic() - self._last_start
                if cpu < 50 and elapsed_since_last >= 5:
                    break
                await asyncio.sleep(0.5)  # Poll every 500ms

            # Launch the task
            asyncio.create_task(coro(*args, **kwargs))
            self._last_start = time.monotonic()

            # Mark the queue item as done
            self._queue.task_done()

    async def start_worker(self) -> None:
        """Create a worker task if one isn’t running yet."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

    async def stop_worker(self) -> None:
        """Gracefully stop the worker (useful on app shutdown)."""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                await asyncio.sleep(0)


# Convenience instance that callers can import
wikijs_task_queue = WikiJsTaskQueue()
