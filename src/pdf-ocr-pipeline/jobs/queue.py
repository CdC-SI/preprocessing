"""
In-process priority work queue.

Work is scheduled at *page* granularity rather than document granularity. That
is the key property: a 500-page upload becomes 500 small units that interleave
with everything else, instead of one unit that monopolises the workers. It
also makes progress reporting, retries and cancellation naturally granular.

A hand-rolled heap is used rather than asyncio.PriorityQueue because we need
two things the stdlib version cannot do: re-prioritise queued items (aging)
and drop all items belonging to a cancelled job.
"""

import asyncio
import heapq
import itertools
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

from preprocessing import config

logger = logging.getLogger("pdf-ocr-pipeline.queue")


class WorkKind(str, Enum):
    PAGE = "page"
    FINALIZE = "finalize"


@dataclass
class WorkItem:
    kind: WorkKind
    job_id: str
    page_index: int = -1  # 0-based; unused for FINALIZE
    attempt: int = 1
    enqueued_at: float = field(default_factory=time.time)

    def __repr__(self) -> str:  # pragma: no cover - logging aid
        if self.kind is WorkKind.PAGE:
            return f"<page {self.job_id}:{self.page_index} attempt={self.attempt}>"
        return f"<finalize {self.job_id}>"


class WorkQueue:
    def __init__(self) -> None:
        self._heap: List[Tuple[int, int, WorkItem]] = []
        self._counter = itertools.count()
        self._cond = asyncio.Condition()
        self._closed = False
        self._aging_task: Optional[asyncio.Task] = None

    # -- producer ----------------------------------------------------------
    async def put(self, item: WorkItem, priority: int) -> None:
        async with self._cond:
            heapq.heappush(self._heap, (priority, next(self._counter), item))
            self._cond.notify()

    async def put_many(self, items: List[WorkItem], priority: int) -> None:
        async with self._cond:
            for item in items:
                heapq.heappush(self._heap, (priority, next(self._counter), item))
            self._cond.notify_all()

    # -- consumer ----------------------------------------------------------
    async def get(self) -> Optional[WorkItem]:
        """Pop the highest-priority item, waiting if empty. None once closed."""
        async with self._cond:
            while not self._heap and not self._closed:
                await self._cond.wait()
            if not self._heap:
                return None
            _, _, item = heapq.heappop(self._heap)
            return item

    # -- maintenance -------------------------------------------------------
    async def drop_job(self, job_id: str) -> int:
        """Remove every queued item for a job (cancellation)."""
        async with self._cond:
            before = len(self._heap)
            self._heap = [
                entry for entry in self._heap if entry[2].job_id != job_id
            ]
            heapq.heapify(self._heap)
            removed = before - len(self._heap)
        if removed:
            logger.info("Dropped %d queued item(s) for cancelled job %s", removed, job_id)
        return removed

    async def promote_aged(self) -> int:
        """
        Promote long-waiting low-priority items.

        Without this, a steady stream of small uploads would starve a large
        document indefinitely.
        """
        now = time.time()
        promoted = 0

        async with self._cond:
            rebuilt: List[Tuple[int, int, WorkItem]] = []
            for priority, seq, item in self._heap:
                waited = now - item.enqueued_at
                if priority > 0 and waited > config.AGING_PROMOTION_SECONDS:
                    priority = 0
                    promoted += 1
                rebuilt.append((priority, seq, item))
            self._heap = rebuilt
            heapq.heapify(self._heap)
            if promoted:
                self._cond.notify_all()

        if promoted:
            logger.info(
                "Aging: promoted %d queued item(s) after %.0fs wait",
                promoted,
                config.AGING_PROMOTION_SECONDS,
            )
        return promoted

    async def start_aging(self) -> None:
        if self._aging_task is None:
            self._aging_task = asyncio.create_task(self._aging_loop())

    async def _aging_loop(self) -> None:
        interval = max(30.0, config.AGING_PROMOTION_SECONDS / 10.0)
        while not self._closed:
            try:
                await asyncio.sleep(interval)
                await self.promote_aged()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Aging loop iteration failed")

    async def close(self) -> None:
        self._closed = True
        if self._aging_task is not None:
            self._aging_task.cancel()
            self._aging_task = None
        async with self._cond:
            self._cond.notify_all()

    # -- introspection -----------------------------------------------------
    def depth(self) -> int:
        return len(self._heap)

    def depth_by_tier(self) -> dict:
        high = sum(1 for entry in self._heap if entry[0] <= 0)
        return {"high": high, "low": len(self._heap) - high, "total": len(self._heap)}

    def oldest_wait_seconds(self) -> float:
        if not self._heap:
            return 0.0
        now = time.time()
        return round(max(now - entry[2].enqueued_at for entry in self._heap), 1)


_queue: Optional[WorkQueue] = None


def get_queue() -> WorkQueue:
    global _queue
    if _queue is None:
        _queue = WorkQueue()
    return _queue
