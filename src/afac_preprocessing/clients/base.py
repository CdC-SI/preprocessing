"""Protocols des clients modèle — async-first, il n'y a pas de variante sync
dans le contrat (contrainte C2 : tous les appels VLM/embedding sont asynchrones).
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

_T = TypeVar("_T", bound=BaseModel)

DEFAULT_VISION_MAX_TOKENS = 8192


@runtime_checkable
class AsyncVlmClient(Protocol):
    """Appels VLM (vision + texte structuré) utilisés par les 6 étapes modèle."""

    async def vision_completion(
        self,
        prompt: str,
        image_b64: str,
        *,
        max_tokens: int = DEFAULT_VISION_MAX_TOKENS,
        temperature: float = 0.0,
    ) -> str: ...

    async def text_completion_structured(
        self,
        system_prompt: str,
        user_content: str,
        response_format: type[_T],
    ) -> _T: ...

    async def check_connectivity(self) -> bool: ...


@runtime_checkable
class AsyncEmbeddingClient(Protocol):
    """Appels d'embedding (metadata-generation, hyq-embedding)."""

    async def get_embedding(self, text: str) -> list[float]: ...

    async def check_connectivity(self) -> bool: ...
