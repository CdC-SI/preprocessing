"""table-jsonl-normalize stage — replaces native Markdown tables surviving in
_final.md with their JSONL-per-line equivalent -> _final_embed.md.

Conversion of tools/markdown_tables_to_jsonl.py's parsing core into a
registered pipeline step. The CLI tool now imports these functions instead of
duplicating them (see tools/markdown_tables_to_jsonl.py's module docstring).

Why this step exists: markdown-control's own TABLES prompt rule says a JSON
table must never be turned into a Markdown table, but the VLM does not follow
that rule reliably (observed: converted for some pages/tables, respected for
others in the same document). This step is a deterministic safety net,
independent of the VLM, applied once at the very end of the content chain.

Deliberately does NOT rewrite _final.md in place: _final.md stays the
human-readable artifact (clean Markdown tables where the VLM produced them);
_final_embed.md is the machine-facing variant, read in preference to
_final.md by document_embedder.py and metadata_generation.py when it exists.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import jsonlines

from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed

if TYPE_CHECKING:
    from ..context import PipelineContext

_log = logging.getLogger(__name__)


# Parsing markdown -> tables
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
# Anchored start/end (^...$): emphasis is stripped only when it wraps the
# WHOLE segment. A "anywhere in the text" match would confuse an isolated
# formula asterisk ("4*2=8") or a list bullet with an emphasis pair, and
# would delete the text between the two occurrences.
_FULL_EMPHASIS_RE = re.compile(r"^(\*\*\*|\*\*|\*|___|__|_)(.+)\1$", re.DOTALL)


def deduplicate_columns(columns: list[str]) -> list[str]:
    """Suffixes duplicate column names with a running number (col, col_2, col_3...)."""
    counts: dict[str, int] = {}
    result = []
    for col in columns:
        if col not in counts:
            counts[col] = 1
            result.append(col)
        else:
            counts[col] += 1
            result.append(f"{col}_{counts[col]}")
    return result


def fill_empty_headers(labels: list[str]) -> list[str]:
    """Replaces blank header cells with a positional placeholder (column_N, 1-based).

    A column with no header text in the Markdown (e.g. the 2nd column of
    ``| Adhésion à l'AFac possible ☺ | |``) would otherwise produce an empty
    JSON key (``""``), unusable by a downstream LLM.
    """
    return [label if label else f"column_{idx}" for idx, label in enumerate(labels, start=1)]


def _strip_full_emphasis(text: str) -> str:
    """Removes Markdown emphasis (bold/italic, asterisk or underscore) ONLY
    when it wraps the entire given text. Otherwise returns the text
    unchanged — an isolated ``*`` or ``_`` in the middle of the text is
    never touched."""
    match = _FULL_EMPHASIS_RE.match(text)
    return match.group(2) if match else text


def clean_cell_text(value: str) -> str:
    """Normalizes a Markdown cell's text into plain text for the JSON output.

    - Each ``<br>``-separated segment (line break inside a merged cell) is
      cleaned independently, empty segments dropped, then joined with
      ``"; "`` — avoids a trailing ``"; "`` that a plain ``<br>`` substitution
      followed by ``.strip()`` would leave (``strip`` only removes
      whitespace, not a ``;``).
    - Markdown emphasis is stripped segment by segment (see
      _strip_full_emphasis), so a per-segment wrap (``*a*<br>*b*``) is
      recognized even when it doesn't hold across the whole joined string.

    Applied to both headers and values: a VLM correction can bold any cell,
    not only values.
    """
    segments = (segment.strip() for segment in _BR_RE.split(value))
    cleaned = [_strip_full_emphasis(segment) for segment in segments if segment]
    return "; ".join(cleaned).strip()


def _is_table_line(line: str) -> bool:
    s = line.strip()
    return len(s) > 1 and s.startswith("|") and s.endswith("|")


def _split_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_separator_line(line: str) -> bool:
    cells = _split_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-+:?", c) for c in cells)


def _is_table_start(lines: list[str], i: int) -> bool:
    """True if lines[i] is a header immediately followed by its |---|---| separator."""
    n = len(lines)
    return _is_table_line(lines[i]) and i + 1 < n and _is_table_line(lines[i + 1]) and _is_separator_line(lines[i + 1])


def _build_row(cells: list[str], header_keys: list[str], prev_row: dict | None) -> dict:
    """
    Builds the {column: value} dict for one data row. Generic forward-fill:
    an empty cell is filled with the same column's value from the previous
    row (purely positional rule, valid for any table/column — no hardcoded
    column name). Reproduces the standard "unmerge" behavior for a merged
    (rowspan) PDF table cell, which the upstream VLM correction does not
    restore reliably.

    prev_row=None disables forward-fill for this row (see _extract_table_at:
    never applied to a block's last row — where page-break artifacts land,
    and where the row above has no real relationship).
    """
    row: dict = {}
    for idx, key in enumerate(header_keys):
        value = clean_cell_text(cells[idx]) if idx < len(cells) else ""
        if not value and prev_row and prev_row.get(key):
            value = prev_row[key]
        row[key] = value
    return row


def _collect_raw_rows(lines: list[str], start: int, header_labels: list[str]) -> tuple[list[list[str]], int]:
    """Consumes raw (un-merged) data rows starting at start, until a
    non-table line or a new (header, separator) pair. Skips rows identical
    to the header (page-boundary duplicate).

    Comparison uses cleaned text (see clean_cell_text) on both sides, not
    raw text: the VLM correction applies emphasis inconsistently from one
    page to the next (the same header can be bold on one page and not on the
    next), so a raw comparison would miss the duplicate and let it leak
    through as a fake data row.
    """
    n = len(lines)
    canonical_header = [clean_cell_text(label) for label in header_labels]
    raw_rows: list[list[str]] = []
    j = start
    while j < n and _is_table_line(lines[j]) and not _is_table_start(lines, j):
        cells = _split_cells(lines[j])
        if [clean_cell_text(cell) for cell in cells] != canonical_header:
            raw_rows.append(cells)
        j += 1
    return raw_rows, j


def _extract_table_at(lines: list[str], i: int) -> tuple[list[dict], int] | None:
    """
    If lines[i] starts a table (header + separator at i+1), consumes the
    data rows that follow — until a non-table line or a new (header,
    separator) pair, which marks the start of an adjacent table with no
    separator line between the two.

    Forward-fill (see _build_row) applied to every row EXCEPT the block's
    last one: a page break in the middle of a group of merged rows can leave
    an "orphan" at the very end of the block, right before a new header
    restarts for a completely different group — in that case the previous
    row has no real relationship and a forward-fill would insert a false
    value instead of an honest empty cell.

    :return: (rows, next_index) if lines[i] starts a table, else None
    """
    if not _is_table_start(lines, i):
        return None

    header_labels = _split_cells(lines[i])
    cleaned_labels = fill_empty_headers([clean_cell_text(label) for label in header_labels])
    header_keys = deduplicate_columns(cleaned_labels)
    # header_labels (raw) is passed on: _collect_raw_rows cleans both sides
    # itself before comparing, so the duplicate-header check is robust to
    # emphasis differences without needing the filled/deduplicated keys.
    raw_rows, next_i = _collect_raw_rows(lines, i + 2, header_labels)

    rows: list[dict] = []
    prev_row: dict | None = None
    last_idx = len(raw_rows) - 1
    for idx, cells in enumerate(raw_rows):
        row = _build_row(cells, header_keys, None if idx == last_idx else prev_row)
        rows.append(row)
        prev_row = row
    return rows, next_i


def iter_blocks(text: str):
    """
    Walks the text once and yields a sequence of blocks:
    ("text", line) for each non-table line, ("table", rows) once per native
    Markdown table detected (| col | col |), rows being the list of
    {column: value} dicts for that table.

    Single entry point shared by parse_markdown_tables() (separate .jsonl
    files, traceability) and render_markdown_with_jsonl_tables() (document
    rewritten for embedding) — so the two can't drift apart if the table
    format changes later.

    :param text: markdown content to parse
    :return: generator of ("text", str) | ("table", list[dict]) tuples
    """
    lines = text.split("\n")
    i, n = 0, len(lines)
    while i < n:
        extracted = _extract_table_at(lines, i)
        if extracted is not None:
            rows, next_i = extracted
            if rows:
                yield "table", rows
            i = next_i
            continue
        yield "text", lines[i]
        i += 1


def parse_markdown_tables(text: str) -> list[list[dict]]:
    """Extracts native Markdown tables from a text. See iter_blocks()."""
    return [rows for kind, rows in iter_blocks(text) if kind == "table"]


def render_markdown_with_jsonl_tables(text: str) -> str:
    """
    Rebuilds the document, replacing each native Markdown table (| col | col |)
    with its equivalent JSONL lines (one per row), preserving all non-table
    text unchanged. Reuses iter_blocks() — so, by construction, produces
    exactly the same lines as those written to <doc>-table-N.jsonl by
    write_tables_jsonl().

    :param text: source markdown content
    :return: markdown content with tables replaced by JSONL
    """
    out: list[str] = []
    for kind, value in iter_blocks(text):
        if kind == "table":
            out.extend(json.dumps(row, ensure_ascii=False) for row in value)
        else:
            out.append(value)
    return "\n".join(out)


def write_tables_jsonl(tables: list[list[dict]], output_dir: Path, doc_name: str) -> list[Path]:
    """Writes one file per table: <doc_name>-table-1.jsonl, -table-2.jsonl, ..."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for idx, rows in enumerate(tables, start=1):
        path = output_dir / f"{doc_name}-table-{idx}.jsonl"
        with jsonlines.open(path, mode="w") as writer:
            for row in rows:
                writer.write(row)
        _log.info("Table %d: %d row(s) -> %s", idx, len(rows), path.name)
        written.append(path)
    return written


class TableJsonlNormalizeStep(PipelineStep):
    """Replaces Markdown tables surviving in _final.md with JSONL -> _final_embed.md."""

    name = "table-jsonl-normalize"
    description = "Normalisation des tableaux Markdown résiduels en JSONL"
    requires_vlm = False

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.final_markdown]

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.final_embed_markdown]

    def execute(self, ctx: PipelineContext) -> StepResult:
        ws = ctx.workspace
        try:
            text = ws.final_markdown.read_text(encoding="utf-8")
            tables = parse_markdown_tables(text)
            if tables:
                write_tables_jsonl(tables, ws.tables_markdown_dir, ws.doc_name)
            embed_text = render_markdown_with_jsonl_tables(text)
            ws.final_embed_markdown.parent.mkdir(parents=True, exist_ok=True)
            ws.final_embed_markdown.write_text(embed_text, encoding="utf-8")
        except Exception as exc:
            _log.exception("table-jsonl-normalize failed on %s", ws.doc_name)
            raise StepFailed(f"table-jsonl-normalize failed on {ws.doc_name}: {exc}") from exc

        message = f"{len(tables)} table(s) converted" if tables else "no Markdown table found"
        _log.info("%s -> %s", message, ws.final_embed_markdown)
        return StepResult(StepStatus.OK, outputs=self.outputs(ctx), message=message)
