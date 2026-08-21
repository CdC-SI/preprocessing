"""reorder-doctags step, reordering the blocks of a .doctags file by y0/x0.

Conversion of the ``simple_extraction/reordered_doctags.py`` script.
All business functions are MOVED as-is.

Docling can extract blocks in an incorrect order when y0 coordinates are
similar or missing. Re-sorts them by vertical position (y0) then horizontal
position (x0), page by page, before the downstream VLM steps.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed
from ..utils.pdf_utils import pdf_page_count

if TYPE_CHECKING:
    from ..context import PipelineContext

_log = logging.getLogger(__name__)

TAG_UL_CLOSE = "</unordered_list>"
TAG_PAGE_FOOTER = "<page_footer>"
TAG_PAGE_BREAK = "<page_break>"
_NO_X0 = 10**9  # arbitrary x0 value for blocks without a horizontal coordinate = placed last


# Data model
@dataclass
class Block:
    raw: str
    y0: int | None
    x0: int | None
    is_list_item: bool = False


# Business logic (pure functions) — moved as-is
def extract_xy0(s: str) -> tuple[int | None, int | None]:
    """
    Extract (x0, y0) from the first <loc_x0><loc_y0> pair found in s.

    :param s: Doctags-formatted string potentially containing <loc_x0><loc_y0> location tags.
    :type s: str
    :return: A tuple (x0, y0) of the first coordinates found, or (None, None) if no location tag is present.
    :rtype: tuple[int | None, int | None]
    """
    match = re.search(r"<loc_(\d+)><loc_(\d+)>", s)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _collect_until(lines: list[str], start: int, closing_tag: str) -> tuple[list[str], int]:
    """
    Accumulate lines from start until closing_tag inclusive. Returns (parts, next_i).

    :param lines: The list of doctags lines to scan.
    :type lines: list[str]
    :param start: Index of the line to start collecting from.
    :type start: int
    :param closing_tag: The tag string that marks the end of the block to collect.
    :type closing_tag: str
    :return: A tuple of the collected lines and the index of the line following the closing tag.
    :rtype: tuple[list[str], int]
    """
    parts = [lines[start]]
    i = start + 1
    while i < len(lines):
        parts.append(lines[i])
        if closing_tag in lines[i]:
            i += 1
            break
        i += 1
    return parts, i


def _parse_ordered_list(lines: list[str], i: int) -> tuple[Block, int]:
    """
    Parse an <ordered_list>…</ordered_list> block as a single Block. Returns (Block, next_i).

    :param lines: The list of doctags lines being parsed.
    :type lines: list[str]
    :param i: Index of the line where the <ordered_list> tag starts.
    :type i: int
    :return: A tuple of the resulting Block and the index of the line following the block.
    :rtype: tuple[Block, int]
    """
    parts, i = _collect_until(lines, i, "</ordered_list>")
    text = "\n".join(parts)
    x0, y0 = extract_xy0(text)
    return Block(raw=text, y0=y0, x0=x0, is_list_item=False), i


def _parse_unordered_list(lines: list[str], i: int) -> tuple[list[Block], int]:
    """
    Parse an <unordered_list>…</unordered_list> block into individual Blocks. Returns (blocks, next_i).

    :param lines: The list of doctags lines being parsed.
    :type lines: list[str]
    :param i: Index of the line where the <unordered_list> tag starts.
    :type i: int
    :return: A tuple of the list of parsed Blocks (one per list item) and the index of the line following the block.
    :rtype: tuple[list[Block], int]
    """
    parts, i = _collect_until(lines, i, TAG_UL_CLOSE)
    ul_text = "\n".join(parts)
    blocks = []
    for item in re.findall(r"<list_item>.*?</list_item>", ul_text, flags=re.DOTALL):
        x0, y0 = extract_xy0(item)
        blocks.append(Block(raw=item.replace("\n", "").strip(), y0=y0, x0=x0, is_list_item=True))
    return blocks, i


def parse_blocks(content: str) -> list[Block]:
    """
    - ordered_list  : treated as a single block so that </ordered_list> (y0=None) never gets
      sorted ahead of its items during sorting.
    - unordered_list: items are extracted individually (is_list_item=True) so they can be
      sorted; render_blocks re-wraps them afterwards.

    :param content: The full .doctags file content (with the <doctag>/</doctag> wrapper already stripped).
    :type content: str
    :return: The list of parsed Blocks in their original document order.
    :rtype: list[Block]
    """
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    blocks: list[Block] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        if "<ordered_list>" in line:
            block, i = _parse_ordered_list(lines, i)
            blocks.append(block)
        elif "<unordered_list>" in line:
            new_blocks, i = _parse_unordered_list(lines, i)
            blocks.extend(new_blocks)
        else:
            x0, y0 = extract_xy0(line)
            blocks.append(Block(raw=line, y0=y0, x0=x0, is_list_item=False))
            i += 1

    return blocks


def split_pages(blocks: list[Block], expected_pages: int | None = None) -> list[list[Block]]:
    """
    Splits a list of blocks into pages. <page_footer> is authoritative when present (at
    least once in the document): it is the only reliable boundary, and <page_break> is
    then ignored (neither kept in a page, nor used as a split trigger).

    Docling sometimes inserts a misplaced standalone <page_break> (an extraction
    artifact, observed in the middle of a physical page's content rather than at its
    actual boundary). Treating it as a split trigger would then produce an extra
    phantom page: the physical page in question would be split into two chunks, and
    downstream url_tuning_vlm.py (which loops over range(1, n_pages+1) where n_pages
    comes from the actual PDF page count) would never process the extra chunk,
    page content silently lost, and another page duplicated under two numbers. Hence
    the choice to rely only on <page_footer> when it exists.

    But some Docling documents emit no <page_footer> at all (no page has one), in
    which case <page_break> is the only available boundary: simply ignoring it would
    merge the entire document into a single page (observed on "Liste des
    représentations suisses à l'étranger", 7 PDF pages, 6 <page_break>, 0
    <page_footer>; without this fallback, split_pages() returned a single page
    containing the entire doctags). Hence the fallback to <page_break> as a trigger
    only if the document contains no <page_footer> at all.

    Formerly a known limitation, now arbitrated by *expected_pages*: the decision
    (footer vs break) used to be made once for the entire document, so a document
    where ONLY SOME pages carry a <page_footer> was mishandled — has_footer=True
    merged the page without a footer into the next one. Observed on "TN -
    Allègement dès 01.2025" (3 PDF pages, page 1 without footer): reordering
    produced 2 pages and markdown-control rejected the document.

    Neither signal is reliable on its own — a footer can be missing, a
    <page_break> can be misplaced — so when the caller knows the real PDF page
    count, it is passed here and used to pick the strategy that reproduces it.
    Whenever the primary strategy already yields *expected_pages* (or the caller
    passes None), the result is exactly what it was before: only documents that
    are currently mis-split can change.

    The downstream check (comparing the number of reassembled pages to the PDF
    page count, cf. markdown_control.py) remains the safety net when neither
    strategy matches.

    :param blocks: The list of parsed Blocks (in document order, spanning potentially multiple pages).
    :type blocks: list[Block]
    :param expected_pages: Real PDF page count, used to arbitrate between the two
        splitting strategies. None disables arbitration (historical behavior).
    :type expected_pages: int | None
    :return: The list of pages, each page being a list of Blocks.
    :rtype: list[list[Block]]
    """
    has_footer = any(TAG_PAGE_FOOTER in b.raw for b in blocks)

    pages, discarded_breaks = _split(blocks, by_footer=has_footer)

    if expected_pages is not None and len(pages) != expected_pages:
        fallback, _ = _split(blocks, by_footer=not has_footer)
        if len(fallback) == expected_pages:
            _log.warning(
                "Splitting by %s produced %d page(s) for %d PDF page(s); falling "
                "back to %s, which matches. A page is likely missing its "
                "<page_footer>.",
                TAG_PAGE_FOOTER if has_footer else TAG_PAGE_BREAK, len(pages),
                expected_pages,
                TAG_PAGE_BREAK if has_footer else TAG_PAGE_FOOTER,
            )
            return fallback

    if has_footer and discarded_breaks:
        _log.warning(
            "%d standalone <page_break>(s) discarded while the document contains "
            "<page_footer> tags, check that no page lost its footer (see known "
            "limitation of split_pages()).",
            discarded_breaks,
        )

    return pages


def _split(blocks: list[Block], *, by_footer: bool) -> tuple[list[list[Block]], int]:
    """One splitting pass. Returns (pages, number of discarded standalone breaks).

    ``by_footer`` selects the boundary: <page_footer> closes a page, or a
    standalone <page_break> opens the next one.
    """
    pages: list[list[Block]] = []
    current: list[Block] = []
    discarded_breaks = 0

    for block in blocks:
        is_standalone_break = TAG_PAGE_BREAK in block.raw and TAG_PAGE_FOOTER not in block.raw
        if is_standalone_break:
            if not by_footer and current:
                pages.append(current)
                current = []
            elif by_footer:
                discarded_breaks += 1
            continue  # never kept: pure marker, never re-added to a page

        current.append(block)
        if by_footer and TAG_PAGE_FOOTER in block.raw:
            pages.append(current)
            current = []

    if current:
        pages.append(current)

    return pages, discarded_breaks


def sort_page(blocks: list[Block]) -> list[Block]:
    """
    Sorts the blocks of a page by ascending y0, then ascending x0.
    Blocks without coordinates (y0=None) are placed first, in their original order.
    The original index serves as a tiebreaker to guarantee a stable sort.

    :param blocks: The list of Blocks belonging to a single page.
    :type blocks: list[Block]
    :return: The list of Blocks sorted by position (y0, then x0), stable with respect to original order.
    :rtype: list[Block]
    """
    indexed = list(enumerate(blocks))
    no_pos  = [(i, b) for i, b in indexed if b.y0 is None]
    with_pos = [(i, b) for i, b in indexed if b.y0 is not None]
    with_pos.sort(key=lambda t: (t[1].y0, t[1].x0 if t[1].x0 is not None else _NO_X0, t[0]))
    return [b for _, b in no_pos] + [b for _, b in with_pos]


def render_blocks(blocks: list[Block]) -> str:
    """
    Converts a list of sorted blocks into text, re-wrapping list_item blocks in <unordered_list>.

    :param blocks: The list of Blocks, already sorted, to render back into doctags text.
    :type blocks: list[Block]
    :return: The rendered doctags text for the page.
    :rtype: str
    """
    out: list[str] = []
    in_ul = False

    for block in blocks:
        if block.is_list_item:
            if not in_ul:
                out.append("<unordered_list>")
                in_ul = True
            out.append(block.raw)
        else:
            if in_ul:
                out.append(TAG_UL_CLOSE)
                in_ul = False
            out.append(block.raw)

    if in_ul:
        out.append(TAG_UL_CLOSE)

    return "\n".join(out)


def reorder_doctags(
    input_path: Path, output_path: Path, expected_pages: int | None = None
) -> None:
    """
    Reads input_path, re-sorts the blocks by y0/x0 page by page, writes the result to output_path.

    :param input_path: Path to the source .doctags file to reorder.
    :type input_path: Path
    :param output_path: Path where the reordered .doctags file will be written.
    :type output_path: Path
    :param expected_pages: Real PDF page count, forwarded to split_pages() to
        arbitrate between the two splitting strategies. None disables arbitration.
    :type expected_pages: int | None
    """

    content = input_path.read_text(encoding="utf-8")
    content = re.sub(r"</?doctag>\s*", "", content).strip()

    pages = split_pages(parse_blocks(content), expected_pages)
    result_pages = [render_blocks(sort_page(page)) for page in pages]

    # split_pages() already drops every standalone <page_break> marker (see its docstring),
    # so no rendered page should ever start with one, this strip is a defensive no-op kept
    # in case a future Docling doctags variant reintroduces a leading marker some other way.
    cleaned = [re.sub(r"^\s*<page_break\s*/?>\s*\n?", "", p) for p in result_pages]
    body = "\n<page_break>\n".join(cleaned)

    final = "<doctag>\n" + body + "\n</doctag>\n"
    output_path.write_text(final, encoding="utf-8")
    _log.info("Doctags reordered (%d page(s)): %s", len(pages), output_path)


class ReorderDoctagsStep(PipelineStep):
    """Reorders the blocks of the .doctags file by position (y0 then x0), page by page."""

    name = "reorder-doctags"
    description = "Reordering of doctags tags"
    requires_vlm = False

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.doctags]

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.reordered_doctags]

    def execute(self, ctx: PipelineContext) -> StepResult:
        input_path = ctx.workspace.doctags
        output_path = ctx.workspace.reordered_doctags
        output_path.parent.mkdir(parents=True, exist_ok=True)

        _log.info("Input : %s", input_path)
        _log.info("Output: %s", output_path)
        try:
            reorder_doctags(input_path, output_path, pdf_page_count(ctx.workspace.source_pdf))
        except Exception as exc:
            _log.exception("Error while reordering %s", input_path.name)
            raise StepFailed(f"reorder-doctags failed on {input_path.name}: {exc}") from exc
        return StepResult(StepStatus.OK, outputs=self.outputs(ctx))
