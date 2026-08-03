"""Fake VLM and embedding clients for tests: no network calls, just fixed
responses. Each call is recorded so tests can check what was asked of them.
"""

from __future__ import annotations
from typing import TypeVar
from pydantic import BaseModel
from .base import DEFAULT_VISION_MAX_TOKENS

_T = TypeVar("_T", bound=BaseModel)


class FakeVlmClient:
    """Fake AsyncVlmClient: fixed responses, calls recorded.

    The methods are ``async def`` without ``await``: this is intentional —
    the client contract is async-only and the double must
    stay substitutable for the real client. There simply is no I/O to wait on.
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
        # The kwargs are recorded so tests can verify what the step actually
        # requested from the client.
        self.calls.append(("vision_completion", (prompt, image_b64, max_tokens, temperature)))
        return self.vision_response

    async def text_completion_structured(
        self,
        system_prompt: str,
        user_content: str,
        response_format: type[_T],
    ) -> _T:
        self.calls.append(("text_completion_structured", (system_prompt, user_content)))
        # Builds an instance of the requested schema with the values
        # supplied by the test (structured_factory), otherwise model defaults.
        return response_format.model_validate(self.structured_factory)

    async def check_connectivity(self) -> bool:
        self.calls.append(("check_connectivity", ()))
        return self.reachable


class FakeEmbeddingClient:
    """Fake AsyncEmbeddingClient: fixed vector, calls recorded."""

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
