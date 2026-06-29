"""Reranker API wrapper — scores (query, documents) pairs via the /score endpoint."""
import logging
import sys
from pathlib import Path

import requests

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

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


def _load_cfg() -> dict | None:
    try:
        from utils.config import load_vlm_config
        return load_vlm_config()
    except Exception as exc:
        _log.warning("Could not load reranker config: %s", exc)
        return None


_CFG: dict | None = _load_cfg()


def rerank(query: str, doc_texts: list[str], timeout: int = 30) -> list[float] | None:
    """
    Score each (query, doc_text) pair with the reranker.

    Returns a list of floats in the same order as doc_texts, or None if the
    reranker is unavailable or the call fails.
    """
    if _CFG is None:
        _log.warning("Reranker config unavailable — skipping reranker call.")
        return None

    query_prompt = f"{_PREFIX}<Instruct>: {_INSTRUCTION}\n<Query>: {query}\n"
    doc_prompts = [f"<Document>: {doc}{_SUFFIX}" for doc in doc_texts]

    try:
        resp = requests.post(
            f"{_CFG['RERANKER_URL']}/score",
            json={
                "model": _CFG["RERANKER_MODEL_NAME"],
                "text_1": [query_prompt],
                "text_2": doc_prompts,
                "truncate_prompt_tokens": -1,
            },
            verify=_CFG["CA_PATH"],
            timeout=timeout,
        )
        resp.raise_for_status()
        return [item["score"] for item in resp.json()["data"]]
    except Exception as exc:
        _log.error("Reranker call failed: %s", exc)
        return None