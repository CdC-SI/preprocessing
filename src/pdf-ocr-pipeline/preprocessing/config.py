"""
Central configuration for the PDF OCR pipeline.

Every tunable is an environment variable so it can be changed via the
`pdf-ocr-pipeline-env` secret without rebuilding the image or re-uploading
the model bundle.
"""

import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------
# Model endpoints
# --------------------------------------------------------------------------
VLM_URL = _str("VLM_URL")
VLM_MODEL_NAME = _str("VLM_MODEL_NAME")
LLM_URL = _str("LLM_URL")
LLM_MODEL_NAME = _str("LLM_MODEL_NAME")
EMBEDDING_URL = _str("EMBEDDING_URL")
EMBEDDING_MODEL_NAME = _str("EMBEDDING_MODEL_NAME")
AUTH_TOKEN = _str("AUTH_TOKEN")

# Upstream HTTP timeout for a single model call (seconds).
MODEL_HTTP_TIMEOUT = _float("MODEL_HTTP_TIMEOUT", 300.0)
VERIFY_SSL = _bool("VERIFY_SSL", False)


# --------------------------------------------------------------------------
# Tokenizer (local, bundled)
# --------------------------------------------------------------------------
# Directory holding tokenizer.json / tokenizer_config.json for the *embedding*
# model (Qwen3-Embedding-0.6B). Bundled in the model archive; the pod has no
# internet access so this can never be downloaded at runtime.
TOKENIZER_DIR = _str(
    "TOKENIZER_DIR",
    str(Path(__file__).resolve().parent.parent / "tokenizer"),
)

# Hard ceiling of the embedding model. Inputs longer than this are truncated
# server-side, so we must never exceed it.
MAX_EMBEDDING_TOKENS = _int("MAX_EMBEDDING_TOKENS", 32768)

# Working chunk size. Deliberately far below MAX_EMBEDDING_TOKENS: a single
# vector over 32k tokens is far too coarse for useful retrieval. Tune this
# during retrieval evaluation.
CHUNK_TOKEN_BUDGET = _int("CHUNK_TOKEN_BUDGET", 5000)

# Reserved space for the document summary + chunk summary (+ neighbour
# summaries) that get prefixed to the chunk before embedding. Without this
# reservation the embedding call silently truncates the tail of every chunk.
SUMMARY_HEADROOM_TOKENS = _int("SUMMARY_HEADROOM_TOKENS", 1024)


def effective_chunk_budget() -> int:
    """Token budget available for raw page content within one chunk."""
    budget = min(CHUNK_TOKEN_BUDGET, MAX_EMBEDDING_TOKENS - SUMMARY_HEADROOM_TOKENS)
    return max(budget, 256)


# --------------------------------------------------------------------------
# PDF rendering
# --------------------------------------------------------------------------
# Image token cost in the VLM is driven by resolution, so DPI is the single
# biggest GPU-cost lever. Benchmark 110/130/150 before settling.
PDF_RENDER_DPI = _int("PDF_RENDER_DPI", 150)
# 85 rather than 75: JPEG artefacts land on the high-frequency edges that
# character recognition depends on.
JPEG_QUALITY = _int("JPEG_QUALITY", 85)
# Cap the longest edge to avoid pathological page sizes (A0 plans etc.).
MAX_IMAGE_EDGE_PX = _int("MAX_IMAGE_EDGE_PX", 2000)


# --------------------------------------------------------------------------
# Upload limits
# --------------------------------------------------------------------------
MAX_UPLOAD_BYTES = _int("MAX_UPLOAD_BYTES", 10 * 1024 * 1024)  # 10 MB
MAX_PAGES = _int("MAX_PAGES", 1000)
# Above this page count the legacy `:predict` endpoint refuses the request and
# points the caller at the async /jobs API.
LEGACY_MAX_PAGES = _int("LEGACY_MAX_PAGES", 10)
LEGACY_TIMEOUT_SECONDS = _float("LEGACY_TIMEOUT_SECONDS", 540.0)


# --------------------------------------------------------------------------
# Queue / worker pool
# --------------------------------------------------------------------------
# Documents at or below this page count are treated as interactive and get the
# high-priority queue tier.
LARGE_DOC_PAGE_THRESHOLD = _int("LARGE_DOC_PAGE_THRESHOLD", 5)

# Global cap on OCR requests in flight towards the VLM. The VLM runs with
# --max-num-seqs=17 shared with the translation service, so this reserves the
# bulk of the capacity for translation.
VLM_MAX_CONCURRENCY = _int("VLM_MAX_CONCURRENCY", 4)
# Large documents get a tighter budget so one big upload cannot consume the
# whole OCR allowance while small interactive uploads wait.
OCR_LARGE_DOC_CONCURRENCY = _int("OCR_LARGE_DOC_CONCURRENCY", 2)

# Number of in-process worker tasks draining the queue. Slightly above
# VLM_MAX_CONCURRENCY so that CPU-bound work (rendering, tokenizing) can
# overlap with GPU waits.
WORKER_COUNT = _int("WORKER_COUNT", VLM_MAX_CONCURRENCY + 2)

# A low-priority job queued for longer than this is promoted so that a steady
# stream of small uploads can never starve a large document indefinitely.
AGING_PROMOTION_SECONDS = _float("AGING_PROMOTION_SECONDS", 900.0)  # 15 min

# Per-page retry policy for transient upstream failures.
PAGE_MAX_ATTEMPTS = _int("PAGE_MAX_ATTEMPTS", 3)
PAGE_RETRY_BASE_DELAY = _float("PAGE_RETRY_BASE_DELAY", 2.0)

# Embedding requests are sliced so one document cannot build a single enormous
# request body.
EMBEDDING_BATCH_SIZE = _int("EMBEDDING_BATCH_SIZE", 8)


# --------------------------------------------------------------------------
# Job store retention
# --------------------------------------------------------------------------
RESULT_TTL_SECONDS = _float("RESULT_TTL_SECONDS", 3600.0)  # 1 hour
METADATA_TTL_SECONDS = _float("METADATA_TTL_SECONDS", 86400.0)  # 24 hours
STORE_SWEEP_INTERVAL = _float("STORE_SWEEP_INTERVAL", 60.0)
# LRU eviction ceiling for retained result payloads.
MAX_RESULT_BYTES = _int("MAX_RESULT_BYTES", 2 * 1024 * 1024 * 1024)  # 2 GB


# --------------------------------------------------------------------------
# Feature flags (Phase 4)
# --------------------------------------------------------------------------
TEXT_LAYER_EXTRACTION_ENABLED = _bool("TEXT_LAYER_EXTRACTION_ENABLED", False)
CONTEXTUAL_RETRIEVAL_ENABLED = _bool("CONTEXTUAL_RETRIEVAL_ENABLED", False)


# --------------------------------------------------------------------------
# Instance identity
# --------------------------------------------------------------------------
# Job state lives in memory only. Embedding a per-process instance id in every
# job id lets the API distinguish "unknown job" (404) from "this job belonged
# to a previous process, please resubmit" (410 Gone) after a restart.
INSTANCE_ID = _str("INSTANCE_ID") or uuid.uuid4().hex[:8]

SERVICE_NAME = _str("SERVICE_NAME", "user-pdf-preprocessing")
LOG_LEVEL = _str("LOG_LEVEL", "INFO")
