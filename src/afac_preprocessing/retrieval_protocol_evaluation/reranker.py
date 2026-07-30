"""Reranker API wrapper — scores (query, documents) pairs via the /score endpoint.

Lot 9 : ``sys.path.insert`` + ``utils.config`` (façade supprimée au lot 8)
remplacés par ``Settings``, et l'appel HTTP passe de ``requests`` à
``httpx.AsyncClient`` — tous les appels réseau du dépôt sont asynchrones
(exigence métier).

Le reranker n'est pas un endpoint OpenAI : ``/score`` est propre à vLLM, d'où
un appel HTTP direct plutôt qu'un client du ``ClientBundle``.
"""

from __future__ import annotations

import logging

import httpx

from ..exceptions import ConfigError
from ..settings import Settings, default_dotenv

_log = logging.getLogger(__name__)

# Prompt format required by the vLLM reranker (same as test_reranker_embedding.py)
_PREFIX = (
    "<|im_start|>system\n"
    "Judge whether the Document meets the requirements based on the Query and the "
    "Instruct provided. Note that the answer can only be \"yes\" or \"no\"."
    "<|im_end|>\n<|im_start|>user\n"
)
_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
_INSTRUCTION = (
    "Given a user search query, determine whether the document can answer the query or not."
)


def _load_settings() -> Settings | None:
    """Settings, ou None si la configuration est absente.

    Le None est délibéré et conservé du code d'origine : le reranker est
    optionnel dans le protocole d'évaluation (``evaluate_doc(..., use_reranker=False)``
    est le cas nominal), une config manquante ne doit donc pas faire échouer
    une évaluation.
    """
    try:
        return Settings.from_dotenv(default_dotenv())
    except ConfigError as exc:
        _log.warning("Could not load reranker config: %s", exc)
        return None


_SETTINGS: Settings | None = _load_settings()


async def rerank(query: str, doc_texts: list[str], timeout: int = 30) -> list[float] | None:
    """
    Score each (query, doc_text) pair with the reranker.

    Returns a list of floats in the same order as doc_texts, or None if the
    reranker is unavailable or the call fails.
    """
    if _SETTINGS is None or _SETTINGS.reranker_url is None:
        _log.warning("Reranker config unavailable — skipping reranker call.")
        return None

    query_prompt = f"{_PREFIX}<Instruct>: {_INSTRUCTION}\n<Query>: {query}\n"
    doc_prompts = [f"<Document>: {doc}{_SUFFIX}" for doc in doc_texts]

    try:
        async with httpx.AsyncClient(
            verify=_SETTINGS.resolved_ca_path, timeout=timeout
        ) as client:
            resp = await client.post(
                f"{str(_SETTINGS.reranker_url).rstrip('/')}/score",
                json={
                    "model": _SETTINGS.reranker_model_name,
                    "text_1": [query_prompt],
                    "text_2": doc_prompts,
                    "truncate_prompt_tokens": -1,
                },
            )
            resp.raise_for_status()
            return [item["score"] for item in resp.json()["data"]]
    except Exception as exc:
        _log.exception("Reranker call failed: %s", exc)
        return None
