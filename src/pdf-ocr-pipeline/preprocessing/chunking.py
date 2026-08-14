"""
Chunk assembly and document construction.

The previous implementation tokenized the *accumulated* chunk on every page,
so page N cost N pages of tokenization work and one network round-trip: O(n^2)
overall. Each page is now tokenized exactly once, by the worker that extracts
it, and the chunker merely maintains a running sum.
"""

import datetime
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import config, tokenization

logger = logging.getLogger("pdf-ocr-pipeline.chunking")


@dataclass
class PageResult:
    """Extraction output for a single page."""

    page_number: int  # 1-based, as surfaced in metadata
    text: str = ""
    token_count: int = 0
    source: str = "vlm"  # "vlm" | "text_layer"
    failed: bool = False
    error: Optional[str] = None


@dataclass
class Chunk:
    text: str
    page_numbers: List[int]
    token_count: int
    summary: str = ""
    language: str = ""
    neighbour_context: str = field(default="", repr=False)


def build_chunks(pages: List[PageResult]) -> List[Chunk]:
    """
    Pack consecutive pages into chunks up to the effective token budget.

    Pages are kept in document order and never interleaved. A single page that
    exceeds the whole budget is split on token boundaries.
    """
    budget = config.effective_chunk_budget()
    chunks: List[Chunk] = []

    current_parts: List[str] = []
    current_pages: List[int] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current_parts, current_pages, current_tokens
        if not current_parts:
            return
        chunks.append(
            Chunk(
                text="".join(current_parts).strip(),
                page_numbers=list(current_pages),
                token_count=current_tokens,
            )
        )
        current_parts, current_pages, current_tokens = [], [], 0

    for page in pages:
        if not page.text.strip():
            continue

        page_text = page.text.rstrip() + "\n\n"
        page_tokens = page.token_count

        # Pathological single page: split it on token boundaries and emit each
        # piece as its own chunk.
        if page_tokens > budget:
            flush()
            logger.info(
                "Page %d is %d tokens (budget %d); splitting into sub-chunks",
                page.page_number,
                page_tokens,
                budget,
            )
            for piece in tokenization.split_by_tokens(page_text, budget):
                chunks.append(
                    Chunk(
                        text=piece.strip(),
                        page_numbers=[page.page_number],
                        token_count=tokenization.count_tokens_sync(piece),
                    )
                )
            continue

        if current_tokens + page_tokens > budget:
            flush()

        current_parts.append(page_text)
        current_pages.append(page.page_number)
        current_tokens += page_tokens

    flush()

    logger.info(
        "Chunking produced %d chunk(s) from %d page(s) (budget=%d tokens)",
        len(chunks),
        len(pages),
        budget,
    )
    return chunks


def choose_document_language(chunks: List[Chunk]) -> str:
    """
    Length-weighted majority vote over per-chunk language detections.

    More reliable than the previous single detection, which saw a truncated
    document (or, for large documents, nothing at all).
    """
    scores: Dict[str, int] = {}
    for chunk in chunks:
        lang = (chunk.language or "").strip().lower()
        if not lang:
            continue
        scores[lang] = scores.get(lang, 0) + max(chunk.token_count, 1)

    if not scores:
        return ""
    return max(scores.items(), key=lambda kv: kv[1])[0]


def build_contextualized_text(
    chunks: List[Chunk],
    index: int,
    document_summary: str,
) -> str:
    """
    Assemble the text that actually gets embedded.

    document summary + (neighbour summaries) + chunk summary + chunk content.
    Neighbour summaries are a cheap approximation of contextual retrieval: all
    summaries already exist, so this costs no extra LLM calls.
    """
    chunk = chunks[index]
    parts: List[str] = []

    if document_summary:
        parts.append(document_summary.strip())

    if config.CONTEXTUAL_RETRIEVAL_ENABLED:
        previous = chunks[index - 1].summary if index > 0 else ""
        following = chunks[index + 1].summary if index + 1 < len(chunks) else ""
        if previous:
            parts.append(f"[Preceding context] {previous.strip()}")
        if following:
            parts.append(f"[Following context] {following.strip()}")

    if chunk.summary and chunk.summary.strip() != document_summary.strip():
        parts.append(chunk.summary.strip())

    parts.append(chunk.text)
    combined = "\n\n".join(p for p in parts if p)

    # Safety net: never exceed the embedding model's hard limit.
    return tokenization.truncate_to_tokens(combined, config.MAX_EMBEDDING_TOKENS)


def make_doc(
    chunk: Chunk,
    content: str,
    embedding: List[float],
    doc_title: str,
    user_uuid: str,
    document_language: str,
    document_summary: str,
    dpi: int,
) -> dict:
    """Build one vector-DB document. Output shape is unchanged from v1."""
    metadata = {
        "page_num": ",".join(str(num) for num in chunk.page_numbers),
        "dpi": dpi,
        "language": document_language or chunk.language,
        "summary": document_summary or chunk.summary,
        "chunk_summary": chunk.summary,
        "title": doc_title,
        "source": "user_pdf_upload",
        "privacy": "private",
        "doctype": "rag",
        "last_modification": datetime.datetime.now().strftime("%d.%m.%Y"),
        "user_uuid": user_uuid,
    }

    formatted_embedding = str(embedding).replace("[", "").replace("]", "")

    return {
        "content": content,
        "metadata": metadata,
        "embedding": formatted_embedding,
    }
