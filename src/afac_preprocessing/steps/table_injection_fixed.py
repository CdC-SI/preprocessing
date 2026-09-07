"""Fixed variants of steps 04 (csv-to-jsonlines) and 05 (load-jsonline-doctags).

Opt-in through ``afac-preprocess run --fixed-tables``: without the flag the
canonical steps run unchanged, so an existing pipeline keeps its exact
behaviour. See ``core/registry.build_default_steps``.

Why these variants exist — the failure they fix, observed on
``TE - Revenu déterminant``, is not a content *loss* but a content
*substitution*: the document ends up holding table-04's data twice, once of
which sits where table-03's data belongs. A reader (human or LLM) gets an
answer that is confidently wrong.

The causal chain, verified step by step:

1. ``table-03`` is a 2-column × **1-row** table, exported by Docling with
   numeric column names (``0,1``). ``csv_to_jsonlines._detect_header_row``
   concludes the real header sits on row 1, promotes the only data row to be
   the header, and is left with an empty DataFrame: the table is counted as
   "skipped (empty table)" and **no JSONL is written**.
2. In step 05 the orphaned ``<otsl>`` block falls through to the positional
   fallback ``tables_in_order[idx]``, which hands it *the next table*. Every
   following index is shifted.

Measured blast radius on the corpus: 1 document out of 133 having tables. Rare,
but silent — the step ends successfully with a mere WARNING.

Two independent fixes, in the two classes below:

- ``TableJsonlBuilder``: promoting a header must never empty a table.
- ``OtslTableInjector``: never substitute a table by position. This second one
  is what protects against *any* future cause of a missing JSONL, not just the
  header heuristic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from ..core.step import StepResult, StepStatus
from ..exceptions import StepFailed
from .csv_to_jsonlines import (
    CsvToJsonlinesStep,
    _detect_header_row,
    deduplicate_columns,
    safe_row_dict,
)
from .load_jsonline_doctags import (
    LoadJsonlineDoctagsStep,
    TableCoords,
    _find_otsl_blocks,
    _parse_table_coords,
    jsonl_rows_to_block,
    load_jsonl_rows,
)

if TYPE_CHECKING:
    from ..context import PipelineContext

_log = logging.getLogger(__name__)

CSV_GLOB = "*.csv"
JSONL_GLOB = "*.jsonl"


@dataclass
class TableJsonlBuilder:
    """Table CSV (Docling) -> JSONL rows, without ever emptying the table.

    Keeps step 04's header heuristic (``_detect_header_row``), which is right
    in the general case: Docling does emit numeric column names when the real
    header lives in the first data row. It only lacks a guard for the
    single-row table, where promoting that row leaves no data at all.
    """

    def read_table(self, csv_path: Path) -> pd.DataFrame:
        """Read the CSV with the detected header, falling back to ``header=0``
        when that detection empties the table.

        This is the root-cause fix: a non-empty table must never come back
        empty from a mere header decision.
        """
        header_row = _detect_header_row(csv_path)
        frame = pd.read_csv(csv_path, header=header_row)

        if frame.empty and header_row != 0:
            fallback = pd.read_csv(csv_path, header=0)
            if not fallback.empty:
                _log.info(
                    "%s — header on row %d emptied the table, falling back to header=0 (%d row(s)).",
                    csv_path.name, header_row, len(fallback),
                )
                frame = fallback

        frame.columns = deduplicate_columns([str(col) for col in frame.columns])
        return frame

    def build_rows(self, csv_path: Path) -> list[dict]:
        """JSONL rows for one table, empty list if the table really is empty."""
        frame = self.read_table(csv_path)
        if frame.empty:
            _log.warning("%s — genuinely empty table, skipped.", csv_path.name)
            return []
        return [safe_row_dict(row) for _, row in frame.iterrows()]


@dataclass
class Table:
    """An extracted table, ready to be injected."""

    name: str
    coords: TableCoords | None
    rows: list[dict]

    @property
    def bbox(self) -> tuple[int, ...] | None:
        """Coordinates without the page number — the matching key, see
        ``OtslTableInjector``."""
        return None if self.coords is None else self.coords[1:]


@dataclass
class InjectionOutcome:
    """What the injection did with each ``<otsl>`` block of the document."""

    doctags: str
    replaced: list[str] = field(default_factory=list)
    preserved: list[TableCoords] = field(default_factory=list)
    unused_tables: list[str] = field(default_factory=list)


@dataclass
class OtslTableInjector:
    """Replace ``<otsl>`` blocks with their JSONL, without positional substitution.

    Matching is done on the **bounding box** (x0, y0, x1, y1), never on file
    order. Step 05's positional fallback only exists for legacy JSONL whose
    filename carries no coordinates; applying it when coordinates *are*
    available is precisely what turns "a missing table" into "a table replaced
    by another one".

    The page number is deliberately **excluded** from the key. Step 05 includes
    it, but ``_find_otsl_blocks`` cannot read it: it infers the page by counting
    preceding ``<page_footer>`` tags, which fails outright on a document that
    has none — observed on "Liste des représentations suisses à l'étranger"
    (7 tables, 7 blocks, inferred page = 1 for all of them, so 6 failed matches
    while the bounding boxes lined up exactly). The bbox comes from the
    ``<otsl><loc_...>`` tag itself and is therefore reliable; checked across the
    133 documents with tables, it identifies a table uniquely (zero collision).

    A block with no match keeps its original ``<otsl>``. No content is lost:
    step 09 knows how to turn a native ``<otsl>`` into a markdown table. The
    format differs (markdown table instead of JSONL), the data does not.
    """

    def inject(self, doctags: str, tables: list[Table]) -> InjectionOutcome:
        """Inject the tables into the doctags.

        :param doctags: content of ``<doc>_reordered.doctags``.
        :param tables: available tables, with their coordinates.
        :return: the resulting doctags and the detail of what was done.
        """
        blocks = _find_otsl_blocks(doctags)
        by_bbox = {table.bbox: table for table in tables if table.bbox is not None}

        outcome = InjectionOutcome(doctags=doctags)
        if not blocks:
            _log.warning("No <otsl> block — doctags left unchanged.")
            outcome.unused_tables = [table.name for table in tables]
            return outcome

        parts: list[str] = []
        cursor = 0
        used: set[str] = set()

        for match, coords in blocks:
            table = by_bbox.get(coords[1:])
            if table is None:
                # The block is not rewritten and the cursor does not advance:
                # the original <otsl> is re-emitted as-is by the final append.
                # No arbitrary table is substituted.
                _log.warning(
                    "<otsl> block bbox=%s has no matching JSONL — block kept as-is "
                    "(no substitution).",
                    coords[1:],
                )
                outcome.preserved.append(coords)
                continue

            parts.append(doctags[cursor : match.start()])
            parts.append(f"<text>\n{jsonl_rows_to_block(table.rows)}\n</text>")
            cursor = match.end()
            used.add(table.name)
            outcome.replaced.append(table.name)

        parts.append(doctags[cursor:])
        outcome.doctags = "".join(parts)
        outcome.unused_tables = [table.name for table in tables if table.name not in used]
        return outcome


class FixedCsvToJsonlinesStep(CsvToJsonlinesStep):
    """Step 04 with ``TableJsonlBuilder``'s header guard.

    Subclasses the real step: ``name``, ``inputs()``, ``outputs()`` and
    ``is_applicable()`` are inherited untouched, so pipeline wiring and
    ``--from-step`` / ``--only`` references stay valid. Only ``execute()``
    changes, and it writes to the canonical ``tables/*.jsonl`` path.
    """

    def execute(self, ctx: PipelineContext) -> StepResult:
        tables_dir = ctx.workspace.tables_dir
        csv_files = sorted(tables_dir.glob(CSV_GLOB))
        if not csv_files:
            raise StepFailed(f"No CSV files found in: {tables_dir}")

        builder = TableJsonlBuilder()
        n_ok = n_skip = 0
        for csv_path in csv_files:
            rows = builder.build_rows(csv_path)
            if not rows:
                n_skip += 1
                continue
            jsonl_path = tables_dir / csv_path.with_suffix(".jsonl").name
            jsonl_path.write_text(jsonl_rows_to_block(rows) + "\n", encoding="utf-8")
            n_ok += 1

        _log.info("Done, %d converted, %d skipped (empty).", n_ok, n_skip)
        return StepResult(
            StepStatus.OK,
            outputs=self.outputs(ctx),
            message=f"{n_ok} converted, {n_skip} skipped",
        )


class FixedLoadJsonlineDoctagsStep(LoadJsonlineDoctagsStep):
    """Step 05 with ``OtslTableInjector``'s bbox matching.

    Same inheritance principle as above: only the injection logic changes, the
    output stays ``<doc>_reordered_with_tables.doctags``.
    """

    def execute(self, ctx: PipelineContext) -> StepResult:
        doctags_path = ctx.workspace.reordered_doctags
        tables_dir = ctx.workspace.tables_dir
        output_path = ctx.workspace.reordered_with_tables_doctags
        output_path.parent.mkdir(parents=True, exist_ok=True)

        doctags = doctags_path.read_text(encoding="utf-8")

        if not tables_dir.exists():
            _log.warning("Tables folder not found (%s) — file copied unchanged.", tables_dir)
            output_path.write_text(doctags, encoding="utf-8")
            return StepResult(StepStatus.OK, outputs=self.outputs(ctx), message="passthrough")

        tables = [
            table
            for jsonl_path in sorted(tables_dir.glob(JSONL_GLOB))
            if (
                table := Table(
                    name=jsonl_path.name,
                    coords=_parse_table_coords(jsonl_path.name),
                    rows=load_jsonl_rows(jsonl_path),
                )
            ).rows
        ]

        outcome = OtslTableInjector().inject(doctags, tables)
        output_path.write_text(outcome.doctags, encoding="utf-8")

        for name in outcome.unused_tables:
            _log.warning("Table %s built but never injected (no matching <otsl>).", name)

        _log.info(
            "Completed — %d replacement(s), %d <otsl> block(s) kept.",
            len(outcome.replaced), len(outcome.preserved),
        )
        return StepResult(
            StepStatus.OK,
            outputs=self.outputs(ctx),
            message=f"{len(outcome.replaced)} replacement(s), {len(outcome.preserved)} kept",
        )
