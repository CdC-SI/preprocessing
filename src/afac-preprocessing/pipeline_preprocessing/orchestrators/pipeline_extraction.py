"""Full pipeline — runs all modular steps in sequence (extraction + metadata).

Usage:
    uv run python pipeline_extraction.py --dotenv .env.test
    uv run python pipeline_extraction.py --dotenv .env.test --from-step 8
    uv run python pipeline_extraction.py --dotenv .env.test --from-step markdown-convert --to-step markdown-control
    uv run python pipeline_extraction.py --dotenv .env.test --skip-steps opencv-check,image-description
    uv run python pipeline_extraction.py --dotenv .env.test --only markdown-control
    uv run python pipeline_extraction.py --dotenv .env.test --input data/input_files/afac/Adhésion/MonDoc.pdf
    uv run python pipeline_extraction.py --dotenv .env.test --input data/input_files/afac/Adhésion  # dossier → traite tous les PDF trouvés récursivement
    uv run python pipeline_extraction.py --list-steps

Each step receives the resolved --dotenv path so DOC_NAME is picked up
consistently from the same .env file throughout the run.

--from-step / --to-step / --skip-steps / --only accept either a step number
or its name (see --list-steps). Numbers still work for compatibility, but
they're fragile across pipeline profiles — this pipeline has 13 steps,
fullpipeline_modular_v3.py has 11, so the same number can mean a different
step depending on which script you run. Prefer names in new scripts/docs.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


class Step(NamedTuple):
    name: str
    script: Path


_HERE = Path(__file__).resolve().parent        # orchestrators/
_PIPELINE_ROOT = _HERE.parent                  # pipeline_preprocessing/
_PROJECT_ROOT = _PIPELINE_ROOT.parent          # afac-preprocessing/
_SIMPLE  = _PIPELINE_ROOT / "simple_extraction"
_DESCIMG = _PIPELINE_ROOT / "description_image"
_META    = _PIPELINE_ROOT / "metadata"

STEPS: list[Step] = [
    Step("docling-extract",           _SIMPLE  / "docling_extract.py"),            # 01 — doctags via Docling
    Step("reorder-doctags",           _SIMPLE  / "reordered_doctags.py"),          # 02 — réordonnement des balises
    Step("opencv-check",              _SIMPLE  / "opencv_checker.py"),             # 03 — QA visuelle only, skipped par défaut
    Step("csv-to-jsonlines",          _SIMPLE  / "csv_to_jsonlines.py"),           # 04 — CSV → JSONL
    Step("load-jsonline-doctags",     _SIMPLE  / "load_jsonline_doctags.py"),      # 05 — chargement doctags enrichi
    Step("image-description",         _DESCIMG / "description_image_context.py"),  # 06 — descriptions images VLM (slow)
    Step("url-extraction",            _SIMPLE  / "url_extaction.py"),              # 07 — extraction URL
    Step("url-tuning",                _SIMPLE  / "url_tuning_vlm.py"),             # 08 — tuning URL via VLM
    Step("markdown-convert",          _SIMPLE  / "docling_markdown_converter.py"), # 09 — conversion markdown
    Step("markdown-control",          _SIMPLE  / "markdown_control_vlm.py"),       # 10 — contrôle markdown VLM
    Step("inject-image-descriptions", _SIMPLE  / "inject_image_descriptions.py"),  # 11 — injection descriptions images → _final.md
    Step("metadata-generation",       _META    / "metadata_generation.py"),        # 12 — metadata + embedding CSV
    Step("hyq-embedding",             _META    / "hyq_embedding_doc.py"),          # 13 — embeddings des questions hyq
]

_N = len(STEPS)
_NAME_TO_NUM = {step.name: i for i, step in enumerate(STEPS, start=1)}
_DOCLING_EXTRACT_STEP = "docling-extract"
_OPENCV_CHECK_STEP = "opencv-check"


def _resolve_step_ref(ref: str) -> int:
    """Resolve a step reference (1-based number or name, see --list-steps) to its number."""
    ref = ref.strip()
    if ref in _NAME_TO_NUM:
        return _NAME_TO_NUM[ref]
    if ref.isdigit() and 1 <= int(ref) <= _N:
        return int(ref)
    valid = ", ".join(f"{i}={step.name}" for i, step in enumerate(STEPS, start=1))
    raise SystemExit(f"[ERROR] Unknown step {ref!r}. Valid steps: {valid}")


def _print_steps_table() -> None:
    print(f"{'#':>2}  {'Name':<26} Script")
    for i, step in enumerate(STEPS, start=1):
        note = "  (skipped by default — see --with-opencv-check)" if step.name == _OPENCV_CHECK_STEP else ""
        print(f"{i:02d}  {step.name:<26} {step.script.name}{note}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Full modular pipeline ({_N} steps : extraction + metadata).",
        epilog=(
            "Examples:\n"
            "  uv run python pipeline_extraction.py --dotenv .env.test\n"
            "  uv run python pipeline_extraction.py --dotenv .env.test --from-step 8\n"
            "  uv run python pipeline_extraction.py --dotenv .env.test --from-step markdown-convert --to-step markdown-control\n"
            "  uv run python pipeline_extraction.py --dotenv .env.test --skip-steps opencv-check,image-description\n"
            "  uv run python pipeline_extraction.py --dotenv .env.test --only markdown-control\n"
            "  uv run python pipeline_extraction.py --list-steps\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--list-steps",
        action="store_true",
        help="Print the step number → name table and exit (no --dotenv required).",
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=Path(".env.test"),
        help="Path to the .env file passed to every step (default: .env.test).",
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        metavar="PDF_OR_DIR",
        help=(
            "Path to an input PDF, or a directory to batch-process every PDF found "
            "recursively inside it. Overrides DOC_NAME and DOC_PATH from the .env file "
            "(per file processed). Sets DOC_NAME to the file stem and DOC_PATH to the "
            "path relative to data/input_files/ (or the absolute path if outside that "
            "directory)."
        ),
    )
    parser.add_argument(
        "--from-step",
        type=str,
        default="1",
        metavar="N_OR_NAME",
        help=f"First step to run, inclusive — number (1–{_N}) or name, see --list-steps (default: 1).",
    )
    parser.add_argument(
        "--to-step",
        type=str,
        default=str(_N),
        metavar="N_OR_NAME",
        help=f"Last step to run, inclusive — number (1–{_N}) or name, see --list-steps (default: {_N}).",
    )
    parser.add_argument(
        "--skip-steps",
        type=str,
        default="",
        metavar="N_OR_NAME[,...]",
        help="Comma-separated step numbers or names to skip, e.g. --skip-steps opencv-check,image-description.",
    )
    parser.add_argument(
        "--only",
        type=str,
        default="",
        metavar="N_OR_NAME[,...]",
        help=(
            "Run only these steps (comma-separated numbers or names), ignoring "
            "--from-step/--to-step/--skip-steps. Always executed in pipeline order."
        ),
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help=(
            "Forwarded to step 01 (docling-extract) only. Measured on this "
            "corpus (born-digital PDFs, native text layer): identical extracted text, "
            "3-4x faster than the default forced EasyOCR pass."
        ),
    )
    parser.add_argument(
        "--with-opencv-check",
        action="store_true",
        help=(
            "Run the opencv-check step — visual QA only, produces no output "
            "consumed by later steps. Skipped by default."
        ),
    )
    return parser.parse_args()


def _run_step(step_num: int, step: Step, dotenv: Path, extra_args: list[str] | None = None) -> int:
    print(f"\n{'=' * 60}")
    print(f"  Step {step_num:02d}/{_N:02d} — {step.name} ({step.script.name})")
    print(f"{'=' * 60}")
    result = subprocess.run(
        [sys.executable, str(step.script), "--dotenv", str(dotenv), *(extra_args or [])],
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
    )
    if result.returncode != 0:
        print(f"\n[FAILED] {step.name} ({step.script.name}) exited with code {result.returncode}")
    return result.returncode


def _set_doc_env(input_path: Path) -> None:
    """Set DOC_NAME/DOC_PATH from an input PDF path, mirroring --input's single-file behavior."""
    os.environ["DOC_NAME"] = input_path.stem.strip()
    input_files_root = (_PROJECT_ROOT / "data" / "input_files").resolve()
    try:
        os.environ["DOC_PATH"] = str(input_path.relative_to(input_files_root))
    except ValueError:
        os.environ["DOC_PATH"] = str(input_path)


def _run_selected_steps(
    selected: list[tuple[int, Step]], dotenv: Path, no_ocr: bool
) -> int:
    """Run the selected steps once against whatever DOC_NAME/DOC_PATH is currently set.

    Returns the exit code of the first failing step, or 0 if all succeeded.
    """
    for i, step in selected:
        extra_args = ["--no-ocr"] if (step.name == _DOCLING_EXTRACT_STEP and no_ocr) else None
        code = _run_step(i, step, dotenv, extra_args)
        if code != 0:
            return code
    return 0


def _select_steps(args: argparse.Namespace) -> tuple[list[tuple[int, Step]], str]:
    """Resolve --from-step/--to-step/--skip-steps or --only into an ordered step selection.

    Returns (selected, display_string_for_logging).
    """
    if args.only:
        only_nums = {_resolve_step_ref(s) for s in args.only.split(",") if s.strip()}
        selected = [(i, STEPS[i - 1]) for i in sorted(only_nums)]
        names = ", ".join(STEPS[i - 1].name for i in sorted(only_nums))
        return selected, f"only: {names}"

    skip: set[int] = {_resolve_step_ref(s) for s in args.skip_steps.split(",") if s.strip()}
    if not args.with_opencv_check:
        skip.add(_NAME_TO_NUM[_OPENCV_CHECK_STEP])
    from_step = max(1, _resolve_step_ref(args.from_step))
    to_step = min(_N, _resolve_step_ref(args.to_step))

    selected = [
        (i, step)
        for i, step in enumerate(STEPS, start=1)
        if from_step <= i <= to_step and i not in skip
    ]
    skip_names = ", ".join(STEPS[i - 1].name for i in sorted(skip))
    display = f"steps {from_step}→{to_step}" + (f"  skip: {skip_names}" if skip else "")
    return selected, display


def _run_batch_mode(
    input_root: Path,
    selected: list[tuple[int, Step]],
    dotenv: Path,
    no_ocr: bool,
    selection_display: str,
) -> None:
    """--input pointing at a directory: run the selected steps for every PDF found under it."""
    pdfs = sorted(input_root.rglob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"[ERROR] No PDF files found under {input_root}")

    print(f"Found {len(pdfs)} PDF(s) under {input_root}/\n")
    for pdf in pdfs:
        print(f"  {pdf.relative_to(input_root)}")
    print(f"\nPipeline starting — {selection_display} — dotenv: {dotenv}")

    failed: list[tuple[Path, int]] = []
    for idx, pdf in enumerate(pdfs, start=1):
        print(f"\n{'#' * 60}")
        print(f"  PDF {idx}/{len(pdfs)}: {pdf.relative_to(input_root)}")
        print(f"{'#' * 60}")
        _set_doc_env(pdf)
        code = _run_selected_steps(selected, dotenv, no_ocr)
        if code != 0:
            failed.append((pdf, code))

    print(f"\n{'#' * 60}")
    if failed:
        print(f"  Batch finished with {len(failed)} failure(s):")
        for pdf, code in failed:
            print(f"    - {pdf.relative_to(input_root)}  (exit {code})")
        sys.exit(1)
    print(f"  Batch finished — all {len(pdfs)} PDF(s) processed successfully.")
    print(f"{'#' * 60}")


def _run_single_mode(
    input_arg: Path | None,
    selected: list[tuple[int, Step]],
    dotenv: Path,
    no_ocr: bool,
    selection_display: str,
) -> None:
    """--input pointing at a single file, or no --input → DOC_NAME comes from the .env file."""
    if input_arg:
        input_path = input_arg.resolve()
        if not input_path.exists():
            raise SystemExit(f"[ERROR] Input PDF not found: {input_path}")
        _set_doc_env(input_path)

    print(f"Pipeline starting — {selection_display} — dotenv: {dotenv}")

    code = _run_selected_steps(selected, dotenv, no_ocr)
    if code != 0:
        sys.exit(code)

    print(f"\n{'=' * 60}")
    print("  All steps completed successfully.")
    print(f"{'=' * 60}")


def main() -> None:
    args = parse_args()

    if args.list_steps:
        _print_steps_table()
        return

    dotenv = args.dotenv.resolve()
    if not dotenv.exists():
        raise SystemExit(f"[ERROR] .env file not found: {dotenv}")

    selected, selection_display = _select_steps(args)

    if not selected:
        raise SystemExit("[ERROR] No steps selected — check --from-step, --to-step, --skip-steps, --only.")

    if args.input and args.input.resolve().is_dir():
        _run_batch_mode(args.input.resolve(), selected, dotenv, args.no_ocr, selection_display)
        return

    _run_single_mode(args.input, selected, dotenv, args.no_ocr, selection_display)


if __name__ == "__main__":
    main()
