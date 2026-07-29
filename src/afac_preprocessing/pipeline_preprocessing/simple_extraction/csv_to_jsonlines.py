"""
csv_to_jsonlines_modulaire.py — Convert CSV tables (Docling) into JSONL files.

For each CSV found in the input folder, produces a .jsonl file
containing one JSON line per row of the table.

Usage:
    uv run python csv_to_jsonlines_modulaire.py --input-dir data/output_files_preprocessing/MonDoc/tables
    uv run python csv_to_jsonlines_modulaire.py --dotenv .env.test
"""
import argparse
import logging
import sys
from pathlib import Path

import jsonlines
import pandas as pd

from ...utils.paths import project_root, resolve_doc_name

_log = logging.getLogger(__name__)


# CSV → JSONL utilities
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


# CLI
def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.

    :return: Parsed command-line arguments.
    :rtype: Namespace
    """
    parser = argparse.ArgumentParser(
        description=(
            "Convert CSV tables extracted by Docling into JSONL files. "
            "One .jsonl per CSV, one JSON line per row of the table."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run python csv_to_jsonlines_modulaire.py "
            "--input-dir data/output_files_preprocessing/MonDoc/tables\n"
            "  uv run python csv_to_jsonlines_modulaire.py "
            "--input-dir data/output_files_preprocessing/MonDoc/tables "
            "--output-dir data/output_files_preprocessing/MonDoc/jsonlines\n"
            "  uv run python csv_to_jsonlines_modulaire.py --dotenv .env.test\n"
        ),
    )
    parser.add_argument(
        "--input-dir", "-i",
        type=Path,
        default=None,
        help=(
            "Folder containing the CSV files to convert. "
            "If omitted, resolves data/output_files_preprocessing/<DOC_NAME>/tables/ from the environment."
        ),
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=None,
        help=(
            "Output folder for the JSONL files. "
            "Default: same folder as --input-dir."
        ),
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FILE",
        help="A .env file to load in order to resolve DOC_NAME (e.g. .env.test). Ignored if --input-dir is provided.",
    )
    return parser.parse_args()


# Path resolution
def resolve_input_dir(args: argparse.Namespace) -> Path:
    """
    Resolve the input directory containing the CSV files.

    :param args: Parsed command-line arguments.
    :type args: argparse.Namespace
    :return: Resolved absolute path to the input directory.
    :rtype: Path
    """
    if args.input_dir:
        return args.input_dir.resolve()
    doc_name = resolve_doc_name(args, primary_flag="--input-dir")
    return project_root() / "data" / "output_files_preprocessing" / doc_name / "tables"


def resolve_output_dir(args: argparse.Namespace, input_dir: Path) -> Path:
    """
    Resolve the output directory for the JSONL files.

    :param args: Parsed command-line arguments.
    :type args: argparse.Namespace
    :param input_dir: Resolved input directory, used as the default output location.
    :type input_dir: Path
    :return: Resolved absolute path to the output directory.
    :rtype: Path
    """
    if args.output_dir:
        return args.output_dir.resolve()
    return input_dir  # default: JSONL next to the CSVs


# Entry point
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()

    input_dir = resolve_input_dir(args)
    if not input_dir.exists():
        raise SystemExit(f"Error: directory not found — {input_dir}")

    output_dir = resolve_output_dir(args, input_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_files = sorted(input_dir.glob("*.csv"))
    if not csv_files:
        raise SystemExit(f"No CSV files found in: {input_dir}")

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
            _log.exception("%s → error: %s", csv_path.name, e)
            n_err += 1

    _log.info(
        "Done — %d converted, %d skipped (empty), %d error(s).",
        n_ok, n_skip, n_err,
    )
    sys.exit(1 if n_err > 0 else 0)


if __name__ == "__main__":
    main()
