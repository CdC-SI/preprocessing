"""ClientBundle — LA boucle d'événements et LE client AsyncOpenAI du run.

Piège P7 : 6 étapes async × ``asyncio.run()`` = 6 boucles et 6 clients
``AsyncOpenAI`` par document, pool de connexions httpx détruit à chaque étape.
Le bundle possède **une** boucle et **un** client par cible (VLM, embedding),
construits une fois, fermés à la fin. ``asyncio.run`` est interdit hors de
``cli/main.py`` (invariant n°3) — les étapes passent par ``ctx.run_async()``.

⛔ Aucun cache de réponses ici (contrainte C1).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from types import TracebackType
from typing import TYPE_CHECKING, TypeVar

from ..exceptions import EmbeddingUnavailable, VlmUnavailable
from .base import AsyncEmbeddingClient, AsyncVlmClient

if TYPE_CHECKING:
    from openai import AsyncOpenAI

    from ..settings import Settings

_log = logging.getLogger(__name__)

_T = TypeVar("_T")


class ClientBundle:
    """Propriétaire unique de la boucle async et des clients du run."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._loop: asyncio.AbstractEventLoop | None = None
        self._vlm_raw: AsyncOpenAI | None = None
        self._embedding_raw: AsyncOpenAI | None = None
        self._vlm: AsyncVlmClient | None = None
        self._embedding: AsyncEmbeddingClient | None = None

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """LA boucle du run — créée au premier usage, jamais remplacée."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def run_async(self, coro: Awaitable[_T]) -> _T:
        """Exécute *coro* sur la boucle du run (⚠ jamais ``asyncio.run()``)."""
        return self.loop.run_until_complete(coro)

    def vlm(self) -> AsyncVlmClient:
        """Client VLM du run — lève ``VlmUnavailable``, ne renvoie jamais None."""
        if self._vlm is None:
            from .openai_client import OpenAIVlmClient, build_async_client

            if not str(self._settings.vlm_url):
                raise VlmUnavailable("VLM_URL is not configured")
            # timeout=180 : le client est partagé par tout le run (P7) et doit
            # porter le besoin le plus exigeant — markdown-control utilisait
            # historiquement 180 s là où les autres étapes prenaient 120 s.
            self._vlm_raw = build_async_client(self._settings, timeout=180.0)
            self._vlm = OpenAIVlmClient(
                self._vlm_raw,
                self._settings.vlm_model_name,
                temperature=self._settings.vlm_temperature,
            )
        return self._vlm

    def embeddings(self) -> AsyncEmbeddingClient:
        """Client d'embedding du run — lève ``EmbeddingUnavailable`` si absent."""
        if self._embedding is None:
            from .openai_client import OpenAIEmbeddingClient, build_async_embedding_client

            if self._settings.embedding_url is None:
                raise EmbeddingUnavailable("EMBEDDING_URL is not configured")
            self._embedding_raw = build_async_embedding_client(self._settings)
            self._embedding = OpenAIEmbeddingClient(
                self._embedding_raw, self._settings.embedding_model_name
            )
        return self._embedding

    def close(self) -> None:
        """Ferme proprement les clients puis la boucle."""
        if self._loop is not None and not self._loop.is_closed():
            for raw in (self._vlm_raw, self._embedding_raw):
                if raw is not None:
                    self._loop.run_until_complete(raw.close())
            self._loop.close()
        self._loop = None
        self._vlm = self._vlm_raw = None
        self._embedding = self._embedding_raw = None

    def __enter__(self) -> ClientBundle:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
