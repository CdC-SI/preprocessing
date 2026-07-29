"""Étape url-extraction — extraction des hyperliens (URL, mailto) du PDF.

Conversion du script ``simple_extraction/url_extaction.py`` (vague B).
Fonctions métier DÉPLACÉES telles quelles (invariant n°1).

Uses PyMuPDF to extract the external links of each page and associates
the text of the words whose center lies within the link's rectangle.
Produces a JSONL file — one line per link found.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import fitz  # PyMuPDF
import jsonlines

from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed

if TYPE_CHECKING:
    from ..context import PipelineContext

_log = logging.getLogger(__name__)


# Business logic (pure functions) — déplacées telles quelles
def is_external_link(uri: str | None) -> bool:
    """
    Return True if the URI is an external link (http, https, mailto).

    :param uri: The URI to check.
    :type uri: str | None
    :return: True if the URI starts with "http://", "https://" or "mailto:".
    :rtype: bool
    """
    return bool(uri and uri.startswith(("http://", "https://", "mailto:")))


def get_link_text(link: dict, words: list[tuple]) -> str:
    """
    Return the text of the words whose center lies within the link's rectangle.

    :param link: The link dict (as returned by PyMuPDF's get_links()), containing a "from" rectangle.
    :type link: dict
    :param words: The list of words on the page, as returned by PyMuPDF's get_text("words").
    :type words: list[tuple]
    :return: The concatenated text of the words inside the link's rectangle, or "No text" if none.
    :rtype: str
    """
    rect = link.get("from")
    if not rect:
        return "No text"
    rx0, ry0, rx1, ry1 = rect
    link_words = [
        w[4] for w in words
        if rx0 <= (w[0] + w[2]) / 2 <= rx1 and ry0 <= (w[1] + w[3]) / 2 <= ry1
    ]
    return " ".join(link_words).strip() if link_words else "No text"


def serialize_link(link: dict) -> dict:
    """
    Convert fitz.Rect to a list for JSON serialization.

    :param link: The link dict to serialize.
    :type link: dict
    :return: A copy of the link dict with the "from" rectangle converted to a list.
    :rtype: dict
    """
    link_serializable = link.copy()
    if "from" in link_serializable and isinstance(link_serializable["from"], fitz.Rect):
        link_serializable["from"] = list(link_serializable["from"])
    return link_serializable


def extract_url_links(pdf_path: Path) -> list[dict]:
    """
    Extract all external links from the PDF, page by page.
    Returns a list of dicts with page_number, text, hyperlink, type, details.

    :param pdf_path: Path to the PDF file to process.
    :type pdf_path: Path
    :return: A list of dicts describing each external link found.
    :rtype: list[dict]
    """
    results = []
    with fitz.open(pdf_path) as doc:
        for page_num in range(len(doc)):
            page = doc[page_num]
            links = page.get_links()
            words = page.get_text("words")
            page_links = [
                {
                    "page_number": page_num + 1,
                    "text": get_link_text(link, words),
                    "hyperlink": link.get("uri"),
                    "type": "URI",
                    "details": serialize_link(link),
                }
                for link in links
                if is_external_link(link.get("uri"))
            ]
            if page_links:
                _log.info("  Page %d: %d link(s)", page_num + 1, len(page_links))
            results.extend(page_links)
    return results


def save_links(links: list[dict], output_path: Path) -> None:
    """
    Write the list of links to a JSONL file.

    :param links: The list of link dicts to write.
    :type links: list[dict]
    :param output_path: The path of the output JSONL file.
    :type output_path: Path
    """
    with jsonlines.open(output_path, mode="w") as writer:
        for item in links:
            writer.write(item)


class UrlExtractionStep(PipelineStep):
    """Extrait les hyperliens externes du PDF vers hyperlinks_data_<doc>.jsonl."""

    name = "url-extraction"
    description = "Extraction des hyperliens du PDF"
    requires_vlm = False

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.source_pdf]

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.hyperlinks_jsonl]

    def execute(self, ctx: PipelineContext) -> StepResult:
        pdf_path = ctx.workspace.source_pdf
        output_path = ctx.workspace.hyperlinks_jsonl
        output_path.parent.mkdir(parents=True, exist_ok=True)

        _log.info("Source PDF: %s", pdf_path)
        _log.info("Output    : %s", output_path)
        try:
            links = extract_url_links(pdf_path)
            save_links(links, output_path)
        except Exception as exc:
            _log.exception("Error while extracting links from %s", pdf_path.name)
            raise StepFailed(f"url-extraction failed on {pdf_path.name}: {exc}") from exc

        if links:
            _log.info("Done — %d link(s) extracted → %s", len(links), output_path)
        else:
            _log.info("Done — no external link found in %s", pdf_path.name)
        return StepResult(
            StepStatus.OK, outputs=self.outputs(ctx), message=f"{len(links)} link(s)"
        )
