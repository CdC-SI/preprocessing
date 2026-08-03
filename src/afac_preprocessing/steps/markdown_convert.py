"""markdown-convert stage, conversion of enriched doctags into Markdown.

Conversion of the script simple_extraction/docling_markdown_converter.py
(wave B). Business functions MOVED as-is (invariant no. 1).

In the pipeline, the stage reads <doc>_url_vlm.doctags (output of
url-tuning, historical default suffix _url_vlm) and produces
<doc>_url_vlm.md, the base <doc>.md is produced by docling-extract,
not here.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from docling_core.types.doc.document import DoclingDocument, DocTagsDocument

from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed

if TYPE_CHECKING:
    from ..context import PipelineContext

_log = logging.getLogger(__name__)


# Business logic (pure functions) — déplacées telles quelles
def _split_pages(content: str) -> str:
    """
    If the content is a single <doctag> block, split it into one block per page using
    </page_footer> (native Docling doctags) or <page_break> (produced by url_tuning_vlm.py)
    as the delimiter, the format from_multipage_doctags_and_images expects.
    Without this split, Docling stops after the first page and ignores the rest.

    :param content: raw doctags content
    :type content: str
    :return: content split into per-page <doctag> blocks
    :rtype: str
    """
    if content.count("<doctag>") > 1:
        return content  # already in the correct multi-page format

    inner = re.sub(r"^\s*</?doctag>\s*", "", content.strip(), flags=re.DOTALL)
    inner = re.sub(r"\s*</doctag>\s*$", "", inner, flags=re.DOTALL)

    # Try </page_footer> first (native Docling doctags),
    # then <page_break> (separator produced by url_tuning_vlm.py and assemble_doctags).
    parts = re.split(r"(?<=</page_footer>)", inner)
    if len(parts) <= 1:
        parts = re.split(r"<page_break\s*/?>", inner)

    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) <= 1:
        return content  # single-page document, nothing to do

    return "\n".join(f"<doctag>\n{p}\n</doctag>" for p in parts)


def _hoist_misplaced_tags(content: str) -> str:
    """
    Docling cannot handle <section_header_level_N> or <unordered_list> nested inside an
    <ordered_list>. It flattens them into the list, losing headers and section boundaries.
    Extracts these tags from <ordered_list> blocks and places them right after the
    matching </ordered_list>.

    :param content: doctags content
    :type content: str
    :return: content with misplaced tags hoisted out
    :rtype: str
    """
    HOIST = re.compile(
        r"(<section_header_level_\d[^>]*>.*?</section_header_level_\d>|"
        r"<unordered_list>.*?</unordered_list>)",
        re.DOTALL,
    )
    OL = re.compile(r"<ordered_list>(.*?)</ordered_list>", re.DOTALL)

    def _fix_ol(m: re.Match) -> str:
        inner = m.group(1)
        hoisted: list[str] = []

        def _extract(tag_m: re.Match) -> str:
            hoisted.append(tag_m.group(0))
            return ""

        cleaned = HOIST.sub(_extract, inner)
        result = f"<ordered_list>{cleaned}</ordered_list>"
        if hoisted:
            result += "\n" + "\n".join(hoisted)
        return result

    return OL.sub(_fix_ol, content)


def preprocess_doctags(content: str) -> str:
    """
    Preprocess the doctags content: page splitting and misplaced tag correction.
    Public entry point for external modules, avoids coupling on the private helpers.

    :param content: raw doctags content
    :return: preprocessed content, ready for DocTagsDocument
    """
    return _hoist_misplaced_tags(_split_pages(content))

PAGE_BREAK = "<!-- page-break -->"


def convert_doctags_to_markdown(doctags_path: Path) -> str:
    """
    Read the .doctags file, apply preprocessing, convert to Markdown via Docling
    page by page, then join the pages with a <!-- page-break --> separator.

    :param doctags_path: path to the .doctags file to convert
    :type doctags_path: Path
    :return: final Markdown content with page separators
    :rtype: str
    """
    from ..utils.markdown_utils import apply_markdown_transforms

    content = doctags_path.read_text(encoding="utf-8")
    content = _split_pages(content)
    content = _hoist_misplaced_tags(content)

    page_blocks = re.findall(r"<doctag>(.*?)</doctag>", content, re.DOTALL)

    if not page_blocks:
        doctags_doc = DocTagsDocument.from_multipage_doctags_and_images(content, None)
        doc = DoclingDocument.load_from_doctags(doctags_doc)
        return apply_markdown_transforms(doc.export_to_markdown())

    page_markdowns = []
    for block in page_blocks:
        single = f"<doctag>{block}</doctag>"
        dt = DocTagsDocument.from_multipage_doctags_and_images(single, None)
        doc = DoclingDocument.load_from_doctags(dt)
        md = apply_markdown_transforms(doc.export_to_markdown())
        page_markdowns.append(md.strip())

    return f"\n\n{PAGE_BREAK}\n\n".join(page_markdowns)


class MarkdownConvertStep(PipelineStep):
    """Converts <doc>_url_vlm.doctags into <doc>_url_vlm.md via Docling."""

    name = "markdown-convert"
    description = "Doctags -> markdown conversion"
    requires_vlm = False

    def _source_doctags(self, ctx: PipelineContext) -> Path:
        """Doctags to convert: the last actually produced link.
        url-tuning (08) and image-description (06) are both skippable
        (no-vlm, no-images profiles); go back up the chain to the available
        doctags rather than requiring the most enriched version.
        """
        ws = ctx.workspace
        for candidate in (
            ws.url_vlm_doctags,
            ws.reordered_with_tables_pictures_doctags,
            ws.reordered_with_tables_doctags,
        ):
            if candidate.exists():
                return candidate
        # None exists: return the nominal value so that validate_input
        # produces the usual error message.
        return ws.url_vlm_doctags

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        return [self._source_doctags(ctx)]

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.url_vlm_markdown]

    def execute(self, ctx: PipelineContext) -> StepResult:
        input_path = self._source_doctags(ctx)
        output_path = ctx.workspace.url_vlm_markdown

        _log.info("Input   : %s", input_path)
        _log.info("Output  : %s", output_path)
        try:
            markdown = convert_doctags_to_markdown(input_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown, encoding="utf-8")
        except Exception as exc:
            _log.exception("Error while converting %s", input_path.name)
            raise StepFailed(f"markdown-convert failed on {input_path.name}: {exc}") from exc

        _log.info("Markdown generated: %s", output_path)
        return StepResult(StepStatus.OK, outputs=self.outputs(ctx))
