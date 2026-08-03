"""PipelineContext, the state of a run, explicit and immutable.

Replaces the mutation of os.environ between steps (_set_doc_env).
The context never builds clients itself: 
it delegates to the ClientBundle, which owns THE loop and THE clients for the run.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from .clients.base import AsyncEmbeddingClient, AsyncVlmClient
from .clients.bundle import ClientBundle
from .settings import Settings
from .workspace import DocumentWorkspace

_T = TypeVar("_T")


@dataclass(frozen=True) # Once created, its attributes cannot be modified.
class PipelineContext:
    """Everything a step is allowed to know about a run."""

    settings: Settings
    workspace: DocumentWorkspace
    clients: ClientBundle = field(repr=False)
    dry_run: bool = False

    def vlm(self) -> AsyncVlmClient:
        """Run VLM client, raises VlmUnavailable; never returns None."""
        return self.clients.vlm()

    def embeddings(self) -> AsyncEmbeddingClient:
        """Run embedding client, raises EmbeddingUnavailable if unavailable."""
        return self.clients.embeddings()

    def run_async(self, coro: Awaitable[_T]) -> _T:
        """Executes coro (co-routine) on THE run loop, the one owned by the ClientBundle.
        Never use asyncio.run() inside a step: each step that
        opened its own event loop would destroy the httpx connection pool.
        """
        return self.clients.run_async(coro)

    @classmethod
    def for_pdf(
        cls, # https://realpython.com/ref/glossary/cls/
        pdf: Path,
        settings: Settings,
        *,
        clients: ClientBundle | None = None,
        dry_run: bool = False,
    ) -> PipelineContext:
        """Document context. In batch mode, pass the SAME clients instance to each
        document to share the event loop and connections across the entire run."""
        workspace = DocumentWorkspace.for_document(pdf, settings)
        return cls(
            settings=settings,
            workspace=workspace,
            clients=clients if clients is not None else ClientBundle(settings),
            dry_run=dry_run,
        )
