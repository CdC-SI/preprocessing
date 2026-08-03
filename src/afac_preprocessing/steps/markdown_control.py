"""markdown-control stage — Markdown quality control via VLM, page by page.

Conversion of the script simple_extraction/markdown_control_vlm.py.
Already async (Semaphore + gather), pattern with url-tuning. 
Business functions MOVED as-is, only the
dispatch changes: shared client via ctx.vlm(), no more
asyncio.run() or client.close() in the stage. The historical
180-second timeout of this stage is handled by the ClientBundle client.

Each VLM call receives only the Markdown of ITS page + the image of that
page, avoids duplications at page boundaries.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import fitz  # PyMuPDF

from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed
from ..prompts.prompts import VLM_PROMPT_STAGE4_CHECK_PAGE_EN

if TYPE_CHECKING:
    from ..clients.base import AsyncVlmClient
    from ..context import PipelineContext

_log = logging.getLogger(__name__)


# Fonctions métier — déplacées telles quelles
def _strip_code_fences(text: str) -> str:
    """Strip opening/closing code fences that Qwen sometimes wraps around its output.

    Handles ```json, ```markdown, ``` (bare), etc.
    """
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def _pdf_page_count(pdf_path: Path) -> int:
    """Returns the number of pages in the PDF (to be called via asyncio.to_thread)."""
    with fitz.open(str(pdf_path)) as doc:
        return doc.page_count


def pdf_page_to_base64(pdf_path: Path, page_num: int, dpi: int = 150) -> str:
    """
   Renders a PDF page as a base64-encoded PNG image.
   
   :param pdf_path: path to the PDF
   :param page_num: page number (1-based)
   :param dpi: rendering resolution
   :return: base64-encoded image
    """
    with fitz.open(str(pdf_path)) as doc:
        page = doc[page_num - 1]
        pix = page.get_pixmap(dpi=dpi)
        img_bytes = pix.tobytes("png")
    return base64.b64encode(img_bytes).decode("utf-8")


PAGE_BREAK = "<!-- page-break -->"


def load_page_markdowns(md_path: Path) -> list[str]:
    """
    Loads the paginated markdown produced by stage 09 and returns a list,
    one entry per page, splitting on the PAGE_BREAK separator.

    :param md_path: path to the .md file produced by stage 09
    :return: list of markdown strings, one per page
    """
    content = md_path.read_text(encoding="utf-8")
    pages = [p.strip() for p in content.split(PAGE_BREAK) if p.strip()]
    _log.info("Markdown: %d page(s) detected in %s", len(pages), md_path.name)
    return pages


# Traitement des pages
async def process_page(
    page_num: int,
    total_pages: int,
    page_markdown: str,
    pdf_path: Path,
    semaphore: asyncio.Semaphore,
    vlm: AsyncVlmClient,
    prompt_template: str,
    dpi: int = 150,
) -> tuple[int, str]:
    """
    Processes a page: sends its image + its markdown to the VLM and retrieves the
    correction. Retry on transient errors is handled by the OpenAI client.

    (Dispatch adapted for the refactor: ``vlm`` is the shared AsyncVlmClient
    of the run. Body unchanged.)

    :param page_num: page number (1-based)
    :param total_pages: total number of PDF pages
    :param page_markdown: markdown of this page only
    :param pdf_path: path to the original PDF
    :param semaphore: concurrency-limiting semaphore
    :param prompt_template: prompt template to format
    :param dpi: PDF page rendering resolution
    :return: (page number, corrected markdown for this page)
    """
    async with semaphore:
        _log.info("Processing page %d/%d ...", page_num, total_pages)

        try:
            image_b64 = await asyncio.to_thread(pdf_page_to_base64, pdf_path, page_num, dpi)
        except Exception as e:
            _log.exception("PDF rendering error on page %d: %s", page_num, e)
            return page_num, ""

        prompt = prompt_template.format(
            page_num=page_num,
            total_pages=total_pages,
            page_markdown=page_markdown,
        )

        try:
            result = await vlm.vision_completion(prompt, image_b64)
            _log.info("Page %d/%d processed.", page_num, total_pages)
            return page_num, _strip_code_fences(result)
        except Exception as e:
            _log.exception("VLM error on page %d: %s", page_num, e)
            return page_num, ""


class MarkdownControlStep(PipelineStep):
    """Checks/corrects the markdown page by page via VLM -> _vlm_check.md."""

    name = "markdown-control"
    description = "VLM markdown quality control"
    requires_vlm = True

    def __init__(self, *, max_concurrency: int = 1, dpi: int = 150) -> None:
        # Mêmes défauts que --workers/--dpi du script historique.
        self.max_concurrency = max_concurrency
        self.dpi = dpi
        self.prompt_template = VLM_PROMPT_STAGE4_CHECK_PAGE_EN  # variante v2 (défaut pipeline)

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.source_pdf, ctx.workspace.url_vlm_markdown]

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.vlm_check_markdown]

    def execute(self, ctx: PipelineContext) -> StepResult:
        return ctx.run_async(self._execute_async(ctx))  # ⚠ PAS asyncio.run() (P7)

    async def _execute_async(self, ctx: PipelineContext) -> StepResult:
        ws = ctx.workspace
        pdf_path = ws.source_pdf
        md_path = ws.url_vlm_markdown
        output_path = ws.vlm_check_markdown
        vlm = ctx.vlm()

        if not await vlm.check_connectivity():
            raise StepFailed("VLM unavailable, stopping the pipeline.")
        _log.info("PDF      : %s", pdf_path)
        _log.info("Markdown : %s", md_path)
        _log.info("Output   : %s", output_path)
        _log.info("Workers  : %d", self.max_concurrency)
        _log.info("DPI      : %d", self.dpi)

        page_markdowns = load_page_markdowns(md_path)
        total_pages = len(page_markdowns)

        pdf_page_count = await asyncio.to_thread(_pdf_page_count, pdf_path)
        if pdf_page_count != total_pages:
            raise StepFailed( 
                f"Inconsistency: {total_pages} page(s) in the markdown but " 
                f"{pdf_page_count} page(s) in the PDF ({pdf_path.name}). " 
                f"Rerun markdown-convert to regenerate {md_path.name} with the " 
                "separators <!-- page-break -->." 
                )

        _log.info("%d page(s) to check.", total_pages)

        semaphore = asyncio.Semaphore(self.max_concurrency)
        tasks = [
            process_page(
                page_num=p,
                total_pages=total_pages,
                page_markdown=page_markdowns[p - 1],
                pdf_path=pdf_path,
                semaphore=semaphore,
                vlm=vlm,
                prompt_template=self.prompt_template,
                dpi=self.dpi,
            )
            for p in range(1, total_pages + 1)
        ]

        # No client.close() here: the ClientBundle owns the client (P7).
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[tuple[int, str]] = []
        for page_num_idx, r in enumerate(raw_results, 1):
            if isinstance(r, BaseException):
                _log.error(
                    "Page task %d : unexpected exception.",
                    page_num_idx,
                    exc_info=r,
                )
                results.append((page_num_idx, ""))
            else:
                results.append(r)

        results_sorted = sorted(results, key=lambda x: x[0])
        failed_pages = [p for p, content in results_sorted if not content.strip()]
        page_corrections = [content for _, content in results_sorted if content.strip()]

        if failed_pages:
            _log.warning(
                "%d/%d page(s) failed and not included in the output: %s",
                len(failed_pages), total_pages, failed_pages,
            )

        if not page_corrections:
            raise StepFailed(
                f"All pages ({total_pages}) failed, " 
                "output file not saved. Check the VLM logs."
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n\n".join(page_corrections), encoding="utf-8")
        _log.info("Verified markdown saved: %s", output_path)
        return StepResult(StepStatus.OK, outputs=self.outputs(ctx))
