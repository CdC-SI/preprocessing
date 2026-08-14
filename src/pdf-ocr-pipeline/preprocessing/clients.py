"""
Upstream model clients (VLM, LLM, embeddings) and the global VLM concurrency
budget.

Concurrency control matters as much as correctness here: the VLM is shared
with the translation service, which runs with --max-num-seqs=17. Capping our
in-flight OCR requests guarantees translation always has capacity, and the
vLLM `priority` field (Phase 3) then decides ordering within the waiting
queue.
"""

import asyncio
import logging
from typing import List, Optional

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel

from . import config
from prompts.prompts import LLM_PROMPT, OCR_PROMPT

logger = logging.getLogger("pdf-ocr-pipeline.clients")


class VLMResponseModel(BaseModel):
    ocr_content: str = ""


class LLMResponseModel(BaseModel):
    language: str = ""
    summary: str = ""


_http_client: Optional[httpx.AsyncClient] = None
_vlm_client: Optional[AsyncOpenAI] = None
_llm_client: Optional[AsyncOpenAI] = None
_embedding_client: Optional[AsyncOpenAI] = None

# Global budget of OCR requests in flight towards the VLM. With a single
# replica (enforced by the manifest) this is a genuine global cap.
_vlm_semaphore: Optional[asyncio.Semaphore] = None
# Tighter sub-budget so a large document cannot consume the whole OCR
# allowance while small interactive uploads wait behind it.
_large_doc_semaphore: Optional[asyncio.Semaphore] = None

_inflight_vlm = 0


def init_clients() -> None:
    """Build the shared HTTP clients and semaphores. Call once, from the loop."""
    global _http_client, _vlm_client, _llm_client, _embedding_client
    global _vlm_semaphore, _large_doc_semaphore

    if _http_client is not None:
        return

    headers = {"Authorization": f"Bearer {config.AUTH_TOKEN}"} if config.AUTH_TOKEN else {}
    _http_client = httpx.AsyncClient(
        timeout=config.MODEL_HTTP_TIMEOUT,
        verify=config.VERIFY_SSL,
        headers=headers,
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
    )

    def _client(base_url: str) -> AsyncOpenAI:
        return AsyncOpenAI(
            base_url=base_url,
            api_key=config.AUTH_TOKEN or "not-used",
            http_client=_http_client,
            max_retries=0,  # retries are handled per page by the worker
        )

    _vlm_client = _client(config.VLM_URL)
    _llm_client = _client(config.LLM_URL)
    _embedding_client = _client(config.EMBEDDING_URL)

    _vlm_semaphore = asyncio.Semaphore(config.VLM_MAX_CONCURRENCY)
    _large_doc_semaphore = asyncio.Semaphore(config.OCR_LARGE_DOC_CONCURRENCY)

    logger.info(
        "Model clients ready (vlm_concurrency=%d, large_doc_concurrency=%d)",
        config.VLM_MAX_CONCURRENCY,
        config.OCR_LARGE_DOC_CONCURRENCY,
    )


async def close_clients() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def inflight_vlm_requests() -> int:
    return _inflight_vlm


class _VlmSlot:
    """Acquires the global VLM budget, plus the large-doc sub-budget if needed."""

    def __init__(self, is_large_doc: bool):
        self._is_large = is_large_doc

    async def __aenter__(self):
        global _inflight_vlm
        if self._is_large:
            await _large_doc_semaphore.acquire()
        await _vlm_semaphore.acquire()
        _inflight_vlm += 1
        return self

    async def __aexit__(self, *exc_info):
        global _inflight_vlm
        _inflight_vlm -= 1
        _vlm_semaphore.release()
        if self._is_large:
            _large_doc_semaphore.release()
        return False


def vlm_slot(is_large_doc: bool = False) -> _VlmSlot:
    return _VlmSlot(is_large_doc)


async def call_vlm(data_url: str, priority: Optional[int] = None) -> VLMResponseModel:
    """
    OCR a single rendered page. Raises on failure so the worker can retry;
    the caller decides when to give up on a page.
    """
    messages = [
        {"role": "system", "content": "You are an expert at parsing PDF documents."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": OCR_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    extra_body = {}
    if priority is not None:
        # vLLM runs with --scheduling-policy priority; lower value = higher
        # priority. Translation uses the default 0, so any positive value here
        # keeps it ahead of OCR traffic.
        extra_body["priority"] = priority

    res = await _vlm_client.chat.completions.parse(
        model=config.VLM_MODEL_NAME,
        messages=messages,
        response_format=VLMResponseModel,
        top_p=0.8,
        temperature=0.7,
        presence_penalty=1.5,
        extra_body=extra_body or None,
    )
    parsed = res.choices[0].message.parsed
    return parsed if parsed is not None else VLMResponseModel()


async def call_llm(text: str, priority: Optional[int] = None) -> LLMResponseModel:
    """Summarise + detect language for one chunk (always well within context)."""
    messages = [
        {
            "role": "developer",
            "content": [{"type": "text", "text": LLM_PROMPT.format(doc=text)}],
        }
    ]

    extra_body = {}
    if priority is not None:
        extra_body["priority"] = priority

    try:
        res = await _llm_client.chat.completions.parse(
            model=config.LLM_MODEL_NAME,
            messages=messages,
            response_format=LLMResponseModel,
            temperature=0.0,
            extra_body=extra_body or None,
        )
        parsed = res.choices[0].message.parsed
        return parsed if parsed is not None else LLMResponseModel()
    except Exception:
        logger.exception("LLM call failed for a chunk of %d chars", len(text))
        return LLMResponseModel()


async def embed_batch(texts: List[str]) -> List[List[float]]:
    """Embed a slice of chunks. Returns [] per text on failure."""
    if not texts:
        return []
    try:
        res = await _embedding_client.embeddings.create(
            model=config.EMBEDDING_MODEL_NAME,
            input=texts,
        )
        ordered = sorted(res.data, key=lambda d: d.index)
        return [item.embedding for item in ordered]
    except Exception:
        logger.exception("Embedding call failed for a batch of %d text(s)", len(texts))
        return [[] for _ in texts]
