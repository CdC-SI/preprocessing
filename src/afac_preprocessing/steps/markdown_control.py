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
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import fitz  # PyMuPDF

from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed
from ..prompts.prompts import VLM_PROMPT_STAGE4_CHECK_PAGE_EN, VLM_PROMPT_STAGE4_CHECK_PAGE_MINIMAL_EN
from .inject_image_descriptions import PLACEHOLDER_RE

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

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")


def extract_links(markdown: str) -> list[dict[str, str]]:
    """Markdown links present in *markdown*, as link dicts compatible with
    ``DeterministicLinkInjector`` (``{"text": ..., "hyperlink": ...}``)."""
    return [
        {"text": match.group(1), "hyperlink": match.group(2)}
        for match in _MARKDOWN_LINK_RE.finditer(markdown)
    ]


def restore_dropped_links(original_markdown: str, corrected_markdown: str) -> str:
    """Re-inserts, into the VLM-corrected page, any link present in the
    original page that the correction dropped or mangled.

    The prompt already lists Markdown links as immutable — verified
    unreliable in practice (3 of 7 correctly-formed, unescaped links dropped
    on "TN - Fortune et revenu acquis sous forme de rente" despite the
    instruction). A first attempt tried protecting links behind an opaque
    ``[[[LINK:N]]]`` placeholder before the VLM call, mirroring the
    ``[[[IMAGE_DESC:N]]]`` pipeline markers — but those markers turned out
    NOT to survive the round-trip either: verified absent from
    ``_url_vlm.md`` (the VLM's own input) and only present in the output
    because the prompt's own literal example primes the model to recreate it
    when it notices an undescribed picture in the page image. That's pattern
    completion, not preservation, and it doesn't generalize to a token the
    model has never been shown.

    So links are restored **after** the fact instead, by reusing the same
    anchor-text search already proven for url-tuning
    (``DeterministicLinkInjector``, which is doctags-agnostic — it only
    operates on a plain string) — deterministic, and independent of
    whatever the VLM did to the surrounding text: the anchor's plain text
    usually survives even when its link markup doesn't (observed: "selon
    l'art. 17 LPP" present with no brackets at all after correction).

    Every existing ``[text](url)`` span in the corrected page is protected
    UNCONDITIONALLY, before any restoration is attempted — not only the ones
    matching an original link's exact string. A first version only pre-seeded
    exact matches: when the VLM reformats an anchor while keeping the link
    intact (observed: "-bpanda" -> "— bpanda", em-dash normalization), the
    exact check misses it, the link is treated as dropped, and the fuzzy
    restore pass (which tolerates that very same dash difference) nests a
    new [text](url) inside the still-present link's own brackets — verified
    on "Adhésion traitement": `[[Process "..." — bpanda](url)](url)`. Once
    every existing link span is protected regardless of its exact content,
    that nesting becomes structurally impossible.

    :param original_markdown: this page's markdown before the VLM call.
    :param corrected_markdown: the VLM's rewrite of the same page.
    :return: corrected_markdown with every originally-present link restored.
    """
    original_links = extract_links(original_markdown)
    if not original_links:
        return corrected_markdown

    from .url_tuning_fixed import DeterministicLinkInjector, PageCanvas

    injector = DeterministicLinkInjector()
    canvas = PageCanvas(corrected_markdown)

    # Every link already present in the corrected page is protected first —
    # whatever its exact text, whatever URL it points to. A restoration
    # attempt can then never land inside an existing link's brackets.
    existing_urls: set[str] = set()
    for match in _MARKDOWN_LINK_RE.finditer(corrected_markdown):
        canvas.injected.append(match.span())
        existing_urls.add(match.group(2))

    restored = 0
    for link in original_links:
        if link["hyperlink"] in existing_urls:
            continue  # already present as some link's destination, in some form
        if injector.inject_link(canvas, link):
            restored += 1

    if restored:
        _log.info("%d link(s) restored after the VLM correction dropped them.", restored)
    return canvas.text


def restore_dropped_image_markers(original_markdown: str, corrected_markdown: str) -> str:
    """Re-inserts, into the VLM-corrected page, any ``[[[IMAGE_DESC:N]]]``
    marker present in the original page that the correction dropped.

    Same root cause documented in :func:`restore_dropped_links`: these
    markers do not survive the VLM round-trip — the model sees the real
    picture in the page image and pattern-completes the odd token back into
    a generic ``<!-- image -->``, discarding the index. Verified on "CI -
    Tableau des dispenses": the marker for image 2 is present in
    ``_url_vlm.md`` (markdown-control's own input) and absent from
    ``_vlm_check.md`` (its output), silently starving
    ``inject-image-descriptions`` of the marker it needs to place the
    description, with only a log warning to show for it.

    Restored the same way as links: deterministically, after the fact,
    anchored on the surrounding plain text rather than trusting the VLM to
    preserve an opaque token it was never trained to keep. For a marker
    that shared its line with other text (e.g. inlined in a list item), the
    text on that line is the anchor and the marker is glued directly next
    to it, matching how it was found (``inject-image-descriptions`` inlines
    list-item descriptions the same way). For a marker standalone on its
    own line, the nearest preceding non-blank line is the anchor, and the
    marker is reinserted as its own paragraph (blank line before and
    after) rather than glued onto that line — gluing would merge two
    paragraphs into a run-on line once the marker is later replaced by its
    (often multi-sentence) description. When no anchor can be found in the
    corrected text at all (the page was rewritten too heavily to trace),
    the marker is appended at the end instead of being dropped: a
    description in the wrong spot is recoverable, a silently discarded one
    is not.

    :param original_markdown: this page's markdown before the VLM call.
    :param corrected_markdown: the VLM's rewrite of the same page.
    :return: corrected_markdown with every originally-present marker restored.
    """
    original_matches = list(PLACEHOLDER_RE.finditer(original_markdown))
    if not original_matches:
        return corrected_markdown

    already_present = {int(m.group(1)) for m in PLACEHOLDER_RE.finditer(corrected_markdown)}

    restored = 0
    for match in original_matches:
        idx = int(match.group(1))
        if idx in already_present:
            continue

        marker_text = match.group(0)
        anchor, mode = _find_anchor(original_markdown, match.start(), match.end())

        if anchor and anchor in corrected_markdown:
            pos = corrected_markdown.index(anchor)
            if mode == "inline_before":
                insertion = marker_text
            elif mode == "inline_after":
                pos += len(anchor)
                insertion = marker_text
            else:  # "paragraph"
                pos += len(anchor)
                insertion = "\n\n" + marker_text
            corrected_markdown = corrected_markdown[:pos] + insertion + corrected_markdown[pos:]
        else:
            _log.warning(
                "IMAGE_DESC:%d marker's anchor text not found in the corrected page; "
                "appending it at the end instead of dropping it.",
                idx,
            )
            corrected_markdown = corrected_markdown.rstrip("\n") + "\n\n" + marker_text + "\n"

        already_present.add(idx)
        restored += 1

    if restored:
        _log.info("%d image marker(s) restored after the VLM correction dropped them.", restored)
    return corrected_markdown


def _find_anchor(markdown: str, marker_start: int, marker_end: int) -> tuple[str, str]:
    """Finds the plain text to search for in the corrected page in order to
    re-locate where a dropped marker used to sit.

    Prefers text sharing the marker's own line (glued after text that
    precedes it, before text that follows it); falls back to the nearest
    preceding non-blank line when the marker stood alone on its line, in
    which case it is reinserted as a new paragraph rather than glued.

    :return: (anchor text, one of "inline_after" / "inline_before" / "paragraph")
    """
    line_start = markdown.rfind("\n", 0, marker_start) + 1
    line_end = markdown.find("\n", marker_end)
    if line_end == -1:
        line_end = len(markdown)

    before = markdown[line_start:marker_start].strip()
    if before:
        return before, "inline_after"

    after = markdown[marker_end:line_end].strip()
    if after:
        return after, "inline_before"

    for line in reversed(markdown[:line_start].splitlines()):
        if line.strip():
            return line.strip(), "paragraph"

    return "", "paragraph"


def _is_json_object_line(line: str) -> dict | None:
    """Returns the parsed dict if *line* is a single-line JSON object, else None."""
    stripped = line.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


_DASH_VARIANTS = str.maketrans({
    "‐": "-",  # hyphen
    "‑": "-",  # non-breaking hyphen
    "‒": "-",  # figure dash
    "–": "-",  # en dash
    "—": "-",  # em dash
    "−": "-",  # minus sign
})


def _normalized_values(obj: dict) -> list:
    """Row values for matching purposes: dash variants (the VLM is observed
    to swap a plain hyphen for an en/em dash while "correcting" a page) and
    stray whitespace are collapsed, so a value the VLM only re-typeset —
    not actually changed — still matches its original. Restoration always
    reinstates the original line's exact text regardless, dash included."""
    normalized = []
    for v in obj.values():
        if isinstance(v, str):
            v = re.sub(r"\s+", " ", v.translate(_DASH_VARIANTS)).strip()
        normalized.append(v)
    return normalized


def restore_original_json_tables(original_markdown: str, corrected_markdown: str) -> str:
    """Restores the original keys of any pipeline-generated JSON table row
    whose keys the VLM correction renamed.

    ``load-jsonline-doctags`` (an earlier, deterministic stage) already
    replaces each doctags ``<otsl>`` table with its JSONL equivalent sourced
    from ``tables/*.jsonl`` — Docling's own structural table recognition,
    the ground truth, produced before any VLM ever sees the document. These
    rows are meant to be immutable "pipeline-generated artifacts" per the
    prompt's priority rule 5, but the VLM does not always comply: observed
    on "Analyse des inscriptions" renaming ``"Historique des données de
    l'assurance facultative"`` / ``"....1"`` (Docling's own dedup suffix for
    a table whose header is a single merged title, not two named columns)
    to invented labels ``"Période"`` / ``"Support"`` — plausible-looking,
    but severing the only text tying the rows back to their source table.

    Restored the same way as links and image markers: deterministically,
    after the fact. Row *values* survive the renaming (only the keys were
    touched), so each original row is re-located in the corrected page by
    its values and swapped back in verbatim, keys included — dash and all:
    the VLM is also observed to swap a plain hyphen for an en/em dash while
    "correcting" a page (e.g. a date range like "1983 - 1987"), which
    :func:`_normalized_values` treats as the same value for matching
    purposes only, without weakening the match on anything else. A row is
    still left alone if its values genuinely differ beyond that — safer to
    under-restore than to overwrite a page the VLM has otherwise
    legitimately corrected (OCR fixes affect the input's own JSON rows too,
    e.g. digit corrections inside values, which this function is not meant
    to undo).

    :param original_markdown: this page's markdown before the VLM call.
    :param corrected_markdown: the VLM's rewrite of the same page.
    :return: corrected_markdown with original-keyed JSON rows restored.
    """
    original_rows = [
        (line, obj)
        for line in original_markdown.splitlines()
        if (obj := _is_json_object_line(line)) is not None
    ]
    if not original_rows:
        return corrected_markdown

    corrected_lines = corrected_markdown.splitlines()
    restored = 0

    for original_line, original_obj in original_rows:
        if original_line in corrected_markdown:
            continue  # untouched, nothing to restore

        original_values = _normalized_values(original_obj)
        for corrected_line in corrected_lines:
            corrected_obj = _is_json_object_line(corrected_line)
            if corrected_obj is not None and _normalized_values(corrected_obj) == original_values:
                corrected_markdown = corrected_markdown.replace(corrected_line, original_line, 1)
                restored += 1
                break

    if restored:
        _log.info(
            "%d JSON table row(s) restored to their original keys after the VLM renamed them.",
            restored,
        )
    return corrected_markdown


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
        self.prompt_template = VLM_PROMPT_STAGE4_CHECK_PAGE_MINIMAL_EN  # test : prompt court, OCR seulement

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
                page_num, content = r
                if content.strip():
                    content = restore_dropped_image_markers(page_markdowns[page_num - 1], content)
                    content = restore_original_json_tables(page_markdowns[page_num - 1], content)
                    content = restore_dropped_links(page_markdowns[page_num - 1], content)
                results.append((page_num, content))

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
