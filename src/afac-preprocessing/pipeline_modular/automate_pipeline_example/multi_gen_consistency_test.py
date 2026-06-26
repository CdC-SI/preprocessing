"""Multi-generation consistency test runner.

Runs the full modular pipeline N times for the same document, setting GEN_ID=1..N
each time. After each successful run, output files are snapshotted into:

    data/output_files/<DOC_NAME>/gen_runs/gen_<N>/

so you can diff outputs across generations and check VLM consistency.

Usage:
    uv run python pipeline_modular/automate_pipeline_example/multi_gen_consistency_test.py \\
        --dotenv .env.test \\
        --input "data/input_files/Adhésion/Ahésion traitement.pdf" \\
        --runs 5

    # Run only specific steps for each generation (faster targeted testing):
    uv run python ... --runs 3 --from-step 10 --to-step 10

    # Don't snapshot outputs after each run:
    uv run python ... --runs 5 --no-snapshot

    # Keep going even if one generation fails:
    uv run python ... --runs 5 --continue-on-error
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent        # automate_pipeline_example/
_PIPELINE_ROOT = _HERE.parent                  # pipeline_modular/
_PROJECT_ROOT = _PIPELINE_ROOT.parent          # afac-preprocessing/

PIPELINE_SCRIPT = _HERE / "fullpipeline_modular_v2.py"
_N_STEPS = 13  # total steps in fullpipeline_modular_v2.py


def update_gen_id_in_env(env_file: Path, gen_id: int) -> None:
    """Writes GEN_ID=<gen_id> into the .env file (creates or replaces)."""
    content = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    replacement = f'GEN_ID="{gen_id}"'
    if re.search(r"^GEN_ID=.*$", content, flags=re.MULTILINE):
        content = re.sub(r"^GEN_ID=.*$", replacement, content, flags=re.MULTILINE)
    else:
        if content and not content.endswith("\n"):
            content += "\n"
        content += replacement + "\n"
    env_file.write_text(content, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full modular pipeline N times (GEN_ID=1..N) and snapshot outputs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run python multi_gen_consistency_test.py --dotenv .env.test \\\n"
            '      --input "data/input_files/Adhésion/Ahésion traitement.pdf" --runs 5\n'
            "  uv run python multi_gen_consistency_test.py --dotenv .env.test \\\n"
            '      --input "data/input_files/..." --runs 3 --from-step 10 --to-step 10\n'
        ),
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=Path(".env.test"),
        help="Path to the .env file (default: .env.test).",
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        metavar="PDF",
        help="Path to the input PDF (overrides DOC_NAME/DOC_PATH in .env).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=5,
        metavar="N",
        help="Number of pipeline runs to execute (GEN_ID goes from 1 to N, default: 5).",
    )
    parser.add_argument(
        "--from-step",
        type=int,
        default=1,
        metavar="N",
        help=f"First step to run per generation (1–{_N_STEPS}, default: 1).",
    )
    parser.add_argument(
        "--to-step",
        type=int,
        default=_N_STEPS,
        metavar="N",
        help=f"Last step to run per generation (1–{_N_STEPS}, default: {_N_STEPS}).",
    )
    parser.add_argument(
        "--skip-steps",
        type=str,
        default="",
        metavar="N[,N...]",
        help="Comma-separated step numbers to skip per generation.",
    )
    parser.add_argument(
        "--no-snapshot",
        action="store_true",
        help="Skip snapshotting output files after each run (outputs will be overwritten).",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Keep running subsequent generations even if one fails.",
    )
    return parser.parse_args()


def resolve_doc_name(args: argparse.Namespace, dotenv: Path) -> str:
    """Infer DOC_NAME from --input or from the .env file."""
    if args.input:
        return args.input.stem
    # Fall back to DOC_NAME in the .env file
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            m = re.match(r'^DOC_NAME=["\']?([^"\']+)["\']?', line.strip())
            if m:
                return m.group(1).strip()
    return ""


def snapshot_outputs(output_dir: Path, gen_id: int) -> Path:
    """Copy current output_dir to <parent>/<DOC_NAME>_gen<N>/."""
    snapshot_dir = output_dir.parent / f"{output_dir.name}_gen{gen_id}"
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    shutil.copytree(output_dir, snapshot_dir)
    return snapshot_dir


def build_pipeline_cmd(args: argparse.Namespace) -> list[str]:
    """Build the subprocess command for fullpipeline_modular_v2.py."""
    cmd = [sys.executable, str(PIPELINE_SCRIPT), "--dotenv", str(args.dotenv)]
    if args.input:
        cmd += ["--input", str(args.input)]
    if args.from_step != 1:
        cmd += ["--from-step", str(args.from_step)]
    if args.to_step != _N_STEPS:
        cmd += ["--to-step", str(args.to_step)]
    if args.skip_steps:
        cmd += ["--skip-steps", args.skip_steps]
    return cmd


def print_banner(gen_id: int, total: int) -> None:
    print(f"\n{'#' * 60}")
    print(f"  GENERATION {gen_id}/{total}  (GEN_ID={gen_id})")
    print(f"{'#' * 60}")


def print_summary(results: list[tuple[int, bool]], snapshot_dirs: dict[int, Path]) -> None:
    print(f"\n{'=' * 60}")
    print("  MULTI-GENERATION SUMMARY")
    print(f"{'=' * 60}")
    for gen_id, success in results:
        status = "OK " if success else "FAIL"
        snap = f"  → {snapshot_dirs[gen_id]}" if gen_id in snapshot_dirs else ""
        print(f"  gen {gen_id:02d}  [{status}]{snap}")
    failed = [g for g, ok in results if not ok]
    if failed:
        print(f"\n  Failed generations: {failed}")
    else:
        print(f"\n  All {len(results)} generation(s) completed successfully.")
    print(f"{'=' * 60}")


def main() -> None:
    args = parse_args()

    dotenv = args.dotenv.resolve()
    if not dotenv.exists():
        raise SystemExit(f"[ERROR] .env file not found: {dotenv}")

    if args.input and not args.input.resolve().exists():
        raise SystemExit(f"[ERROR] Input PDF not found: {args.input.resolve()}")

    if args.runs < 1:
        raise SystemExit("[ERROR] --runs must be >= 1.")

    doc_name = resolve_doc_name(args, dotenv)
    if not doc_name:
        raise SystemExit(
            "[ERROR] Could not determine DOC_NAME. "
            "Use --input <pdf> or set DOC_NAME in the .env file."
        )

    output_dir = (_PROJECT_ROOT / "data" / "output_files" / doc_name).resolve()
    cmd = build_pipeline_cmd(args)

    step_range = (
        f"steps {args.from_step}→{args.to_step}"
        + (f" (skip {args.skip_steps})" if args.skip_steps else "")
    )
    print(f"\nMulti-gen test — document: {doc_name!r}")
    print(f"Generations : 1..{args.runs}  |  {step_range}")
    print(f"Dotenv      : {dotenv}")
    print(f"Output dir  : {output_dir}")
    print(f"Snapshots   : {'disabled (--no-snapshot)' if args.no_snapshot else str(output_dir / 'gen_runs')}")

    results: list[tuple[int, bool]] = []
    snapshot_dirs: dict[int, Path] = {}

    for gen_id in range(1, args.runs + 1):
        print_banner(gen_id, args.runs)

        update_gen_id_in_env(dotenv, gen_id)

        env = os.environ.copy()
        env["GEN_ID"] = str(gen_id)

        result = subprocess.run(
            cmd,
            stdout=sys.stdout,
            stderr=sys.stderr,
            text=True,
            env=env,
        )
        success = result.returncode == 0
        results.append((gen_id, success))

        if success and not args.no_snapshot:
            if output_dir.exists():
                snap = snapshot_outputs(output_dir, gen_id)
                snapshot_dirs[gen_id] = snap
                print(f"\n[SNAPSHOT] Generation {gen_id} saved to: {snap}")
            else:
                print(f"\n[WARN] Output directory not found, skipping snapshot: {output_dir}")

        if not success:
            print(f"\n[FAILED] Generation {gen_id} exited with code {result.returncode}.")
            if not args.continue_on_error:
                print("Stopping. Use --continue-on-error to keep running on failure.")
                break

        print(f"\nGeneration {gen_id}/{args.runs} done.")

    print_summary(results, snapshot_dirs)

    if any(not ok for _, ok in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
