"""DocumentEmbedder — embedding du markdown final d'un document.

Collaborateur de l'étape metadata-generation (piège P4 : ce n'est PAS une
étape du registre). Conversion de ``metadata/embedding_metadata.py``
(vague D) : logique DÉPLACÉE telle quelle ; l'appel passe en async via
``embeddings.get_embedding`` (la fonction écrite au lot 2, piège P6).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..clients.openai_client import embedding_to_string

if TYPE_CHECKING:
    from ..clients.base import AsyncEmbeddingClient
    from ..workspace import DocumentWorkspace

_log = logging.getLogger(__name__)


def read_embed_markdown(workspace: DocumentWorkspace) -> str:
    """
    Lit le markdown final pour un document donné.

    Préfère <doc>_final_embed.md (tables Markdown remplacées par du JSONL,
    produit par markdown_tables_to_jsonl.py --embed-output) s'il existe, sinon
    <doc>_final.md. Rétrocompatible : les documents sans _final_embed.md
    (v1/v2/baseline) sont inchangés.
    """
    for candidate in (workspace.final_embed_markdown, workspace.final_markdown):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return ""


def write_embedding_output(workspace: DocumentWorkspace, embedding: list[float]) -> Path:
    """Écrit le vecteur brut dans metadata/embedding.json (même format)."""
    workspace.metadata_dir.mkdir(parents=True, exist_ok=True)
    workspace.embedding_json.write_text(
        json.dumps(embedding, ensure_ascii=False), encoding="utf-8"
    )
    return workspace.metadata_dir


class DocumentEmbedder:
    """Génère et écrit l'embedding du markdown final — contrat async."""

    def __init__(self, embeddings: AsyncEmbeddingClient, embedding_model_name: str) -> None:
        self._embeddings = embeddings
        self.embedding_model_name = embedding_model_name

    async def run(self, workspace: DocumentWorkspace) -> tuple[str, str]:
        """Équivalent de run_embedding : lit le markdown, génère l'embedding,
        écrit embedding.json, retourne (embedding_string, embedding_model_name)."""
        markdown_content = read_embed_markdown(workspace)
        if not markdown_content:
            raise FileNotFoundError(
                f"Aucun fichier markdown trouvé pour '{workspace.doc_name}' "
                f"dans {workspace.root.parent}"
            )

        _log.info("Génération de l'embedding")
        embedding = await self._embeddings.get_embedding(markdown_content)

        out_dir = write_embedding_output(workspace, embedding)
        _log.info("embedding écrit dans : %s", out_dir / "embedding.json")

        return embedding_to_string(embedding), self.embedding_model_name
