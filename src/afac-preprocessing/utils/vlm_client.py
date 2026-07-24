"""
vlm_client.py — Unified OpenAI client for all VLM/embedding calls in the pipeline.

Before this module, each VLM script built its own HTTP client (httpx sync, httpx
async, requests, or openai) with its own retry logic:
  - description_image_context.py : requests, no retry
  - url_tuning_vlm.py             : httpx.AsyncClient, homemade retry (3 attempts, 15s*n)
  - markdown_control_vlm.py       : httpx.AsyncClient (via the old utils/vlm_client.py),
                                             homemade retry (_should_retry, _MAX_RETRIES, _RETRY_DELAYS)
  - enhancement_metadata.py       : openai.OpenAI, no explicit retry
  - embedding_metadata.py /
    hyq_embedding_doc.py          : openai.OpenAI, duplicated generate_embedding

This module replaces all of that with a pair of openai clients (sync + async), relying on
the SDK's built-in retry (max_retries=3: connection, 408/409/429, 5xx, with backoff) instead
of retry loops rewritten in each script.

No disk cache here (deliberate): every call actually hits the VLM/embedding
endpoint on each run.
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlparse, urlunparse

import httpx
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel

_log = logging.getLogger(__name__)

_T = TypeVar("_T", bound=BaseModel)

DEFAULT_VISION_MAX_TOKENS = 8192
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 120.0
_ENABLE_THINKING_FALSE = {"chat_template_kwargs": {"enable_thinking": False}}  # avoids content=null (Qwen3)
_ENABLE_THINKING_TRUE = {"chat_template_kwargs": {"enable_thinking": True}}


# Configuration
@dataclass(frozen=True)
class VlmConfig:
    """Immutable configuration, built from the environment (VLM_URL, EMBEDDING_URL, ...)."""
    ca_path: str
    vlm_base_url: str
    vlm_model_name: str
    embedding_base_url: str
    embedding_model_name: str


def _to_base_url(raw_url: str) -> str:
    """Reduces a full endpoint URL (e.g. .../v1/chat/completions) to scheme://host/v1,
    the format expected by the OpenAI SDK's base_url (it appends /chat/completions,
    /embeddings, /models, ... itself). Same logic as enhancement_metadata.py /
    embedding_metadata.py before this fix — centralized here."""
    if not raw_url:
        return ""
    parsed = urlparse(raw_url)
    return urlunparse((parsed.scheme, parsed.netloc, "/v1", "", "", ""))


def build_vlm_config(dotenv_path: Path | None = None) -> VlmConfig:
    """Builds the configuration from the environment (lazy import, see utils.config)."""
    from utils.config import load_vlm_config
    cfg = load_vlm_config(dotenv_path=dotenv_path)
    return VlmConfig(
        ca_path=cfg["CA_PATH"],
        vlm_base_url=_to_base_url(cfg["VLM_URL"]),
        vlm_model_name=cfg["VLM_MODEL_NAME"],
        embedding_base_url=_to_base_url(cfg["EMBEDDING_URL"]),
        embedding_model_name=cfg["EMBEDDING_MODEL_NAME"],
    )


# Client construction
def build_sync_client(
    cfg: VlmConfig, *, timeout: float = DEFAULT_TIMEOUT, max_retries: int = DEFAULT_MAX_RETRIES
) -> OpenAI:
    """Sync client for vision/text calls (chat.completions)."""
    return OpenAI(
        base_url=cfg.vlm_base_url,
        api_key="no-key",
        http_client=httpx.Client(verify=cfg.ca_path),
        timeout=timeout,
        max_retries=max_retries,
    )


def build_async_client(
    cfg: VlmConfig, *, timeout: float = DEFAULT_TIMEOUT, max_retries: int = DEFAULT_MAX_RETRIES
) -> AsyncOpenAI:
    """Async client for concurrent vision/text calls (url_tuning, markdown_control)."""
    return AsyncOpenAI(
        base_url=cfg.vlm_base_url,
        api_key="no-key",
        http_client=httpx.AsyncClient(verify=cfg.ca_path),
        timeout=timeout,
        max_retries=max_retries,
    )


def build_embedding_client(
    cfg: VlmConfig, *, timeout: float = DEFAULT_TIMEOUT, max_retries: int = DEFAULT_MAX_RETRIES
) -> OpenAI:
    """Sync client for embeddings — host distinct from the VLM chat one (EMBEDDING_URL != VLM_URL)."""
    return OpenAI(
        base_url=cfg.embedding_base_url,
        api_key="no-key",
        http_client=httpx.Client(verify=cfg.ca_path),
        timeout=timeout,
        max_retries=max_retries,
    )


# Connectivity
def check_vlm_connectivity(client: OpenAI, model_name: str) -> bool:
    """Checks that the VLM is reachable AND that the configured model is actually served (GET
    /v1/models) before a long-running job. An OpenAI-compatible gateway can respond to
    /v1/models even if the backend serving model_name is down — so we fail
    explicitly if model_name is absent from the list, rather than logging a plain
    warning and continuing (the pipeline would fail eventually anyway, but in a
    diffuse way, page by page, instead of a clean, immediate stop)."""
    try:
        available = [m.id for m in client.models.list().data]
        _log.info("VLM OK — available models: %s", available or ["(none)"])
        if model_name and model_name not in available:
            _log.error("Configured model '%s' absent from the VLM list: %s", model_name, available)
            return False
        return True
    except Exception:
        _log.exception("VLM unreachable")
        return False


async def check_vlm_connectivity_async(client: AsyncOpenAI, model_name: str) -> bool:
    """Async version — see check_vlm_connectivity()."""
    try:
        resp = await client.models.list()
        available = [m.id for m in resp.data]
        _log.info("VLM OK — available models: %s", available or ["(none)"])
        if model_name and model_name not in available:
            _log.error("Configured model '%s' absent from the VLM list: %s", model_name, available)
            return False
        return True
    except Exception:
        _log.exception("VLM unreachable")
        return False


# High-level calls (vision, structured text, embedding) — the SDK handles transient retries
def vision_completion(
    client: OpenAI,
    model: str,
    prompt: str,
    image_b64: str,
    *,
    max_tokens: int = DEFAULT_VISION_MAX_TOKENS,
    temperature: float = 0.0,
) -> str:
    """Sends image + prompt, returns the text. Raises ValueError if the VLM returns content=null."""
    response = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body=_ENABLE_THINKING_FALSE,
    )
    content = response.choices[0].message.content
    if content is None:
        raise ValueError(f"VLM returned content=null. Full response: {response}")
    return content.strip()


async def vision_completion_async(
    client: AsyncOpenAI,
    model: str,
    prompt: str,
    image_b64: str,
    *,
    max_tokens: int = DEFAULT_VISION_MAX_TOKENS,
    temperature: float = 0.0,
) -> str:
    response = await client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body=_ENABLE_THINKING_FALSE,
    )
    content = response.choices[0].message.content
    if content is None:
        raise ValueError(f"VLM returned content=null. Full response: {response}")
    return content.strip()


def text_completion(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_content: str,
    *,
    temperature: float = 0.0,
) -> str:
    """Free-form text completion, no schema constraint (few-shot prompting)."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=temperature,
        extra_body=_ENABLE_THINKING_FALSE,
    )
    content = response.choices[0].message.content
    if content is None:
        raise ValueError(f"VLM returned content=null. Full response: {response}")
    return content.strip()


async def text_completion_async(
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    user_content: str,
    *,
    temperature: float = 0.0,
) -> str:
    """Async counterpart of text_completion() — free-form text completion, no schema
    constraint, thinking disabled (see _ENABLE_THINKING_FALSE)."""
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=temperature,
        extra_body=_ENABLE_THINKING_FALSE,
    )
    content = response.choices[0].message.content
    if content is None:
        raise ValueError(f"VLM returned content=null. Full response: {response}")
    return content.strip()


def text_completion_thinking(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_content: str,
    *,
    temperature: float = 0.0,
) -> str:
    """Free-form text completion with Qwen3 thinking mode enabled (chat_template_kwargs.
    enable_thinking=True), for prompts that benefit from reasoning before answering (e.g.
    open-ended concept extraction). Every other call in this module disables thinking to
    avoid content=null — see _ENABLE_THINKING_FALSE. Do not combine this with structured
    output (text_completion_structured): thinking + response_format is the exact
    combination the rest of this module avoids."""
    # print("CLIENT TIMEOUT: ", client.timeout)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=temperature,
        extra_body=_ENABLE_THINKING_TRUE,
    )
    content = response.choices[0].message.content
    if content is None:
        raise ValueError(f"VLM returned content=null (thinking mode). Full response: {response}")
    return content.strip()


def text_completion_structured(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_content: str,
    response_format: type[_T],
) -> _T:
    """Structured output (Pydantic) — summary/intent/hyq (enhancement_metadata.py)."""
    response = client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format=response_format,
        extra_body=_ENABLE_THINKING_FALSE,
    )
    return response.choices[0].message.parsed


def get_embedding(client: OpenAI, model: str, text: str) -> list[float]:
    response = client.embeddings.create(input=text, model=model)
    return response.data[0].embedding


def embedding_to_string(embedding: list[float]) -> str:
    """Ex: [0.4, 0.8, 1.5] -> \"0.4, 0.8, 1.5\" (EMBEDDING column format in the CSVs)."""
    return str(embedding).replace("[", "").replace("]", "")