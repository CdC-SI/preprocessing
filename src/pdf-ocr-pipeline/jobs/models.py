"""Job domain models and API schemas."""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from preprocessing import config


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (
            JobStatus.COMPLETED,
            JobStatus.COMPLETED_WITH_ERRORS,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        )

    @property
    def has_result(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS)


class JobPriority(int, Enum):
    """Queue tier. Lower value is served first."""

    HIGH = 0  # interactive: small documents and legacy :predict calls
    LOW = 10  # batch: large documents


def new_job_id() -> str:
    """
    Instance-prefixed job id.

    Job state is in-memory only, so a restart loses it. Encoding the instance
    identity lets the API answer 410 Gone ("resubmit") rather than 404 for ids
    issued by a previous process.
    """
    return f"{config.INSTANCE_ID}-{uuid.uuid4().hex}"


def job_id_instance(job_id: str) -> str:
    return job_id.split("-", 1)[0] if "-" in job_id else ""


@dataclass
class Job:
    job_id: str
    user_uuid: str
    doc_title: str
    pdf_bytes: bytes = field(repr=False, default=b"")
    pages_total: int = 0
    pages_done: int = 0
    pages_failed: List[int] = field(default_factory=list)
    status: JobStatus = JobStatus.QUEUED
    priority: JobPriority = JobPriority.HIGH
    error: Optional[str] = None
    error_code: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[List[dict]] = field(default=None, repr=False)
    result_bytes: int = 0
    result_expires_at: Optional[float] = None
    metadata_expires_at: Optional[float] = None
    # Page extraction output, keyed by 0-based page index.
    pages: Dict[int, Any] = field(default_factory=dict, repr=False)
    last_accessed: float = field(default_factory=time.time)

    @property
    def is_large(self) -> bool:
        return self.pages_total > config.LARGE_DOC_PAGE_THRESHOLD

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at is None:
            return None
        end = self.finished_at or time.time()
        return round(end - self.started_at, 2)

    def release_working_memory(self) -> None:
        """Drop the PDF and per-page text once they are no longer needed."""
        self.pdf_bytes = b""
        self.pages = {}

    def to_status_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "pages_total": self.pages_total,
            "pages_done": self.pages_done,
            "pages_failed": sorted(self.pages_failed),
            "doc_title": self.doc_title,
            "priority": "low" if self.priority == JobPriority.LOW else "high",
            "created_at": self.created_at,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "error_code": self.error_code,
            "result_available": self.status.has_result and self.result is not None,
            "result_expires_at": self.result_expires_at,
        }


# --------------------------------------------------------------------------
# API schemas
# --------------------------------------------------------------------------
class SubmitResponse(BaseModel):
    job_id: str
    status: str
    pages_total: int
    priority: str
    poll_url: str
    result_url: str


class StatusResponse(BaseModel):
    job_id: str
    status: str
    pages_total: int
    pages_done: int
    pages_failed: List[int] = []
    doc_title: str = ""
    priority: str = "high"
    created_at: float = 0.0
    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    result_available: bool = False
    result_expires_at: Optional[float] = None


class ResultResponse(BaseModel):
    job_id: str
    status: str
    documents: List[dict]
    pages_failed: List[int] = []


class ErrorResponse(BaseModel):
    error: str
    code: str
    detail: Optional[str] = None
