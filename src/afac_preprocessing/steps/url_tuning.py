"""url-tuning step, integration of hyperlinks into the doctags via VLM.

Conversion of the ``simple_extraction/url_tuning_vlm.py`` script (wave C).
It was already an async step (``Semaphore`` + ``gather``): it's THE template
for wave D. Business functions MOVED as-is (invariant #1); only the
*dispatch* changes (pitfall P7):

- no more client built here nor ``client.close()``, the client comes from
  ``ctx.vlm()`` (Protocol AsyncVlmClient), owned by the ClientBundle;
- no more ``asyncio.run()``, ``execute()`` delegates to ``ctx.run_async()``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import fitz  # PyMuPDF

from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed
from ..prompts.prompts import VLM_PROMPT_CORRECTION_STAGE_3_EN

if TYPE_CHECKING:
    from ..clients.base import AsyncVlmClient
    from ..context import PipelineContext

_log = logging.getLogger(__name__)


# Business logic (pure functions) — moved as-is
def load_jsonl_links(jsonl_path: Path) -> list[dict]:
    """
    Load the links extracted from the JSONL file into a list of dictionaries.

    :param jsonl_path: Path to the JSONL file containing the extracted links.
    :return: List of link dictionaries, one per JSONL line.
    """
    links = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                links.append(json.loads(line))
    return links


def get_links_for_page(links: list[dict], page: int) -> list[dict]:
    """
    Filter the list of links to return only those matching the specified page.

    :param links: List of all extracted links.
    :param page: Page number to filter on.
    :return: List of links belonging to the given page.
    """
    return [l for l in links if l.get("page_number") == page]


def pdf_page_to_base64(pdf_path: Path, page_num: int) -> str:
    """
    Open the PDF, render the specified page as an image, and encode that
    image in base64 to send it to the VLM.

    :param pdf_path: Path to the source PDF file.
    :param page_num: 1-based page number to render.
    :return: Base64-encoded PNG image of the page.
    """
    with fitz.open(str(pdf_path)) as doc:
        pix = doc[page_num - 1].get_pixmap(dpi=100)
    return base64.b64encode(pix.tobytes("png")).decode("utf-8")


def build_prompt(page_tags: str, page_links: list[dict], prompt_template: str) -> str:
    """
    Build the prompt to send to the VLM by combining the page's doctags
    with the links extracted from the JSONL file.
    Links are formatted in a human-readable way for the VLM, with their
    associated text and URL, so it can correctly integrate them into the
    doctags content it will reconstruct for the page.

    :param page_tags: Doctags content for the page.
    :param page_links: Links belonging to this page.
    :param prompt_template: Prompt template to format (v2 or v3)
    :return: Formatted prompt ready to send to the VLM.
    """
    # NOTE: the strings below (links_str) are part of the prompt payload sent
    # to the VLM and are intentionally left in French — see task constraint 3.
    links_str = "\n".join(
        f'{i+1}. texte: "{l["text"]}" -> url: {l["hyperlink"]}'
        for i, l in enumerate(page_links)
    )
    if not links_str:
        links_str = "Aucune URL pour cette page."

    return prompt_template.format(
        page_tags=page_tags,
        links_str=links_str,
    )


async def process_page(
    page_num: int,
    page_tags: str,
    page_links: list[dict],
    pdf_path: Path,
    semaphore: asyncio.Semaphore,
    *,
    vlm: AsyncVlmClient,
    prompt_template: str,
) -> tuple[int, str]:
    """
    Process a PDF page by calling the VLM to reconstruct its doctags
    content enriched with the URL links. Retries on transient errors are
    handled by the OpenAI client (max_retries), no manual retry loop here.

    (Dispatch adapted for the refactor: ``vlm`` is the AsyncVlmClient shared
    by the run — ``await vlm.vision_completion(...)`` replaces
    ``vision_completion_async(client, model_name, ...)``. Body unchanged.)

    :param page_num: Page number being processed.
    :param page_tags: Doctags content for the page.
    :param page_links: Links belonging to this page.
    :param pdf_path: Path to the source PDF file.
    :param semaphore: Semaphore limiting the number of concurrent VLM requests.
    :param prompt_template: Prompt template to format
    :return: Tuple of (page number, resulting doctags content for the page).
    """
    async with semaphore:
        _log.info("Page %d: %d link(s) to insert...", page_num, len(page_links))
        try:
            image_b64 = await asyncio.to_thread(pdf_page_to_base64, pdf_path, page_num)
            prompt = build_prompt(page_tags, page_links, prompt_template)
            result = await vlm.vision_completion(prompt, image_b64)
            _log.info("Page %d processed.", page_num)
            return page_num, result
        except Exception as e:
            _log.exception("Error on page %d: %s", page_num, e)
            return page_num, page_tags  # fallback: return the page's original doctags


def split_doctags_by_page(doctags: str, n_pages: int) -> dict[int, str]:
    """
    Attempt to split the doctags content into pages using <page_break>
    tags as separators.
    If <page_break> tags are present, the content is split directly on
    those tags, which is more reliable for mapping the right doctags
    portions to each page.
    If no separator tag is found, falls back to an approach that
    distributes the doctags elements approximately based on the total count.

    Re-indexes sequentially over non-empty segments rather than over the
    raw split index: a superfluous or consecutive <page_break> (e.g. an
    artifact from an upstream fix) produces an empty segment which, if
    indexed by raw position, shifts all subsequent page numbers, page N
    then ends up stored under key N+1, and run() never finds it
    (pages_tags.get(N, "") silently empty -> page content lost with no error).

    :param doctags: Full doctags content to split.
    :param n_pages: Expected number of pages (from the PDF).
    :return: Mapping of page number to its doctags content.
    """
    parts = re.split(r'<page_break\s*/?>', doctags)
    non_empty = [content for p in parts if (content := p.strip())]

    if len(non_empty) > 1:
        _log.info("Split by <page_break>: %d page(s) detected.", len(non_empty))
        if len(non_empty) != n_pages:
            _log.warning(
                "%d page(s) detected via <page_break> but %d page(s) expected (PDF) — "
                "check the source doctags for extra or missing <page_break> tags.",
                len(non_empty), n_pages,
            )
        return {i + 1: content for i, content in enumerate(non_empty)}

    _log.warning("No <page_break> found, falling back to distribution by count.")
    pattern = re.compile(
        r'(<(?!/)(?!doctag)\w+>'
        r'(?:<loc_\d+>)*'
        r'.*?'
        r'(?=<(?!loc_)\w+>|</doctag>|$))',
        re.DOTALL
    )
    all_tags = [tag.strip() for tag in pattern.findall(doctags) if tag.strip()]
    tags_per_page = max(1, len(all_tags) // n_pages)

    pages = {}
    for i in range(n_pages):
        start = i * tags_per_page
        end = start + tags_per_page if i < n_pages - 1 else len(all_tags)
        pages[i + 1] = "\n".join(all_tags[start:end])
    return pages


def assemble_doctags(pages: dict[int, str]) -> str:
    """
    Assemble the doctags content of each page into a single overall
    content, making sure to strip internal doctag tags to avoid nesting
    issues, and cleanly joining the contents with line breaks.

    :param pages: Mapping of page number to its doctags content.
    :return: Full assembled doctags document, wrapped in <doctag>...</doctag>.
    """
    def _strip_doctag_tags(s: str) -> str:
        s = re.sub(r'</?doctags?>', '', s, flags=re.IGNORECASE)
        return s.strip()

    parts = []
    for page_num in sorted(pages):
        content = pages[page_num].strip()
        if not content:
            continue
        content = _strip_doctag_tags(content)
        content = re.sub(r'\n{2,}', '\n', content)
        if content:
            parts.append(content)
    body = "\n<page_break>\n".join(parts).strip()
    return f"<doctag>\n{body}\n</doctag>"


class UrlTuningStep(PipelineStep):
    """Reconstructs the doctags page by page via VLM, integrating the URLs into them."""

    name = "url-tuning"
    description = "Correction of URLs in the doctags via VLM"
    requires_vlm = True

    def __init__(self, *, max_concurrency: int = 1) -> None:
        # Same default as --workers in the historical script.
        self.max_concurrency = max_concurrency
        self.prompt_template = VLM_PROMPT_CORRECTION_STAGE_3_EN  # v2 variant (pipeline default)

    def _source_doctags(self, ctx: PipelineContext) -> Path:
        """Doctags to correct: the variant enriched by image-description (06)
        if that step ran, otherwise the one from load-jsonline-doctags (05).

        The fallback makes the no-images profile runnable: the step only
        reads this file to split it by page, image descriptions play no
        role here.
        """
        ws = ctx.workspace
        for candidate in (
            ws.reordered_with_tables_pictures_doctags,
            ws.reordered_with_tables_doctags,
        ):
            if candidate.exists():
                return candidate
        # None exists: return the nominal path so validate_inputs
        # produces its usual error message.
        return ws.reordered_with_tables_pictures_doctags

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        ws = ctx.workspace
        return [ws.source_pdf, self._source_doctags(ctx), ws.hyperlinks_jsonl]

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.url_vlm_doctags]

    def execute(self, ctx: PipelineContext) -> StepResult:
        return ctx.run_async(self._execute_async(ctx))  # NOT asyncio.run()

    async def _execute_async(self, ctx: PipelineContext) -> StepResult:
        ws = ctx.workspace
        pdf_path = ws.source_pdf
        doctags_path = self._source_doctags(ctx)
        jsonl_path = ws.hyperlinks_jsonl
        output_path = ws.url_vlm_doctags
        vlm = ctx.vlm()

        if not await vlm.check_connectivity():
            raise StepFailed("VLM unreachable, stopping the pipeline.")

        _log.info("=" * 60)
        _log.info("PDF      : %s", pdf_path)
        _log.info("Doctags  : %s", doctags_path)
        _log.info("JSONL    : %s", jsonl_path)
        _log.info("Output   : %s", output_path)
        _log.info("Workers  : %d", self.max_concurrency)
        _log.info("=" * 60)

        doctags = doctags_path.read_text(encoding="utf-8")
        links = load_jsonl_links(jsonl_path)

        with fitz.open(str(pdf_path)) as doc:
            n_pages = doc.page_count
        _log.info("%d page(s) detected, %d link(s) total.", n_pages, len(links))

        pages_tags = split_doctags_by_page(doctags, n_pages)

        semaphore = asyncio.Semaphore(self.max_concurrency)
        tasks = [
            process_page(
                page_num=p,
                page_tags=pages_tags.get(p, ""),
                page_links=get_links_for_page(links, p),
                pdf_path=pdf_path,
                semaphore=semaphore,
                vlm=vlm,
                prompt_template=self.prompt_template,
            )
            for p in range(1, n_pages + 1)
            if pages_tags.get(p, "").strip()
        ]

        # No client.close() here: the ClientBundle owns the client.
        results = await asyncio.gather(*tasks)

        processed_pages = dict(sorted(results))
        final_doctags = assemble_doctags(processed_pages)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(final_doctags, encoding="utf-8")
        _log.info("Final doctags saved: %s", output_path)
        return StepResult(StepStatus.OK, outputs=self.outputs(ctx))
