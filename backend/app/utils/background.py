"""
Tracked fire-and-forget tasks.

Plain `asyncio.create_task(...)` has two failure modes:
  - Returned task can be garbage-collected mid-run if no reference is kept.
  - Exceptions surface only as "Task exception was never retrieved" warnings.

`spawn` keeps a strong reference until the task finishes and logs any
exception against the supplied name.
"""

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task[Any]:
    """Create a tracked background task that logs exceptions under `name`."""
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    task.add_done_callback(_log_task_exception)
    return task


def _log_task_exception(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    logger.error(
        "Background task %r failed", task.get_name(), exc_info=exc,
    )
