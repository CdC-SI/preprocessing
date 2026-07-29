"""Implémentation réelle des clients — déplacement de ``utils/vlm_client.py``.

Corps des fonctions conservés à l'identique (invariant n°1) ; seules les
signatures d'entrée changent (``Settings`` au lieu du dict de config).

Écarts par rapport à ``utils/vlm_client.py``, tous prévus par le plan :
- ➕ ``text_completion_structured_async`` et ``get_embedding_async`` — elles
  n'existaient pas (piège P6), calquées sur leurs jumelles sync et sur
  ``vision_completion_async``.
- ➖ ``text_completion`` / ``text_completion_async`` / ``text_completion_thinking``
  ne sont PAS portées ici : le contrat du noyau est async-only (C2) et leurs
  seuls appelants vivent dans les dossiers hors périmètre (lot 9), qui
  continuent de passer par ``utils/vlm_client.py``.
- Le client d'embedding devient ``AsyncOpenAI`` (C2) — même construction que
  ``build_embedding_client``, variante async.

No disk cache here (deliberate): every call actually hits the VLM/embedding
endpoint on each run (contrainte C1).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, TypeVar
from urllib.parse import urlparse, urlunparse

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel

from .base import DEFAULT_VISION_MAX_TOKENS

if TYPE_CHECKING:
    from ..settings import Settings

_log = logging.getLogger(__name__)

_T = TypeVar("_T", bound=BaseModel)

DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 120.0
_ENABLE_THINKING_FALSE = {"chat_template_kwargs": {"enable_thinking": False}}  # avoids content=null (Qwen3)


def _to_base_url(raw_url: str) -> str:
    """Reduces a full endpoint URL (e.g. .../v1/chat/completions) to scheme://host/v1,
    the format expected by the OpenAI SDK's base_url (it appends /chat/completions,
    /embeddings, /models, ... itself)."""
    if not raw_url:
        return ""
    parsed = urlparse(raw_url)
    return urlunparse((parsed.scheme, parsed.netloc, "/v1", "", "", ""))


# Client construction — corps identiques à utils/vlm_client.py, entrée = Settings
def build_async_client(
    settings: Settings, *, timeout: float = DEFAULT_TIMEOUT, max_retries: int = DEFAULT_MAX_RETRIES
) -> AsyncOpenAI:
    """Async client for concurrent vision/text calls."""
    return AsyncOpenAI(
        base_url=_to_base_url(str(settings.vlm_url)),
        api_key="no-key",
        http_client=httpx.AsyncClient(verify=settings.resolved_ca_path),
        timeout=timeout,
        max_retries=max_retries,
    )


def build_async_embedding_client(
    settings: Settings, *, timeout: float = DEFAULT_TIMEOUT, max_retries: int = DEFAULT_MAX_RETRIES
) -> AsyncOpenAI:
    """Async client for embeddings — host distinct from the VLM chat one
    (EMBEDDING_URL != VLM_URL)."""
    return AsyncOpenAI(
        base_url=_to_base_url(str(settings.embedding_url)),
        api_key="no-key",
        http_client=httpx.AsyncClient(verify=settings.resolved_ca_path),
        timeout=timeout,
        max_retries=max_retries,
    )


# Connectivity — corps identique à check_vlm_connectivity_async
async def check_vlm_connectivity_async(client: AsyncOpenAI, model_name: str) -> bool:
    """Checks that the VLM is reachable AND that the configured model is actually
    served (GET /v1/models) before a long-running job."""
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


# High-level calls — the SDK handles transient retries
async def vision_completion_async(
    client: AsyncOpenAI,
    model: str,
    prompt: str,
    image_b64: str,
    *,
    max_tokens: int = DEFAULT_VISION_MAX_TOKENS,
    temperature: float = 0.0,
) -> str:
    """Sends image + prompt, returns the text. Raises ValueError if the VLM
    returns content=null."""
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


async def text_completion_structured_async(
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    user_content: str,
    response_format: type[_T],
) -> _T:
    """Structured output (Pydantic) — summary/intent/hyq (enhancement_metadata).

    N'existait pas en async (piège P6) : calquée sur text_completion_structured
    (sync) et sur vision_completion_async.
    """
    response = await client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format=response_format,
        extra_body=_ENABLE_THINKING_FALSE,
    )
    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise ValueError(f"VLM returned parsed=null. Full response: {response}")
    return parsed


async def get_embedding_async(client: AsyncOpenAI, model: str, text: str) -> list[float]:
    """N'existait pas en async (piège P6) : calquée sur get_embedding (sync)."""
    response = await client.embeddings.create(input=text, model=model)
    return response.data[0].embedding


def embedding_to_string(embedding: list[float]) -> str:
    """Ex: [0.4, 0.8, 1.5] -> \"0.4, 0.8, 1.5\" (EMBEDDING column format in the CSVs)."""
    return str(embedding).replace("[", "").replace("]", "")


# Adaptateurs vers les Protocols de base.py — délèguent aux fonctions déplacées
class OpenAIVlmClient:
    """Implémente AsyncVlmClient au-dessus d'un AsyncOpenAI partagé (ClientBundle)."""

    def __init__(self, client: AsyncOpenAI, model: str, *, temperature: float = 0.0) -> None:
        self._client = client
        self._model = model
        self._temperature = temperature

    async def vision_completion(
        self,
        prompt: str,
        image_b64: str,
        *,
        max_tokens: int = DEFAULT_VISION_MAX_TOKENS,
        temperature: float = 0.0,
    ) -> str:
        return await vision_completion_async(
            self._client, self._model, prompt, image_b64,
            max_tokens=max_tokens, temperature=temperature,
        )

    async def text_completion_structured(
        self,
        system_prompt: str,
        user_content: str,
        response_format: type[_T],
    ) -> _T:
        return await text_completion_structured_async(
            self._client, self._model, system_prompt, user_content, response_format
        )

    async def check_connectivity(self) -> bool:
        return await check_vlm_connectivity_async(self._client, self._model)


class OpenAIEmbeddingClient:
    """Implémente AsyncEmbeddingClient au-dessus d'un AsyncOpenAI partagé."""

    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    async def get_embedding(self, text: str) -> list[float]:
        return await get_embedding_async(self._client, self._model, text)

    async def check_connectivity(self) -> bool:
        return await check_vlm_connectivity_async(self._client, self._model)
