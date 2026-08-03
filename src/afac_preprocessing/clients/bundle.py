"""Holds the one event loop and the one VLM/embedding client shared by a run.

Without this, each async step would open its own event loop and its own
client, which is wasteful and breaks connection pooling. ClientBundle
creates them once, hands them out to every step, and closes them when the
run is done.
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
    """Sole owner of the async loop and the run's clients."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._loop: asyncio.AbstractEventLoop | None = None
        self._vlm_raw: AsyncOpenAI | None = None
        self._embedding_raw: AsyncOpenAI | None = None
        self._vlm: AsyncVlmClient | None = None
        self._embedding: AsyncEmbeddingClient | None = None

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """THE loop of the run — created on first use, never replaced."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def run_async(self, coro: Awaitable[_T]) -> _T:
        """Runs *coro* on the run's loop (⚠ never ``asyncio.run()``)."""
        return self.loop.run_until_complete(coro)

    def vlm(self) -> AsyncVlmClient:
        """VLM client of the run — raises ``VlmUnavailable``, never returns None."""
        if self._vlm is None:
            from .openai_client import OpenAIVlmClient, build_async_client

            if not str(self._settings.vlm_url):
                raise VlmUnavailable("VLM_URL is not configured")
            # timeout=180: the client is shared across the whole run (P7) and
            # must accommodate the most demanding need — markdown-control
            # historically used 180s where other steps used 120s.
            self._vlm_raw = build_async_client(self._settings, timeout=180.0)
            self._vlm = OpenAIVlmClient(
                self._vlm_raw,
                self._settings.vlm_model_name,
                temperature=self._settings.vlm_temperature,
            )
        return self._vlm

    def embeddings(self) -> AsyncEmbeddingClient:
        """Embedding client of the run — raises ``EmbeddingUnavailable`` if absent."""
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
        """Cleanly closes the clients then the loop."""
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
