"""Full pipeline — runs all modular steps in sequence (extraction + metadata).

Usage:
    uv run python fullpipeline_modular_v2.py --dotenv .env.test
    uv run python fullpipeline_modular_v2.py --dotenv .env.test --from-step 8
    uv run python fullpipeline_modular_v2.py --dotenv .env.test --from-step 11 --to-step 12
    uv run python fullpipeline_modular_v2.py --dotenv .env.test --skip-steps 3,6

Each step receives the resolved --dotenv path so DOC_NAME is picked up
consistently from the same .env file throughout the run.

Steps:
  01  pipeline_multietape_modular.py           # doctags via Docling
  02  reordered_doctags_modular.py             # réordonnement des balises
  03  opencv_checker_modular.py                # contrôle qualité images (validation only)
  04  csv_to_jsonlines_modular.py              # CSV → JSONL
  05  load_jsonline_doctags_modular.py         # chargement doctags enrichi
  06  description_image_context_modular.py     # descriptions images VLM  (slow)
  07  url_extaction_modular.py                 # extraction URL
  08  url_tuning_vlm_modular.py                # tuning URL via VLM
  09  docling_markdown_converter_modular.py    # conversion markdown
  10  markdown_control_vlm_modular.py          # contrôle markdown VLM
  11  inject_image_descriptions_modular.py     # injection descriptions images → _final.md
  12  metadata_generation_modular.py           # metadata + embedding CSV
  13  hyq_embedding_doc_modular.py             # embeddings des questions hyq
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent        # automate_pipeline_example/
_PIPELINE_ROOT = _HERE.parent                  # pipeline_modular/
_PROJECT_ROOT = _PIPELINE_ROOT.parent          # afac-preprocessing/
_SIMPLE  = _PIPELINE_ROOT / "simple_extraction"
_DESCIMG = _PIPELINE_ROOT / "description_image"
_META    = _PIPELINE_ROOT / "metadata"

STEPS: list[Path] = [
    _SIMPLE  / "pipeline_multietape_modular.py",            # 01
    _SIMPLE  / "reordered_doctags_modular.py",              # 02
    _SIMPLE  / "opencv_checker_modular.py",                 # 03
    _SIMPLE  / "csv_to_jsonlines_modular.py",               # 04
    _SIMPLE  / "load_jsonline_doctags_modular.py",          # 05
    _DESCIMG / "description_image_context_modular.py",      # 06
    _SIMPLE  / "url_extaction_modular.py",                  # 07
    _SIMPLE  / "url_tuning_vlm_modular.py",                 # 08
    _SIMPLE  / "docling_markdown_converter_modular.py",     # 09
    _SIMPLE  / "markdown_control_vlm_modular.py",           # 10
    _SIMPLE  / "inject_image_descriptions_modular.py",      # 11
    _META    / "metadata_generation_modular.py",            # 12
    _META    / "hyq_embedding_doc_modular.py",              # 13
]

_N = len(STEPS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Full modular pipeline ({_N} steps : extraction + metadata).",
        epilog=(
            "Examples:\n"
            "  uv run python fullpipeline_modular_v2.py --dotenv .env.test\n"
            "  uv run python fullpipeline_modular_v2.py --dotenv .env.test --from-step 8\n"
            "  uv run python fullpipeline_modular_v2.py --dotenv .env.test --from-step 11 --to-step 12\n"
            "  uv run python fullpipeline_modular_v2.py --dotenv .env.test --skip-steps 3,6\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        metavar="PDF",
        help=(
            "Path to the input PDF. Overrides DOC_NAME and DOC_PATH from the .env file. "
            "Sets DOC_NAME to the file stem and DOC_PATH to the path relative to "
            "data/input_files/ (or the absolute path if outside that directory)."
        ),
    )
    parser.add_argument(
        "--from-step",
        type=int,
        default=1,
        metavar="N",
        help=f"First step to run, inclusive (1–{_N}, default: 1).",
    )
    parser.add_argument(
        "--to-step",
        type=int,
        default=_N,
        metavar="N",
        help=f"Last step to run, inclusive (1–{_N}, default: {_N}).",
    )
    parser.add_argument(
        "--skip-steps",
        type=str,
        default="",
        metavar="N[,N...]",
        help="Comma-separated step numbers to skip, e.g. --skip-steps 3,6.",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help=(
            "Forwarded to step 01 (pipeline_multietape_modular.py) only. Measured on this "
            "corpus (born-digital PDFs, native text layer): identical extracted text, "
            "3-4x faster than the default forced EasyOCR pass."
        ),
    )
    return parser.parse_args()


def _run_step(step: int, script: Path, dotenv: Path, extra_args: list[str] | None = None) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Step {step:02d}/{_N:02d} — {script.name}")
    print(f"{'=' * 60}")
    result = subprocess.run(
        [sys.executable, str(script), "--dotenv", str(dotenv), *(extra_args or [])],
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
    )
    if result.returncode != 0:
        print(f"\n[FAILED] {script.name} exited with code {result.returncode}")
        sys.exit(result.returncode)


def main() -> None:
    args = parse_args()

    dotenv = args.dotenv.resolve()
    if not dotenv.exists():
        raise SystemExit(f"[ERROR] .env file not found: {dotenv}")

    if args.input:
        input_path = args.input.resolve()
        if not input_path.exists():
            raise SystemExit(f"[ERROR] Input PDF not found: {input_path}")
        os.environ["DOC_NAME"] = input_path.stem.strip()
        input_files_root = (_PROJECT_ROOT / "data" / "input_files").resolve()
        try:
            os.environ["DOC_PATH"] = str(input_path.relative_to(input_files_root))
        except ValueError:
            os.environ["DOC_PATH"] = str(input_path)

    skip: set[int] = {int(s) for s in args.skip_steps.split(",") if s.strip()}
    from_step = max(1, args.from_step)
    to_step = min(_N, args.to_step)

    selected = [
        (i, script)
        for i, script in enumerate(STEPS, start=1)
        if from_step <= i <= to_step and i not in skip
    ]

    if not selected:
        raise SystemExit("[ERROR] No steps selected — check --from-step, --to-step, --skip-steps.")

    skip_display = f"  skip: {sorted(skip)}" if skip else ""
    print(f"Pipeline starting — steps {from_step}→{to_step}{skip_display} — dotenv: {dotenv}")

    for i, script in selected:
        extra_args = ["--no-ocr"] if (i == 1 and args.no_ocr) else None
        _run_step(i, script, dotenv, extra_args)

    print(f"\n{'=' * 60}")
    print("  All steps completed successfully.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
