"""
Worker pool: page extraction, retries, finalisation.

Runs in the same process as the API. With a single replica (enforced by the
manifest) the module-level semaphores in `preprocessing.clients` are a genuine
global cap on OCR pressure against the shared VLM.
"""

import asyncio
import logging
import time
from typing import List, Optional

from preprocessing import chunking, clients, config, render, tokenization
from preprocessing.chunking import Chunk, PageResult

from .models import Job, JobPriority, JobStatus
from .queue import WorkItem, WorkKind, WorkQueue, get_queue
from .store import InMemoryJobStore, get_store

logger = logging.getLogger("pdf-ocr-pipeline.worker")

# vLLM runs with --scheduling-policy priority: lower value wins. Translation
# uses the default 0, so OCR sits strictly behind it at all times.
VLM_PRIORITY_SMALL_DOC = 1
VLM_PRIORITY_LARGE_DOC = 5


class JobCancelled(Exception):
    """
    A page was abandoned because its job was cancelled.

    Deliberately NOT asyncio.CancelledError: that derives from BaseException,
    so it escapes `except Exception` handlers and would tear down the worker
    task itself rather than just abandoning the page.
    """


class WorkerPool:
    def __init__(
        self,
        queue: Optional[WorkQueue] = None,
        store: Optional[InMemoryJobStore] = None,
    ) -> None:
        self.queue = queue or get_queue()
        self.store = store or get_store()
        self._workers: List[asyncio.Task] = []
        # Retry tasks are held here so they are not garbage collected mid-sleep.
        self._background: set = set()
        self._draining = False
        self._active = 0
        self._pages_processed = 0
        self._started_at = time.time()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        await self.store.start()
        await self.queue.start_aging()
        for index in range(config.WORKER_COUNT):
            self._workers.append(asyncio.create_task(self._run(index)))
        logger.info("Worker pool started with %d worker(s)", config.WORKER_COUNT)

    async def drain(self, timeout: float = 120.0) -> None:
        """
        Stop accepting new work and let in-flight pages finish.

        Called on SIGTERM so a planned rollout does not discard work that is
        nearly done. Job state is in-memory, so anything unfinished is lost and
        the client is told to resubmit.
        """
        self._draining = True
        logger.info("Draining worker pool (queue depth=%d)", self.queue.depth())

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.queue.depth() == 0 and self._active == 0:
                break
            await asyncio.sleep(0.5)

        await self.queue.close()
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        await self.store.stop()
        logger.info("Worker pool drained")

    @property
    def draining(self) -> bool:
        return self._draining

    # -- main loop ---------------------------------------------------------
    async def _run(self, worker_index: int) -> None:
        """
        Worker loop.

        This must never exit except on a genuine shutdown. A worker that dies
        silently shrinks the pool, and once all workers are gone the queue
        stops draining entirely with no external symptom other than jobs
        hanging in `queued`.
        """
        try:
            while True:
                try:
                    item = await self.queue.get()
                    if item is None:
                        logger.info("Worker %d stopping (queue closed)", worker_index)
                        return
                    self._active += 1
                    try:
                        await self._handle(item)
                    finally:
                        self._active -= 1
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Worker %d hit an unexpected error; continuing", worker_index
                    )
        except asyncio.CancelledError:
            logger.info("Worker %d cancelled (shutdown)", worker_index)
            raise
        except BaseException:
            # Should be unreachable, but a worker dying unnoticed is the worst
            # possible failure mode, so make it loud.
            logger.critical("Worker %d died unexpectedly", worker_index, exc_info=True)
            raise

    async def _handle(self, item: WorkItem) -> None:
        try:
            job = await self.store.get(item.job_id)
        except Exception:
            logger.debug("Skipping work for unknown job %s", item.job_id)
            return

        if job.status in (JobStatus.CANCELLED, JobStatus.FAILED):
            return

        if item.kind is WorkKind.PAGE:
            await self._process_page(job, item)
        else:
            await self._finalize(job)

    # -- page extraction ---------------------------------------------------
    async def _process_page(self, job: Job, item: WorkItem) -> None:
        if job.status is JobStatus.QUEUED:
            job.status = JobStatus.RUNNING
            job.started_at = job.started_at or time.time()
            await self.store.update(job)

        page_number = item.page_index + 1
        vlm_priority = (
            VLM_PRIORITY_LARGE_DOC if job.is_large else VLM_PRIORITY_SMALL_DOC
        )

        try:
            result = await self._extract_page(job, item.page_index, vlm_priority)

        except JobCancelled:
            # Expected: the job was cancelled mid-page. Abandon this page
            # quietly without retrying or marking it failed.
            logger.debug("Page %d of job %s abandoned (cancelled)", page_number, job.job_id)
            return

        except Exception as exc:
            if item.attempt < config.PAGE_MAX_ATTEMPTS:
                delay = config.PAGE_RETRY_BASE_DELAY * (2 ** (item.attempt - 1))
                logger.warning(
                    "Page %d of job %s failed (attempt %d/%d): %s; retrying in %.1fs",
                    page_number,
                    job.job_id,
                    item.attempt,
                    config.PAGE_MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                self._spawn(self._requeue_later(job, item, delay))
                return

            logger.error(
                "Page %d of job %s failed permanently after %d attempts: %s",
                page_number,
                job.job_id,
                item.attempt,
                exc,
            )
            result = PageResult(
                page_number=page_number,
                text="",
                token_count=0,
                failed=True,
                error=str(exc),
            )
            job.pages_failed.append(page_number)

        # Re-read: the job may have been cancelled while the VLM call was in
        # flight. Recording the page is harmless, but we must not finalise.
        if job.status is JobStatus.CANCELLED:
            return

        job.pages[item.page_index] = result
        job.pages_done += 1
        self._pages_processed += 1
        await self.store.update(job)

        if job.pages_done >= job.pages_total:
            await self.queue.put(
                WorkItem(kind=WorkKind.FINALIZE, job_id=job.job_id),
                # Finalisation is cheap and frees the page buffers, so it runs
                # ahead of queued pages from other jobs.
                priority=-1,
            )

    async def _requeue_later(self, job: Job, item: WorkItem, delay: float) -> None:
        await asyncio.sleep(delay)
        if job.status in (JobStatus.CANCELLED, JobStatus.FAILED):
            return
        retry = WorkItem(
            kind=WorkKind.PAGE,
            job_id=item.job_id,
            page_index=item.page_index,
            attempt=item.attempt + 1,
        )
        await self.queue.put(retry, priority=_queue_priority(job))

    async def _extract_page(
        self, job: Job, page_index: int, vlm_priority: int
    ) -> PageResult:
        page_number = page_index + 1

        # Cheap pre-check: skip rendering entirely for an already-cancelled job.
        if job.status is JobStatus.CANCELLED:
            raise JobCancelled(job.job_id)

        # Phase 4 will consult the text layer here first, under
        # TEXT_LAYER_EXTRACTION_ENABLED, and only fall back to the VLM when the
        # extracted text fails the quality heuristics.

        data_url = await asyncio.to_thread(render.render_page, job.pdf_bytes, page_index)

        async with clients.vlm_slot(is_large_doc=job.is_large):
            if job.status is JobStatus.CANCELLED:
                raise JobCancelled(job.job_id)
            response = await clients.call_vlm(data_url, priority=vlm_priority)

        text = (response.ocr_content or "").strip()
        if not text:
            logger.warning("OCR returned empty content for page %d of %s", page_number, job.job_id)

        token_count = await tokenization.count_tokens(text)
        return PageResult(
            page_number=page_number,
            text=text,
            token_count=token_count,
            source="vlm",
        )

    # -- finalisation ------------------------------------------------------
    async def _finalize(self, job: Job) -> None:
        if job.status.is_terminal:
            return

        try:
            pages = [job.pages[i] for i in sorted(job.pages.keys())]
            chunks = chunking.build_chunks(pages)

            if not chunks:
                job.status = JobStatus.COMPLETED_WITH_ERRORS
                job.error = "No text could be extracted from this document."
                job.error_code = "no_content"
                job.finished_at = time.time()
                job.release_working_memory()
                await self.store.set_result(job.job_id, [])
                await self.store.update(job)
                return

            await self._summarize_chunks(job, chunks)

            document_summary = chunks[0].summary
            document_language = chunking.choose_document_language(chunks)

            contents = [
                chunking.build_contextualized_text(chunks, i, document_summary)
                for i in range(len(chunks))
            ]
            embeddings = await self._embed_all(contents)

            documents = [
                chunking.make_doc(
                    chunk=chunk,
                    content=content,
                    embedding=embedding,
                    doc_title=job.doc_title,
                    user_uuid=job.user_uuid,
                    document_language=document_language,
                    document_summary=document_summary,
                    dpi=config.PDF_RENDER_DPI,
                )
                for chunk, content, embedding in zip(chunks, contents, embeddings)
            ]

            job.status = (
                JobStatus.COMPLETED_WITH_ERRORS
                if job.pages_failed
                else JobStatus.COMPLETED
            )
            job.finished_at = time.time()
            job.release_working_memory()
            await self.store.set_result(job.job_id, documents)
            await self.store.update(job)

            logger.info(
                "Job %s finished: %d document(s) from %d page(s) in %.1fs",
                job.job_id,
                len(documents),
                job.pages_total,
                job.duration_seconds or 0.0,
            )

        except Exception as exc:
            logger.exception("Finalisation failed for job %s", job.job_id)
            job.status = JobStatus.FAILED
            job.error = f"Finalisation failed: {exc}"
            job.error_code = "finalize_failed"
            job.finished_at = time.time()
            job.release_working_memory()
            await self.store.update(job)

    async def _summarize_chunks(self, job: Job, chunks: List[Chunk]) -> None:
        """
        One LLM call per chunk for {summary, language}.

        Each chunk is bounded by CHUNK_TOKEN_BUDGET, so it always fits the
        128k context comfortably. This replaces the previous single call over
        the whole document, which exceeded the limit on any large PDF and
        silently produced empty metadata.
        """
        priority = VLM_PRIORITY_LARGE_DOC if job.is_large else VLM_PRIORITY_SMALL_DOC
        semaphore = asyncio.Semaphore(config.VLM_MAX_CONCURRENCY)

        async def summarize(chunk: Chunk) -> None:
            async with semaphore:
                if job.status is JobStatus.CANCELLED:
                    return
                response = await clients.call_llm(chunk.text, priority=priority)
                chunk.summary = (response.summary or "").strip()
                chunk.language = (response.language or "").strip()

        await asyncio.gather(*(summarize(chunk) for chunk in chunks))

    async def _embed_all(self, contents: List[str]) -> List[List[float]]:
        embeddings: List[List[float]] = []
        for start in range(0, len(contents), config.EMBEDDING_BATCH_SIZE):
            batch = contents[start:start + config.EMBEDDING_BATCH_SIZE]
            embeddings.extend(await clients.embed_batch(batch))
        return embeddings

    # -- introspection -----------------------------------------------------
    def alive_workers(self) -> int:
        return sum(1 for task in self._workers if not task.done())

    def healthy(self) -> bool:
        """False if any worker has died; surfaced via the liveness probe."""
        return self._draining or self.alive_workers() == len(self._workers)

    def stats(self) -> dict:
        return {
            "workers": len(self._workers),
            "workers_alive": self.alive_workers(),
            "healthy": self.healthy(),
            "active_workers": self._active,
            "pages_processed": self._pages_processed,
            "draining": self._draining,
            "uptime_seconds": round(time.time() - self._started_at, 1),
        }


def _queue_priority(job: Job) -> int:
    return int(JobPriority.LOW if job.is_large else JobPriority.HIGH)


_pool: Optional[WorkerPool] = None


def get_pool() -> WorkerPool:
    global _pool
    if _pool is None:
        _pool = WorkerPool()
    return _pool
