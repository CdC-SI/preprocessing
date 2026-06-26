"""Batch runner — finds every PDF under data/input_files/ and runs the full pipeline on each.

Usage:
    uv run python batch_pipeline_all_pdfs.py --dotenv .env.test
    uv run python batch_pipeline_all_pdfs.py --dotenv .env.test --input-dir data/input_files/afac/Adhésion
    uv run python batch_pipeline_all_pdfs.py --dotenv .env.test --from-step 8
    uv run python batch_pipeline_all_pdfs.py --dotenv .env.test --skip-steps 3,6
    uv run python batch_pipeline_all_pdfs.py --dotenv .env.test --dry-run

All flags except --dry-run and --input-dir are forwarded as-is to fullpipeline_modular_v2.py.
"""

import argparse
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PIPELINE_ROOT = _HERE.parent
_PROJECT_ROOT = _PIPELINE_ROOT.parent

_FULL_PIPELINE = _HERE / "fullpipeline_modular_v2.py"
_INPUT_ROOT = _PROJECT_ROOT / "data" / "input_files"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full pipeline on every PDF found under data/input_files/.",
        epilog=(
            "Examples:\n"
            "  uv run python batch_pipeline_all_pdfs.py --dotenv .env.test\n"
            "  uv run python batch_pipeline_all_pdfs.py --dotenv .env.test --dry-run\n"
            "  uv run python batch_pipeline_all_pdfs.py --dotenv .env.test --from-step 8\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=Path(".env.test"),
        help="Path to the .env file (default: .env.test).",
    )
    parser.add_argument(
        "--from-step",
        type=int,
        default=None,
        metavar="N",
        help="First step to run (forwarded to fullpipeline_modular_v2.py).",
    )
    parser.add_argument(
        "--to-step",
        type=int,
        default=None,
        metavar="N",
        help="Last step to run (forwarded to fullpipeline_modular_v2.py).",
    )
    parser.add_argument(
        "--skip-steps",
        type=str,
        default=None,
        metavar="N[,N...]",
        help="Comma-separated steps to skip (forwarded to fullpipeline_modular_v2.py).",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Scan only this directory for PDFs instead of data/input_files/. "
            "Accepts absolute or relative path (e.g. data/input_files/afac/Adhésion)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the PDFs that would be processed without running the pipeline.",
    )
    return parser.parse_args()


def build_forward_args(args: argparse.Namespace) -> list[str]:
    """Build the list of extra flags to forward to fullpipeline_modular_v2.py."""
    extra: list[str] = []
    if args.from_step is not None:
        extra += ["--from-step", str(args.from_step)]
    if args.to_step is not None:
        extra += ["--to-step", str(args.to_step)]
    if args.skip_steps is not None:
        extra += ["--skip-steps", args.skip_steps]
    return extra


def main() -> None:
    args = parse_args()

    dotenv = args.dotenv.resolve()
    if not dotenv.exists():
        raise SystemExit(f"[ERROR] .env file not found: {dotenv}")

    scan_root = args.input_dir.resolve() if args.input_dir else _INPUT_ROOT
    if not scan_root.exists():
        raise SystemExit(f"[ERROR] Input directory not found: {scan_root}")

    pdfs = sorted(scan_root.rglob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"[ERROR] No PDF files found under {_INPUT_ROOT}")

    print(f"Found {len(pdfs)} PDF(s) under {scan_root}/\n")
    for pdf in pdfs:
        print(f"  {pdf.relative_to(_INPUT_ROOT)}")

    if args.dry_run:
        print("\n[dry-run] No pipeline executed.")
        return

    forward = build_forward_args(args)
    failed: list[tuple[Path, int]] = []

    for idx, pdf in enumerate(pdfs, start=1):
        rel = pdf.relative_to(_INPUT_ROOT)
        print(f"\n{'#' * 60}")
        print(f"  PDF {idx}/{len(pdfs)}: {rel}")
        print(f"{'#' * 60}")

        cmd = [
            sys.executable, str(_FULL_PIPELINE),
            "--dotenv", str(dotenv),
            "--input", str(pdf),
            *forward,
        ]
        result = subprocess.run(cmd, stdout=sys.stdout, stderr=sys.stderr, text=True)

        if result.returncode != 0:
            print(f"\n[FAILED] {rel} — exit code {result.returncode}")
            failed.append((pdf, result.returncode))

    print(f"\n{'#' * 60}")
    if failed:
        print(f"  Batch finished with {len(failed)} failure(s):")
        for pdf, code in failed:
            print(f"    - {pdf.relative_to(_INPUT_ROOT)}  (exit {code})")
        sys.exit(1)
    else:
        print(f"  Batch finished — all {len(pdfs)} PDF(s) processed successfully.")
    print(f"{'#' * 60}")


if __name__ == "__main__":
    main()
