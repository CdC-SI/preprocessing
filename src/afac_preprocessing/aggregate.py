"""Aggregation of CSV files by document into one global CSV per root folder.

A root is a direct child of data/input_files/ (afac, and any future corpus). 
The global file is <output>/<root>/<root>.csv, alongside the document directory tree:
output_files_preprocessing/
└── afac/
    ├── afac.csv                                   <- produced here
    └── Adhésion/<doc>/metadata/<doc>_final.csv    <- unchanged

This is not a 14th pipeline step: a step runs per document and has no visibility of the batch. 
It would rewrite the global CSV N times per batch, and its output would depend on other documents, 
which would break the inputs()/outputs() contract.
It is a batch completion action, called by Pipeline.run_batch() and exposed through afac-preprocess aggregate.
"""

from __future__ import annotations

import csv
import logging
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

# Pipeline CSV format, single source of truth, shared with `write_csv_row` 
# (`steps/metadata_generation.py`): the global CSV must be readable exactly 
# like the per-document CSV files.
CSV_HEADER = ["CONTENT", "METADATA", "EMBEDDING"]
CSV_QUOTING = csv.QUOTE_ALL

# The EMBEDDING column is several KB per row, the default CSV limit of 
# 131,072 characters (Python maximum limit) is too low for a real-world corpus.
_FIELD_SIZE_LIMIT_SET = False


def _ensure_field_size_limit() -> None:
    global _FIELD_SIZE_LIMIT_SET
    if not _FIELD_SIZE_LIMIT_SET:
        csv.field_size_limit(sys.maxsize)
        _FIELD_SIZE_LIMIT_SET = True


def find_document_csvs(root_dir: Path) -> list[Path]:
    """The per-document CSV files from the subtree, sorted by RELATIVE path.
    Sorting by relative path, rather than by filename, exactly reproduces the batch processing order,
    which iterates over PDFs via sorted(rglob("*.pdf")). 
    The requirement that "the row order follows the processing order" is therefore satisfied by construction.
    """
    return sorted(
        root_dir.rglob("metadata/*_final.csv"),
        key=lambda p: p.relative_to(root_dir).as_posix(),
    )


def _data_rows(csv_path: Path) -> list[list[str]]:
    """Data rows from a per-document CSV, excluding the header.
    The rows are reused as-is: CSV rows are concatenated, 
    metadata is not regenerated (no re-parsing of the METADATA JSON).
    """
    _ensure_field_size_limit()
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return []
    return rows[1:] if rows[0] == CSV_HEADER else rows


def aggregate_root_csv(out_root: Path, root_name: str) -> Path:
    """Concatenates the <doc>_final.csv files from the <out_root>/<root_name>/ subtree
    into <out_root>/<root_name>/<root_name>.csv.
    Complete reconstruction, never an append: an append would leave rows from deleted documents and duplicate rows on reruns,
    the exact bug that _rows_excluding_title avoids at the document level. 
    The files are rescanned and rewritten. The operation is therefore idempotent.
    out_root: Output root directory (data/output_files_preprocessing/)
    root_name: Root folder name (e.g. "afac")
    :return: Path to the written global CSV
    """
    root_dir = out_root / root_name
    output_path = root_dir / f"{root_name}.csv"

    csv_paths = [p for p in find_document_csvs(root_dir) if p != output_path]
    root_dir.mkdir(parents=True, exist_ok=True)

    n_rows = 0
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, quoting=CSV_QUOTING)
        writer.writerow(CSV_HEADER)
        for csv_path in csv_paths:
            rows = _data_rows(csv_path)
            writer.writerows(rows)
            n_rows += len(rows)

    _log.info(
        "Global CSV written: %s (%d document(s), %d row(s))",
        output_path, len(csv_paths), n_rows,
    )
    return output_path


def is_document_dir(directory: Path) -> bool:
    """True if directory is the folder of ONE document, not a corpus folder.
    A document folder contains metadata/<its name>_final.csv 
    (or, before the metadata step, <its name>.doctags). 
    Distinguishing it is essential: an output produced before batch F1 is flat, 
    its document folders are direct children of the root, 
    and treating them as corpora would create a spurious "global" CSV inside each one.
    """
    name = directory.name
    return (
        (directory / "metadata" / f"{name}_final.csv").exists()
        or (directory / f"{name}.doctags").exists()
    )


def discover_roots(out_root: Path) -> list[str]:
    """Root folders present in the output.
    A root is a direct child of the output directory that contains documents 
    without being a document itself (see is_document_dir).
    """
    if not out_root.is_dir():
        return []
    return sorted(
        d.name for d in out_root.iterdir()
        if d.is_dir() and not is_document_dir(d) and any(d.rglob("metadata/*_final.csv"))
    )


def aggregate_all_roots(out_root: Path) -> list[Path]:
    """Aggregates each discovered root, 
    two corpora produce two independent CSV files, with no name collision."""
    return [aggregate_root_csv(out_root, name) for name in discover_roots(out_root)]
