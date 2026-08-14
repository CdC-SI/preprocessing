"""
PDF inspection and page rendering.

The previous implementation rendered every page into memory up front, which
for a 500-page document meant several GB of RGB bitmaps before compression.
Pages are now rendered one at a time, on demand, by the worker that is about
to process them.
"""

import base64
import logging
from io import BytesIO
from typing import Optional, Tuple

import fitz  # PyMuPDF
from PIL import Image

from . import config

logger = logging.getLogger("pdf-ocr-pipeline.render")


class PdfError(Exception):
    """Raised for PDFs we cannot or will not process."""

    def __init__(self, message: str, code: str = "invalid_pdf"):
        super().__init__(message)
        self.code = code


def inspect_pdf(pdf_bytes: bytes) -> int:
    """
    Validate an uploaded PDF and return its page count.

    Raises PdfError with a stable `code` so the API layer can map it to a
    meaningful HTTP response.
    """
    if not pdf_bytes:
        raise PdfError("Uploaded file is empty.", code="empty_file")

    if len(pdf_bytes) > config.MAX_UPLOAD_BYTES:
        raise PdfError(
            f"File is {len(pdf_bytes)} bytes, limit is {config.MAX_UPLOAD_BYTES}.",
            code="file_too_large",
        )

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise PdfError(f"File could not be parsed as a PDF: {exc}", code="corrupt_pdf")

    try:
        if doc.needs_pass:
            raise PdfError(
                "PDF is password protected and cannot be processed.",
                code="encrypted_pdf",
            )

        page_count = doc.page_count

        if page_count == 0:
            raise PdfError("PDF contains no pages.", code="empty_pdf")

        if page_count > config.MAX_PAGES:
            raise PdfError(
                f"PDF has {page_count} pages, limit is {config.MAX_PAGES}.",
                code="too_many_pages",
            )

        # PyMuPDF silently repairs many damaged/truncated files. That is what
        # we want (better to salvage content than reject the upload), but it
        # should be visible in the logs when extraction quality is questioned.
        if getattr(doc, "is_repaired", False):
            logger.warning(
                "PDF was damaged and has been auto-repaired; extraction may be "
                "incomplete (%d page(s) recovered)",
                page_count,
            )

        # Touch the first page: a truncated file often opens cleanly but fails
        # on first access, and we would rather fail at submit than mid-job.
        try:
            doc.load_page(0)
        except Exception as exc:
            raise PdfError(f"PDF appears truncated or corrupt: {exc}", code="corrupt_pdf")

        return page_count
    finally:
        doc.close()


def _scale_if_needed(img: Image.Image) -> Image.Image:
    longest = max(img.width, img.height)
    if longest <= config.MAX_IMAGE_EDGE_PX:
        return img
    ratio = config.MAX_IMAGE_EDGE_PX / float(longest)
    new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
    logger.debug("Downscaling page image from %s to %s", img.size, new_size)
    return img.resize(new_size, Image.LANCZOS)


def render_page(
    pdf_bytes: bytes,
    page_index: int,
    dpi: Optional[int] = None,
    quality: Optional[int] = None,
) -> str:
    """
    Render a single 0-based page to a JPEG data URL.

    Blocking / CPU-bound: always call via asyncio.to_thread.
    """
    dpi = dpi or config.PDF_RENDER_DPI
    quality = quality or config.JPEG_QUALITY

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc.load_page(page_index)
        pix = page.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        img = _scale_if_needed(img)

        buf = BytesIO()
        # JPEG rather than PNG: roughly 70% smaller with no measurable OCR
        # accuracy loss at quality 85.
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{encoded}"
    finally:
        doc.close()


def extract_page_text(pdf_bytes: bytes, page_index: int) -> Tuple[str, dict]:
    """
    Pull the embedded text layer for a page, with the statistics needed to
    judge whether it is usable. The quality heuristics themselves land in
    Phase 4; this returns the raw material for them.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc.load_page(page_index)
        text = page.get_text("text") or ""
        rect = page.rect
        stats = {
            "char_count": len(text),
            "page_area": float(rect.width * rect.height),
            "image_count": len(page.get_images(full=True)),
        }
        return text, stats
    except Exception as exc:
        logger.warning("Text layer extraction failed for page %d: %s", page_index, exc)
        return "", {"char_count": 0, "page_area": 0.0, "image_count": 0}
    finally:
        doc.close()
