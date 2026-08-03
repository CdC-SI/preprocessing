"""load-jsonline-doctags stage — injection of JSONL tables into the .doctags.

Conversion of the script simple_extraction/load_jsonline_doctags.py (wave B).
Business functions MOVED as-is (invariant no. 1).

Replaces each <otsl>…</otsl> tag in the .doctags with a <text> block containing
the JSONL content of the corresponding table (matching by coordinates).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed

if TYPE_CHECKING:
    from ..context import PipelineContext

_log = logging.getLogger(__name__)


def load_jsonl_rows(jsonl_path: Path) -> list[dict]:
    """Loads all lines from a JSONL file and returns them as a list of dictionaries."""
    rows = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def jsonl_rows_to_block(rows: list[dict]) -> str:
    """Converts a list of dictionaries into a JSONL text block (one line per dictionary)."""
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)


_TABLE_COORDS_RE = re.compile(r"_page(\d+)_x(\d+)_y(\d+)_x(\d+)_y(\d+)")

TableCoords = tuple[int, int, int, int, int]  # (page, x0, y0, x1, y1)


def _parse_table_coords(filename: str) -> TableCoords | None:
    """Extracts (page, x0, y0, x1, y1) from a filename produced by
    docling_extract.export_tables() (page 1-indexed, doctags coordinates 0-500).
    Returns None for a file generated before this fix (no coordinates in the filename),
    triggers the file-order fallback matching in replace_otsl_with_jsonl.
    """
    m = _TABLE_COORDS_RE.search(filename)
    if not m:
        return None
    x0, y0, x1, y1 = (int(g) for g in m.groups()[1:])
    return (int(m.group(1)), x0, y0, x1, y1)


def _find_otsl_blocks(content: str) -> list[tuple[re.Match, TableCoords]]:
    """Locates each <otsl>…</otsl> block with its coordinates (page, x0, y0, x1, y1).
    
    Page inferred from the number of <page_footer> tags encountered before the
    block (1-indexed, same convention as docling_extract.export_tables —
    table.prov[0].page_no).
    Coordinates read directly from the opening tag <otsl><loc_x0><loc_y0><loc_x1><loc_y1>.
    """
    footer_offsets = [m.start() for m in re.finditer(r"<page_footer>", content)]
    otsl_pattern = re.compile(
        r"<otsl><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)>.*?</otsl>", re.DOTALL
    )

    blocks: list[tuple[re.Match, TableCoords]] = []
    for m in otsl_pattern.finditer(content):
        page = 1 + sum(1 for fo in footer_offsets if fo < m.start())
        x0, y0, x1, y1 = (int(g) for g in m.groups())
        blocks.append((m, (page, x0, y0, x1, y1)))
    return blocks


def replace_otsl_with_jsonl(
    doctags_path: Path,
    tables_dir: Path,
    output_path: Path,
) -> int:
    """Replaces the <otsl>…</otsl> tags with the JSONL content of the corresponding
    table, matched by coordinates (page, x0, y0, x1, y1), never by file order,
    to remain correct even if reordered_doctags.py changed the relative order of
    tables on a page (its very purpose). Falls back to the historical file-order
    method (incorrect if the order has changed, or if a document contains 10+
    tables, alphabetical sorting puts "table-10" before "table-2") only if the
    available JSONLs were generated before this fix and do not contain coordinates
    in their filenames.
    
    Returns the number of replacements performed.
    
    If no JSONL files or no <otsl> tags are found, copies the source file unchanged
    (passthrough) so that the next pipeline stage always receives a valid file.

    """
    content = doctags_path.read_text(encoding="utf-8")

    # Charger les JSONL disponibles
    jsonl_files = sorted(tables_dir.glob("*.jsonl"))
    if not jsonl_files:
        _log.warning("No JSONL files in %s — file copied without modification.", tables_dir)
        output_path.write_text(content, encoding="utf-8")
        return 0

    _log.info("%d JSONL file(s) found in: %s", len(jsonl_files), tables_dir)
    tables_in_order: list[tuple[str, list[dict]]] = []
    tables_by_coords: dict[TableCoords, tuple[str, list[dict]]] = {}
    for jsonl_path in jsonl_files:
        rows = load_jsonl_rows(jsonl_path)
        if not rows:
            continue
        tables_in_order.append((jsonl_path.name, rows))
        coords = _parse_table_coords(jsonl_path.name)
        if coords:
            tables_by_coords[coords] = (jsonl_path.name, rows)
        _log.info("  : %s : %d line(s)", jsonl_path.name, len(rows))

    if not tables_in_order:
        _log.warning("All JSONL files are empty, file copied without modification.")
        output_path.write_text(content, encoding="utf-8")
        return 0

    # Localiser les balises <otsl>
    otsl_blocks = _find_otsl_blocks(content)
    if not otsl_blocks:
        _log.warning("No <otsl> tags in %s, file copied without modification.", doctags_path.name)
        output_path.write_text(content, encoding="utf-8")
        return 0

    use_coords = len(tables_by_coords) == len(tables_in_order)
    if not use_coords:
        _log.warning(
            "JSONL without coordinates detected (generated before this fix) — falling back to "
            "file-order matching, potentially incorrect if the table order has changed. "
            "Re-run steps 1/2/4 to regenerate JSONL files with coordinates."
        )

    if len(otsl_blocks) != len(tables_in_order):
        _log.warning(
            "%d <otsl> block(s) vs %d JSONL table(s), best-effort replacement.",
            len(otsl_blocks), len(tables_in_order),
        )

    # Replacement by splitting/joining (no in-place string mutation, the positions 
    # of matches remain valid because `content` is never rewritten)
    result_parts: list[str] = []
    cursor = 0
    n_replaced = 0

    for idx, (match, coords) in enumerate(otsl_blocks):
        table = tables_by_coords.get(coords) if use_coords else None
        if table is None:
            if idx < len(tables_in_order):
                table = tables_in_order[idx]
            else:
                _log.warning(
                    "No JSONL table for <otsl> block no. %d (page=%d), ignored.",
                    idx + 1, coords[0],
                )
                continue

        jsonl_name, rows = table
        jsonl_block = jsonl_rows_to_block(rows)
        new_tag = f"<text>\n{jsonl_block}\n</text>"

        result_parts.append(content[cursor:match.start()])
        result_parts.append(new_tag)
        cursor = match.end()
        n_replaced += 1

        _log.info(
            "  <otsl> block %d/%d (page=%d) replaced — %s (%d line(s), %d chars)",
            idx + 1, len(otsl_blocks), coords[0], jsonl_name, len(rows), len(jsonl_block),
        )

    result_parts.append(content[cursor:])
    result = "".join(result_parts)

    output_path.write_text(result, encoding="utf-8")
    _log.info("Enriched doctags saved: %s", output_path)
    return n_replaced


class LoadJsonlineDoctagsStep(PipelineStep):
    """Injects JSONL tables into the reordered doctags (<otsl> → <text>)."""

    name = "load-jsonline-doctags"
    description = "Doctags enriched with tables (and images)"
    requires_vlm = False

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.reordered_doctags, ctx.workspace.tables_dir]

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.reordered_with_tables_doctags]

    def validate_inputs(self, ctx: PipelineContext) -> None:
        """Only the doctags file is required: a missing tables/ folder was a
        passthrough (copy without modification, exit 0) — behavior preserved."""
        from ..exceptions import StepInputMissing

        if not ctx.workspace.reordered_doctags.exists():
            raise StepInputMissing(
                f"Step '{self.name}': missing input(s): {ctx.workspace.reordered_doctags}"
            )

    def execute(self, ctx: PipelineContext) -> StepResult:
        doctags_path = ctx.workspace.reordered_doctags
        tables_dir = ctx.workspace.tables_dir
        output_path = ctx.workspace.reordered_with_tables_doctags
        output_path.parent.mkdir(parents=True, exist_ok=True)

        _log.info("Source doctags  : %s", doctags_path)
        _log.info("Tables folder  : %s", tables_dir)
        _log.info("Output          : %s", output_path)

        if not tables_dir.exists():
            _log.warning(
                "Tables folder not found (%s) — file copied without modification.", tables_dir
            )
            output_path.write_text(doctags_path.read_text(encoding="utf-8"), encoding="utf-8")
            return StepResult(StepStatus.OK, outputs=self.outputs(ctx), message="passthrough")

        try:
            n = replace_otsl_with_jsonl(doctags_path, tables_dir, output_path)
        except Exception as exc:
            _log.exception("Error while injecting tables into %s", doctags_path.name)
            raise StepFailed(
                f"load-jsonline-doctags failed on {doctags_path.name}: {exc}"
            ) from exc

        _log.info("Completed — %d <otsl> replacement(s) performed.", n)
        return StepResult(
            StepStatus.OK, outputs=self.outputs(ctx), message=f"{n} remplacement(s)"
        )
