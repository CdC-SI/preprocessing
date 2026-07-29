"""PipelineContext — l'état d'un run, explicite et immuable.

Remplace la mutation d'``os.environ`` entre étapes (``_set_doc_env``).
Le contexte ne construit jamais de client lui-même : il délègue au
``ClientBundle``, qui possède LA boucle et LES clients du run (piège P7).
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


@dataclass(frozen=True)
class PipelineContext:
    """Tout ce qu'une étape a le droit de connaître d'un run."""

    settings: Settings
    workspace: DocumentWorkspace
    clients: ClientBundle = field(repr=False)
    dry_run: bool = False

    def vlm(self) -> AsyncVlmClient:
        """Client VLM du run — lève ``VlmUnavailable``, ne renvoie jamais None."""
        return self.clients.vlm()

    def embeddings(self) -> AsyncEmbeddingClient:
        """Client d'embedding du run — lève ``EmbeddingUnavailable`` si absent."""
        return self.clients.embeddings()

    def run_async(self, coro: Awaitable[_T]) -> _T:
        """Exécute *coro* sur LA boucle du run, celle que possède le ClientBundle.

        ⚠ Jamais ``asyncio.run()`` dans une étape (piège P7) : chaque étape qui
        ouvrirait sa propre boucle détruirait le pool de connexions httpx.
        """
        return self.clients.run_async(coro)

    @classmethod
    def for_pdf(
        cls,
        pdf: Path,
        settings: Settings,
        *,
        clients: ClientBundle | None = None,
        dry_run: bool = False,
    ) -> PipelineContext:
        """Contexte d'un document. En batch, passer le MÊME ``clients`` à chaque
        document pour partager boucle et connexions sur tout le run."""
        workspace = DocumentWorkspace.for_document(pdf, settings)
        return cls(
            settings=settings,
            workspace=workspace,
            clients=clients if clients is not None else ClientBundle(settings),
            dry_run=dry_run,
        )
