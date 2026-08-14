"""
Local tokenizer for the embedding model.

Replaces the previous per-page HTTP round-trip to the vLLM `/tokenize`
endpoint. Tokenization is CPU-bound regardless of where it runs, so doing it
in-process removes both the network latency and contention on the GPU pod's
CPU.
"""

import asyncio
import logging
import threading
from pathlib import Path
from typing import List, Optional

from . import config

logger = logging.getLogger("pdf-ocr-pipeline.tokenization")

_tokenizer = None
_lock = threading.Lock()


def _load_tokenizer():
    """Load the bundled tokenizer.json exactly once (thread-safe)."""
    global _tokenizer
    if _tokenizer is not None:
        return _tokenizer

    with _lock:
        if _tokenizer is not None:
            return _tokenizer

        path = Path(config.TOKENIZER_DIR) / "tokenizer.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Tokenizer file not found at {path}. The pod has no internet "
                "access, so tokenizer.json must be bundled in the model archive."
            )

        from tokenizers import Tokenizer  # imported lazily to keep startup cheap

        tok = Tokenizer.from_file(str(path))
        # We only ever count/slice content tokens; special tokens would skew
        # the budget arithmetic.
        tok.no_truncation()
        tok.no_padding()
        _tokenizer = tok
        logger.info("Loaded local tokenizer from %s", path)
        return _tokenizer


def warm_up() -> bool:
    """Eagerly load the tokenizer at startup so the first request is not slow."""
    try:
        _load_tokenizer()
        count_tokens_sync("warm up")
        return True
    except Exception:
        logger.exception("Tokenizer warm-up failed")
        return False


def count_tokens_sync(text: str) -> int:
    if not text:
        return 0
    tok = _load_tokenizer()
    return len(tok.encode(text, add_special_tokens=False).ids)


def encode_ids_sync(text: str) -> List[int]:
    if not text:
        return []
    tok = _load_tokenizer()
    return tok.encode(text, add_special_tokens=False).ids


def decode_sync(ids: List[int]) -> str:
    if not ids:
        return ""
    tok = _load_tokenizer()
    return tok.decode(ids, skip_special_tokens=True)


async def count_tokens(text: str) -> int:
    """Async wrapper. Long documents are offloaded to keep the loop responsive."""
    if not text:
        return 0
    if len(text) < 20_000:
        # Short enough that the thread hand-off costs more than the work.
        return count_tokens_sync(text)
    return await asyncio.to_thread(count_tokens_sync, text)


def split_by_tokens(text: str, budget: int) -> List[str]:
    """
    Split `text` into pieces of at most `budget` tokens.

    Only used for the pathological case of a single page whose content exceeds
    a whole chunk budget; normal pages are packed by the chunker instead.
    """
    if budget <= 0:
        return [text]

    ids = encode_ids_sync(text)
    if len(ids) <= budget:
        return [text]

    pieces: List[str] = []
    for start in range(0, len(ids), budget):
        piece = decode_sync(ids[start:start + budget])
        if piece.strip():
            pieces.append(piece)
    return pieces or [text]


def truncate_to_tokens(text: str, budget: int) -> str:
    """Hard-truncate text to a token budget (used as an embedding safety net)."""
    if budget <= 0 or not text:
        return ""
    ids = encode_ids_sync(text)
    if len(ids) <= budget:
        return text
    logger.warning(
        "Truncating text from %d to %d tokens before embedding", len(ids), budget
    )
    return decode_sync(ids[:budget])


def tokenizer_info() -> Optional[dict]:
    try:
        tok = _load_tokenizer()
        return {"vocab_size": tok.get_vocab_size(), "source": config.TOKENIZER_DIR}
    except Exception as exc:  # pragma: no cover - diagnostics only
        return {"error": str(exc)}
