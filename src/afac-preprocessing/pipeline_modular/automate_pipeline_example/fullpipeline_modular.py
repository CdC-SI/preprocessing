"""Full pipeline — runs all modular steps in sequence (extraction + metadata).

Usage:
    uv run python pipeline_modular/automate_pipeline_example/fullpipeline_modular_v2.py --dotenv .env.test
    uv run python fullpipeline_modular_v2.py --dotenv .env.test

Each step receives the resolved --dotenv path so DOC_NAME is picked up
consistently from the same .env file throughout the run.

Steps 1-10 : extraction (doctags → markdown)
Step 11    : metadata generation (CONTENT + METADATA + EMBEDDING CSV)
Step 12    : hyq embeddings (one CSV per hypothetical question)
"""

import argparse
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent        # automate_pipeline_example/
_PIPELINE_ROOT = _HERE.parent                  # pipeline_modular/
_SIMPLE  = _PIPELINE_ROOT / "simple_extraction"
_DESCIMG = _PIPELINE_ROOT / "description_image"
_META    = _PIPELINE_ROOT / "metadata"

STEPS: list[Path] = [
    _SIMPLE  / "pipeline_multietape_modular.py",        # 01 – doctags via Docling
    _SIMPLE  / "reordered_doctags_modular.py",          # 02 – réordonnement des balises
    _SIMPLE  / "opencv_checker_modular.py",             # 03 – contrôle qualité images
    _SIMPLE  / "csv_to_jsonlines_modular.py",           # 04 – CSV → JSONL
    _SIMPLE  / "load_jsonline_doctags_modular.py",      # 05 – chargement doctags enrichi
    _DESCIMG / "description_image_context_modular.py",  # 06 – descriptions images VLM
    _SIMPLE  / "url_extaction_modular.py",              # 07 – extraction URL (typo intentionnel)
    _SIMPLE  / "url_tuning_vlm_modular.py",             # 08 – tuning URL via VLM
    _SIMPLE  / "docling_markdown_converter_modular.py", # 09 – conversion markdown
    _SIMPLE  / "markdown_control_vlm_modular.py",       # 10 – contrôle markdown VLM
    _META    / "metadata_generation_modular.py",        # 11 – metadata + embedding CSV
    _META    / "hyq_embedding_doc_modular.py",          # 12 – embeddings des questions hyq
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Full modular pipeline (steps 1-11 : extraction + metadata).",
        epilog="Example:\n  uv run python fullpipeline_modular_v2.py --dotenv .env.test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=Path(".env.test"),
        help="Path to the .env file passed to every step (default: .env.test).",
    )
    return parser.parse_args()


def _run_step(step: int, script: Path, dotenv: Path) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Step {step:02d}/{len(STEPS):02d} — {script.name}")
    print(f"{'=' * 60}")
    result = subprocess.run(
        [sys.executable, str(script), "--dotenv", str(dotenv)],
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

    print(f"Pipeline starting — dotenv: {dotenv}")

    for i, script in enumerate(STEPS, start=1):
        _run_step(i, script, dotenv)

    print(f"\n{'=' * 60}")
    print("  All steps completed successfully.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
