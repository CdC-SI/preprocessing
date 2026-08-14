"""
Job storage.

`JobStore` is deliberately an interface with a single in-memory implementation
for this iteration. No Redis is available in the cluster, and with a single
replica (enforced by the manifest) an in-process dict is a correct — if
non-durable — store. Swapping in Redis or a PVC-backed store later means
implementing this interface, not rewriting the pipeline.
"""

import abc
import asyncio
import json
import logging
import time
from typing import Dict, List, Optional

from preprocessing import config

from .models import Job, JobStatus, job_id_instance

logger = logging.getLogger("pdf-ocr-pipeline.store")


class JobNotFound(Exception):
    """No such job id in this instance."""


class JobLost(Exception):
    """Job id was issued by a previous process; state did not survive."""


class JobStore(abc.ABC):
    @abc.abstractmethod
    async def create(self, job: Job) -> None: ...

    @abc.abstractmethod
    async def get(self, job_id: str) -> Job: ...

    @abc.abstractmethod
    async def update(self, job: Job) -> None: ...

    @abc.abstractmethod
    async def set_result(self, job_id: str, documents: List[dict]) -> None: ...

    @abc.abstractmethod
    async def stats(self) -> dict: ...


class InMemoryJobStore(JobStore):
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = asyncio.Lock()
        self._sweeper: Optional[asyncio.Task] = None
        self._total_result_bytes = 0

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self._sweeper is None:
            self._sweeper = asyncio.create_task(self._sweep_loop())
            logger.info(
                "Job store started (result_ttl=%.0fs, metadata_ttl=%.0fs)",
                config.RESULT_TTL_SECONDS,
                config.METADATA_TTL_SECONDS,
            )

    async def stop(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            try:
                await self._sweeper
            except asyncio.CancelledError:
                pass
            self._sweeper = None

    # -- CRUD --------------------------------------------------------------
    async def create(self, job: Job) -> None:
        async with self._lock:
            job.metadata_expires_at = time.time() + config.METADATA_TTL_SECONDS
            self._jobs[job.job_id] = job

    async def get(self, job_id: str) -> Job:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.last_accessed = time.time()
                return job

        # Distinguish "never existed / expired" from "lost in a restart".
        if job_id_instance(job_id) and job_id_instance(job_id) != config.INSTANCE_ID:
            raise JobLost(
                "This job was submitted to a previous instance of the service "
                "and its state did not survive a restart. Please resubmit."
            )
        raise JobNotFound(f"Unknown job id: {job_id}")

    async def update(self, job: Job) -> None:
        async with self._lock:
            self._jobs[job.job_id] = job

    async def set_result(self, job_id: str, documents: List[dict]) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            size = _estimate_bytes(documents)
            job.result = documents
            job.result_bytes = size
            job.result_expires_at = time.time() + config.RESULT_TTL_SECONDS
            self._total_result_bytes += size
        await self._enforce_byte_cap()

    async def delete(self, job_id: str) -> None:
        async with self._lock:
            job = self._jobs.pop(job_id, None)
            if job is not None and job.result is not None:
                self._total_result_bytes -= job.result_bytes

    async def stats(self) -> dict:
        async with self._lock:
            by_status: Dict[str, int] = {}
            for job in self._jobs.values():
                by_status[job.status.value] = by_status.get(job.status.value, 0) + 1
            return {
                "jobs_tracked": len(self._jobs),
                "jobs_by_status": by_status,
                "result_bytes": self._total_result_bytes,
                "result_bytes_limit": config.MAX_RESULT_BYTES,
                "instance_id": config.INSTANCE_ID,
            }

    # -- retention ---------------------------------------------------------
    async def _sweep_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(config.STORE_SWEEP_INTERVAL)
                await self._sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Job store sweep failed")

    async def _sweep(self) -> None:
        now = time.time()
        dropped_results = 0
        dropped_jobs = 0

        async with self._lock:
            for job_id, job in list(self._jobs.items()):
                # Expire the (large) result payload first.
                if (
                    job.result is not None
                    and job.result_expires_at is not None
                    and now > job.result_expires_at
                ):
                    self._total_result_bytes -= job.result_bytes
                    job.result = None
                    job.result_bytes = 0
                    dropped_results += 1

                # Then the (small) metadata record.
                if job.metadata_expires_at is not None and now > job.metadata_expires_at:
                    self._jobs.pop(job_id, None)
                    dropped_jobs += 1

        if dropped_results or dropped_jobs:
            logger.info(
                "Store sweep: expired %d result(s), removed %d job record(s)",
                dropped_results,
                dropped_jobs,
            )

    async def _enforce_byte_cap(self) -> None:
        """LRU-evict retained results so a burst cannot exhaust pod memory."""
        if self._total_result_bytes <= config.MAX_RESULT_BYTES:
            return

        async with self._lock:
            candidates = [
                job for job in self._jobs.values() if job.result is not None
            ]
            candidates.sort(key=lambda j: j.last_accessed)

            evicted = 0
            for job in candidates:
                if self._total_result_bytes <= config.MAX_RESULT_BYTES:
                    break
                self._total_result_bytes -= job.result_bytes
                job.result = None
                job.result_bytes = 0
                job.result_expires_at = None
                evicted += 1

        if evicted:
            logger.warning(
                "Evicted %d result payload(s) to stay within the %d byte cap",
                evicted,
                config.MAX_RESULT_BYTES,
            )


def _estimate_bytes(documents: List[dict]) -> int:
    try:
        return len(json.dumps(documents).encode("utf-8"))
    except Exception:
        return sum(len(str(doc)) for doc in documents)


_store: Optional[InMemoryJobStore] = None


def get_store() -> InMemoryJobStore:
    global _store
    if _store is None:
        _store = InMemoryJobStore()
    return _store
