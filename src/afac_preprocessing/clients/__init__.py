"""Clients VLM/embedding du pipeline — contrat async uniquement (contrainte C2).

⛔ Aucun cache de réponses modèle, nulle part (contrainte C1) : chaque appel
atteint réellement l'endpoint. L'hermétisme des tests vient de ``fake.py``,
pas d'un rejeu.
"""

from .base import AsyncEmbeddingClient, AsyncVlmClient
from .bundle import ClientBundle

__all__ = ["AsyncEmbeddingClient", "AsyncVlmClient", "ClientBundle"]
