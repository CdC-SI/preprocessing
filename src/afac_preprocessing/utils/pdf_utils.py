"""PDF helpers shared by the steps.

``pdf_page_count`` is the ground truth used to arbitrate page splitting: both
``reorder_doctags.split_pages`` and ``markdown_convert._split_pages`` have to
choose between two unreliable delimiters (<page_footer> can be missing on a
page, <page_break> can be misplaced by Docling), and the real page count is what
settles it.

⚠ Deliberately non-fatal, unlike ``markdown_control._pdf_page_count``. Here the
count only *improves* a heuristic, so an unreadable PDF must degrade to the
historical behavior rather than fail a step. In markdown-control the count IS
the check — returning None there would silently disable the safety net — which
is why that one stays strict and separate.
"""

from __future__ import annotations

import logging
from pathlib import Path

_log = logging.getLogger(__name__)


def pdf_page_count(pdf_path: Path) -> int | None:
    """Real number of pages, or None if the PDF cannot be read."""
    try:
        import fitz  # PyMuPDF

        with fitz.open(str(pdf_path)) as doc:
            return doc.page_count
    except Exception:
        _log.warning(
            "Unreadable page count (%s) — page splitting falls back to its heuristic.",
            pdf_path.name,
        )
        return None
