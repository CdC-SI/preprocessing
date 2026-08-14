"""Job submission and cancellation orchestration."""

import asyncio
import logging
import time
from typing import List, Optional

from preprocessing import config, render

from .models import Job, JobPriority, JobStatus, new_job_id
from .queue import WorkItem, WorkKind, get_queue
from .store import get_store
from .worker import get_pool

logger = logging.getLogger("pdf-ocr-pipeline.service")


async def submit_job(
    pdf_bytes: bytes,
    user_uuid: str,
    doc_title: str,
    force_high_priority: bool = False,
) -> Job:
    """
    Validate the PDF and enqueue one work item per page.

    Raises render.PdfError for anything the caller should fix; the API layer
    maps `code` onto an HTTP status.
    """
    page_count = await asyncio.to_thread(render.inspect_pdf, pdf_bytes)

    job = Job(
        job_id=new_job_id(),
        user_uuid=user_uuid,
        doc_title=doc_title,
        pdf_bytes=pdf_bytes,
        pages_total=page_count,
        status=JobStatus.QUEUED,
    )
    # Small documents are interactive (a user waiting on the chatbot UI);
    # large ones are batch work that must not block them.
    job.priority = (
        JobPriority.HIGH
        if force_high_priority or page_count <= config.LARGE_DOC_PAGE_THRESHOLD
        else JobPriority.LOW
    )

    store = get_store()
    await store.create(job)

    items = [
        WorkItem(kind=WorkKind.PAGE, job_id=job.job_id, page_index=index)
        for index in range(page_count)
    ]
    await get_queue().put_many(items, priority=int(job.priority))

    logger.info(
        "Job %s submitted: %d page(s), priority=%s, title=%r",
        job.job_id,
        page_count,
        job.priority.name,
        doc_title,
    )
    return job


async def cancel_job(job: Job) -> bool:
    """
    Cancel a job. Queued pages are dropped immediately; pages already in flight
    are allowed to finish, since aborting a VLM call wastes GPU work that has
    already been done.
    """
    if job.status.is_terminal:
        return False

    job.status = JobStatus.CANCELLED
    job.finished_at = time.time()
    job.error = "Cancelled by client."
    job.error_code = "cancelled"

    dropped = await get_queue().drop_job(job.job_id)
    job.release_working_memory()
    await get_store().update(job)

    logger.info(
        "Job %s cancelled after %d/%d page(s); dropped %d queued item(s)",
        job.job_id,
        job.pages_done,
        job.pages_total,
        dropped,
    )
    return True


async def wait_for_completion(job: Job, timeout: float) -> Job:
    """
    Block until a job reaches a terminal state. Used only by the legacy
    `:predict` endpoint, which is capped at LEGACY_MAX_PAGES pages.
    """
    deadline = time.time() + timeout
    store = get_store()

    while time.time() < deadline:
        current = await store.get(job.job_id)
        if current.status.is_terminal:
            return current
        await asyncio.sleep(0.25)

    raise asyncio.TimeoutError(
        f"Job {job.job_id} did not complete within {timeout:.0f}s"
    )


def queue_stats() -> dict:
    queue = get_queue()
    pool = get_pool()
    from preprocessing import clients

    return {
        "queue": queue.depth_by_tier(),
        "queue_oldest_wait_seconds": queue.oldest_wait_seconds(),
        "vlm_inflight": clients.inflight_vlm_requests(),
        "vlm_max_concurrency": config.VLM_MAX_CONCURRENCY,
        "large_doc_concurrency": config.OCR_LARGE_DOC_CONCURRENCY,
        "worker_pool": pool.stats(),
    }
