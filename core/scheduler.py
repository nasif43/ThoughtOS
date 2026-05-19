import asyncio
import logging
from typing import Callable

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self):
        self._tasks = []

    async def schedule(self, delay_seconds: float, callback: Callable, *args, **kwargs):
        async def _run():
            await asyncio.sleep(delay_seconds)
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(*args, **kwargs)
                else:
                    callback(*args, **kwargs)
            except Exception as e:
                logger.error(f"Scheduled task failed: {e}")

        task = asyncio.create_task(_run())
        self._tasks.append(task)
        return task

    def cancel_all(self):
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()


scheduler = Scheduler()
