"""csv-to-jsonlines step, CSV tables (Docling) -> JSONL files.

Conversion of the simple_extraction/csv_to_jsonlines.py script 
Processing functions are MOVED as-is, only parse_args/main/resolve_* are
removed, replaced by the PipelineStep contract and ctx.workspace.

Semantics preserved identically:
1) missing tables/ directory -> StepInputMissing (was SystemExit "directory not found")
2) no CSV files -> StepFailed (was SystemExit code 1)
3) empty table -> skipped (skip counter)
4) error on a CSV -> counted, the step fails at the end of the loop (was exit 1)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import jsonlines
import pandas as pd

from ..core.step import PipelineStep, StepResult, StepStatus
from ..exceptions import StepFailed

if TYPE_CHECKING:
    from ..context import PipelineContext

_log = logging.getLogger(__name__)


# CSV -> JSONL utilities, moved as-is from csv_to_jsonlines.py
def deduplicate_columns(columns: list[str]) -> list[str]:
    """
    Add a numeric suffix to duplicate column names (col, col_2, col_3...).

    :param columns: List of column names, possibly containing duplicates.
    :type columns: list[str]
    :return: List of column names with duplicates renamed to be unique.
    :rtype: list[str]
    """
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


def safe_row_dict(row: pd.Series) -> dict:
    """
    Convert a row into a JSON-safe dict: NaN becomes None, all values become str.

    :param row: A pandas Series representing one row of the table.
    :type row: pd.Series
    :return: Dict mapping each column name to its JSON-safe value.
    :rtype: dict
    """
    return {k: (None if pd.isna(v) else str(v)) for k, v in row.items()}


def _detect_header_row(csv_path: Path) -> int:
    """Determine whether the real header is on row 0 or row 1.

    Docling can produce numeric column names (0, 1, 2...) when the actual
    header is located in the first data row. We read nrows=1 only once to
    cover both checks (avoids the original triple read).

    Returns 0 (header on row 0) or 1 (header on row 1).
    """
    df_peek = pd.read_csv(csv_path, nrows=1)
    if df_peek.empty:
        return 0

    all_numeric_cols = all(str(col).strip().isdigit() for col in df_peek.columns)
    if not all_numeric_cols:
        return 0

    first_row = df_peek.iloc[0]
    values = [v for v in first_row if not pd.isna(v)] # ignore NaN values, which could skew the detection
    first_row_is_text = bool(values) and all(
        isinstance(v, str) and not str(v).replace(".", "").replace("-", "").isdigit()
        for v in values
    )
    return 1 if first_row_is_text else 0


def process_csv(csv_path: Path, output_dir: Path) -> bool:
    """
    Convert a CSV file into JSONL inside output_dir. Returns True if a file was written.

    :param csv_path: Path to the source CSV file.
    :type csv_path: Path
    :param output_dir: Directory in which the JSONL file will be written.
    :type output_dir: Path
    :return: True if a JSONL file was written, False if the table was empty and skipped.
    :rtype: bool
    """
    header_row = _detect_header_row(csv_path)
    df = pd.read_csv(csv_path, header=header_row)
    df.columns = deduplicate_columns([str(col) for col in df.columns])

    if df.empty:
        _log.warning("%s — skipped (empty table)", csv_path.name)
        return False

    jsonl_path = output_dir / csv_path.with_suffix(".jsonl").name
    with jsonlines.open(jsonl_path, mode="w") as writer:
        for _, row in df.iterrows():
            writer.write(safe_row_dict(row))

    _log.info("%s → %s", csv_path.name, jsonl_path.name)
    return True


class CsvToJsonlinesStep(PipelineStep):
    """Converts each tables/*.csv file into .jsonl (one JSON line per table row)."""

    name = "csv-to-jsonlines"
    description = "Conversion of CSV tables to JSONL"
    requires_vlm = False

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.tables_dir]

    def outputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.tables_dir]

    def execute(self, ctx: PipelineContext) -> StepResult:
        input_dir = ctx.workspace.tables_dir
        output_dir = input_dir  # défaut historique : JSONL à côté des CSV

        csv_files = sorted(input_dir.glob("*.csv"))
        if not csv_files:
            raise StepFailed(f"No CSV files found in: {input_dir}")

        _log.info("%d CSV file(s) found in: %s", len(csv_files), input_dir)
        _log.info("JSONL output in: %s", output_dir)

        n_ok = n_skip = n_err = 0
        for csv_path in csv_files:
            try:
                if process_csv(csv_path, output_dir):
                    n_ok += 1
                else:
                    n_skip += 1
            except Exception as e:
                _log.exception("%s -> error: %s", csv_path.name, e)
                n_err += 1

        _log.info(
            "Done, %d converted, %d skipped (empty), %d error(s).",
            n_ok, n_skip, n_err,
        )
        if n_err > 0:
            raise StepFailed(f"{n_err} CSV file(s) failed to convert in {input_dir}")
        return StepResult(
            StepStatus.OK,
            outputs=self.outputs(ctx),
            message=f"{n_ok} converted, {n_skip} skipped",
        )
