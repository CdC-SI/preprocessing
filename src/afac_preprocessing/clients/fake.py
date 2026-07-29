"""Doubles de test async — zéro réseau, réponses fixes.

C'est le seul mécanisme d'hermétisme des tests (pas de cache, pas de rejeu —
contrainte C1). Les doubles enregistrent les appels reçus pour permettre les
assertions des tests de contrat.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from .base import DEFAULT_VISION_MAX_TOKENS

_T = TypeVar("_T", bound=BaseModel)


class FakeVlmClient:
    """AsyncVlmClient factice : réponses fixes, appels enregistrés.

    Les méthodes sont ``async def`` sans ``await`` : c'est voulu — le contrat
    des clients est async-only (contrainte C2) et le double doit rester
    substituable au vrai client. Il n'y a simplement aucune I/O à attendre.
    """

    def __init__(
        self,
        *,
        vision_response: str = "fake image description",
        structured_factory: dict[str, object] | None = None,
        reachable: bool = True,
    ) -> None:
        self.vision_response = vision_response
        self.structured_factory = structured_factory or {}
        self.reachable = reachable
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def vision_completion(
        self,
        prompt: str,
        image_b64: str,
        *,
        max_tokens: int = DEFAULT_VISION_MAX_TOKENS,
        temperature: float = 0.0,
    ) -> str:
        # Les kwargs sont enregistrés pour que les tests puissent vérifier
        # ce que l'étape a réellement demandé au client.
        self.calls.append(("vision_completion", (prompt, image_b64, max_tokens, temperature)))
        return self.vision_response

    async def text_completion_structured(
        self,
        system_prompt: str,
        user_content: str,
        response_format: type[_T],
    ) -> _T:
        self.calls.append(("text_completion_structured", (system_prompt, user_content)))
        # Construit une instance du schéma demandé avec les valeurs fournies
        # par le test (structured_factory), sinon les défauts du modèle.
        return response_format.model_validate(self.structured_factory)

    async def check_connectivity(self) -> bool:
        self.calls.append(("check_connectivity", ()))
        return self.reachable


class FakeEmbeddingClient:
    """AsyncEmbeddingClient factice : vecteur fixe, appels enregistrés."""

    def __init__(
        self,
        *,
        embedding: list[float] | None = None,
        reachable: bool = True,
    ) -> None:
        self.embedding = embedding if embedding is not None else [0.1, 0.2, 0.3]
        self.reachable = reachable
        self.calls: list[str] = []

    async def get_embedding(self, text: str) -> list[float]:
        self.calls.append(text)
        return list(self.embedding)

    async def check_connectivity(self) -> bool:
        return self.reachable
