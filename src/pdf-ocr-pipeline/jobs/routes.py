"""
HTTP API.

Registered onto the KServe ModelServer's own FastAPI application, so the
existing `/v1/models/{name}:predict` contract, the readiness probes and the
ServingRuntime wiring all continue to work unchanged.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from preprocessing import config, render

from . import service
from .models import (
    JobStatus,
    ResultResponse,
    StatusResponse,
    SubmitResponse,
)
from .store import JobLost, JobNotFound, get_store
from .worker import get_pool

logger = logging.getLogger("pdf-ocr-pipeline.api")

router = APIRouter(tags=["jobs"])

# PdfError.code -> HTTP status
_PDF_ERROR_STATUS = {
    "empty_file": 400,
    "corrupt_pdf": 400,
    "empty_pdf": 400,
    "encrypted_pdf": 422,
    "invalid_pdf": 400,
    "file_too_large": 413,
    "too_many_pages": 413,
}


async def _load_job(job_id: str, user_uuid: str):
    """
    Fetch a job and verify ownership.

    Job ids are unguessable, but checking `user_uuid` as well means one user
    can never read another user's extracted document content.
    """
    try:
        job = await get_store().get(job_id)
    except JobLost as exc:
        raise HTTPException(
            status_code=410,
            detail={"error": str(exc), "code": "job_lost", "status": "lost"},
        )
    except JobNotFound:
        raise HTTPException(
            status_code=404,
            detail={"error": f"Unknown job id: {job_id}", "code": "job_not_found"},
        )

    if job.user_uuid != user_uuid:
        # Deliberately indistinguishable from "not found".
        raise HTTPException(
            status_code=404,
            detail={"error": f"Unknown job id: {job_id}", "code": "job_not_found"},
        )
    return job


@router.post("/jobs", response_model=SubmitResponse, status_code=202)
async def submit(
    file: UploadFile = File(..., description="The PDF to process"),
    user_uuid: str = Form(...),
    doc_title: str = Form(...),
):
    """Accept a PDF and return immediately with a job id."""
    if get_pool().draining:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Service is shutting down; please retry shortly.",
                "code": "draining",
            },
        )

    pdf_bytes = await file.read()

    try:
        job = await service.submit_job(pdf_bytes, user_uuid, doc_title)
    except render.PdfError as exc:
        raise HTTPException(
            status_code=_PDF_ERROR_STATUS.get(exc.code, 400),
            detail={"error": str(exc), "code": exc.code},
        )

    return SubmitResponse(
        job_id=job.job_id,
        status=job.status.value,
        pages_total=job.pages_total,
        priority="low" if job.is_large else "high",
        poll_url=f"/jobs/{job.job_id}?user_uuid={user_uuid}",
        result_url=f"/jobs/{job.job_id}/result?user_uuid={user_uuid}",
    )


@router.get("/jobs/{job_id}", response_model=StatusResponse)
async def status(job_id: str, user_uuid: str = Query(...)):
    """Poll job progress."""
    job = await _load_job(job_id, user_uuid)
    return StatusResponse(**job.to_status_dict())


@router.get("/jobs/{job_id}/result", response_model=ResultResponse)
async def result(job_id: str, user_uuid: str = Query(...)):
    """Fetch the processed documents once the job has completed."""
    job = await _load_job(job_id, user_uuid)

    if not job.status.is_terminal:
        raise HTTPException(
            status_code=409,
            detail={
                "error": f"Job is {job.status.value}; result not available yet.",
                "code": "not_ready",
                "status": job.status.value,
                "pages_done": job.pages_done,
                "pages_total": job.pages_total,
            },
        )

    if not job.status.has_result:
        raise HTTPException(
            status_code=409,
            detail={
                "error": job.error or f"Job ended as {job.status.value}.",
                "code": job.error_code or job.status.value,
                "status": job.status.value,
            },
        )

    if job.result is None:
        raise HTTPException(
            status_code=410,
            detail={
                "error": "Result has expired or was evicted. Please resubmit.",
                "code": "result_expired",
            },
        )

    return ResultResponse(
        job_id=job.job_id,
        status=job.status.value,
        documents=job.result,
        pages_failed=sorted(job.pages_failed),
    )


@router.delete("/jobs/{job_id}")
async def cancel(job_id: str, user_uuid: str = Query(...)):
    """Cancel a job, e.g. when the user deletes the document in the UI."""
    job = await _load_job(job_id, user_uuid)
    cancelled = await service.cancel_job(job)
    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "cancelled": cancelled,
        "pages_processed": job.pages_done,
        "pages_total": job.pages_total,
    }


@router.get("/stats")
async def stats():
    """Operational counters: queue depth, VLM pressure, retention."""
    store_stats = await get_store().stats()
    return {**service.queue_stats(), "store": store_stats}


# --------------------------------------------------------------------------
# Legacy v1 contract
# --------------------------------------------------------------------------
async def handle_legacy_predict(payload: dict) -> dict:
    """
    Backward-compatible `:predict` handler.

    Submits the document at high priority and waits for it, preserving the
    original request and response shapes. Documents above LEGACY_MAX_PAGES are
    refused with a pointer to the async API, because holding an HTTP request
    open for a large document is exactly what caused the frontend timeouts.
    """
    instances = payload.get("instances") or []
    if not instances:
        raise HTTPException(
            status_code=400,
            detail={"error": "Missing 'instances' in payload.", "code": "bad_request"},
        )

    instance = instances[0]
    try:
        import base64

        pdf_bytes = base64.b64decode(instance["data_url"])
        user_uuid = instance["user_uuid"]
        doc_title = instance["doc_title"]
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": f"Missing field: {exc}", "code": "bad_request"},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": f"Could not decode data_url: {exc}", "code": "bad_request"},
        )

    try:
        page_count = await asyncio.to_thread(render.inspect_pdf, pdf_bytes)
    except render.PdfError as exc:
        raise HTTPException(
            status_code=_PDF_ERROR_STATUS.get(exc.code, 400),
            detail={"error": str(exc), "code": exc.code},
        )

    if page_count > config.LEGACY_MAX_PAGES:
        raise HTTPException(
            status_code=413,
            detail={
                "error": (
                    f"This document has {page_count} pages. The synchronous "
                    f"endpoint accepts at most {config.LEGACY_MAX_PAGES}. "
                    "Use the asynchronous API instead."
                ),
                "code": "use_async_api",
                "pages_total": page_count,
                "submit_url": "/jobs",
            },
        )

    job = await service.submit_job(
        pdf_bytes, user_uuid, doc_title, force_high_priority=True
    )

    try:
        job = await service.wait_for_completion(job, config.LEGACY_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail={
                "error": "Processing timed out. Use the asynchronous API.",
                "code": "timeout",
                "job_id": job.job_id,
            },
        )

    if not job.status.has_result:
        raise HTTPException(
            status_code=500,
            detail={
                "error": job.error or "Processing failed.",
                "code": job.error_code or "failed",
            },
        )

    return {"documents": job.result or []}


def error_response(exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=detail)
