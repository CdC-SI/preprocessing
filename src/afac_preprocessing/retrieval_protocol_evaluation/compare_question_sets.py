"""
Compare several QUESTION SETS evaluated against the same document corpus.

Where ``compare_baseline_report.py`` holds the questions fixed and varies the
document representation (baseline vs pipeline markdown), this script does the
opposite: the corpus is fixed and the question set is the variable. That is what
isolates "are slot-based questions better retrieval queries than the HyQ the
pipeline generates?" from every other difference.

Each run is a folder produced by ``evaluate.py --run-label <name>``:

    <output-dir>/<run>/evaluation_results.csv

Outputs:
  <output-dir>/question_set_comparison.csv        mean of the 4 metrics @ each k, per run
  <output-dir>/question_set_comparison_at_<k>.png grouped bars, 4 metrics × runs
  <output-dir>/question_set_by_k.png              trend across k, one subplot per metric
  <output-dir>/slot_breakdown.csv                 mean nDCG@k per slot value (if slots present)

⚠ A comparison is only meaningful if every run was scored against the SAME
corpus: same --stage5, same embedding model. The script checks that the runs
cover the same documents and warns when they do not.

Usage:
    python -m ...compare_question_sets --runs pipeline_hyq slots_v1 slots_v2 slots_v3
    python -m ...compare_question_sets --runs pipeline_hyq slots_v1 --canonical-k 5
"""
import argparse
import logging
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import CANONICAL_K, DEFAULT_OUTPUT_DIR, TOP_KS

_log = logging.getLogger(__name__)

METRICS = ["recall", "precision", "ndcg", "mrr"]
METRIC_LABELS = {"recall": "Recall", "precision": "Precision", "ndcg": "nDCG", "mrr": "MRR"}
SLOT_PREFIX = "slot_"
_RESULTS_NAME = "evaluation_results.csv"


def load_runs(output_dir: Path, run_names: list[str]) -> dict[str, pd.DataFrame]:
    """{run label: results dataframe} — a missing or empty run is fatal, not skipped."""
    runs: dict[str, pd.DataFrame] = {}
    for name in run_names:
        path = output_dir / name / _RESULTS_NAME
        if not path.exists():
            raise FileNotFoundError(
                f"Run '{name}': {path} not found — evaluate it first with "
                f"`evaluate.py … --run-label {name}`."
            )
        df = pd.read_csv(path)
        if df.empty:
            raise ValueError(f"Run '{name}': {path} contains no question.")
        runs[name] = df
    return runs


def check_same_corpus(runs: dict[str, pd.DataFrame]) -> None:
    """Warn if the runs do not cover the same documents.

    Different document coverage makes the means incomparable: a question set
    targeting only the 5 easiest documents will beat one spread over 40, with no
    difference in question quality whatsoever.
    """
    coverage = {name: set(df["source_doc"].unique()) for name, df in runs.items()}
    reference_name, reference = next(iter(coverage.items()))
    for name, docs in coverage.items():
        if docs == reference:
            continue
        only_here = sorted(docs - reference)[:5]
        only_there = sorted(reference - docs)[:5]
        _log.warning(
            "Run '%s' covers %d document(s) vs %d for '%s' — means are not "
            "directly comparable. Only in '%s': %s. Missing from '%s': %s.",
            name, len(docs), len(reference), reference_name,
            name, only_here or "—", name, only_there or "—",
        )


def available_ks(runs: dict[str, pd.DataFrame], top_ks: list[int]) -> list[int]:
    """The k values actually present in every run."""
    ks = [k for k in top_ks if all(f"ndcg@{k}" in df.columns for df in runs.values())]
    if not ks:
        raise ValueError("The runs share no common k value.")
    return ks


def build_comparison(runs: dict[str, pd.DataFrame], top_ks: list[int]) -> pd.DataFrame:
    """One row per run: n_questions, n_docs, and the mean of each metric@k."""
    rows = []
    for name, df in runs.items():
        row: dict = {
            "run": name,
            "n_questions": len(df),
            "n_docs": df["source_doc"].nunique(),
        }
        for metric in METRICS:
            for k in top_ks:
                column = f"{metric}@{k}"
                if column in df.columns:
                    row[f"mean_{column}"] = df[column].mean()
        rows.append(row)
    return pd.DataFrame(rows)


def build_slot_breakdown(runs: dict[str, pd.DataFrame], k: int) -> pd.DataFrame:
    """Mean nDCG@k per (run, slot, slot value).

    The payoff of carrying slots as data: it answers "which slot dimension makes
    a question hard to retrieve?" rather than just "is v2 better than v1?".
    """
    rows = []
    metric = f"ndcg@{k}"
    for name, df in runs.items():
        if metric not in df.columns:
            continue
        for column in [c for c in df.columns if c.startswith(SLOT_PREFIX)]:
            subset = df[[column, metric]].dropna(subset=[column])
            for value, group in subset.groupby(column):
                rows.append({
                    "run": name,
                    "slot": column.removeprefix(SLOT_PREFIX),
                    "value": value,
                    "n_questions": len(group),
                    f"mean_{metric}": group[metric].mean(),
                })
    return pd.DataFrame(rows)


def plot_grouped_bars(comparison: pd.DataFrame, k: int, output_dir: Path) -> Path:
    """4 metric groups × one bar per run, at the canonical k."""
    runs = comparison["run"].tolist()
    x = np.arange(len(METRICS))
    width = min(0.8 / len(runs), 0.35)
    colors = plt.get_cmap("tab10")(np.linspace(0, 0.9, len(runs)))

    fig, ax = plt.subplots(figsize=(max(8, len(runs) * 2), 5))
    for i, run in enumerate(runs):
        offset = (i - (len(runs) - 1) / 2) * width
        values = [
            comparison.loc[comparison["run"] == run, f"mean_{m}@{k}"].to_numpy()[0]
            if f"mean_{m}@{k}" in comparison.columns else 0.0
            for m in METRICS
        ]
        bars = ax.bar(x + offset, values, width, label=run, color=colors[i], alpha=0.9)
        ax.bar_label(bars, fmt="%.3f", padding=2, fontsize=7)

    ax.set_xticks(x)
    ax.set_xticklabels([METRIC_LABELS[m] for m in METRICS])
    ax.set_ylabel(f"Mean score @ k={k}")
    ax.set_title(f"Question sets compared — all metrics @ k={k}")
    ax.set_ylim(0, 1.15)
    ax.legend(title="Question set")
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    fig.tight_layout()

    path = output_dir / f"question_set_comparison_at_{k}.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    _log.info("Plot saved → %s", path)
    return path


def plot_by_k(comparison: pd.DataFrame, top_ks: list[int], output_dir: Path) -> Path:
    """One subplot per metric: every run's trend across k."""
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    colors = plt.get_cmap("tab10")(np.linspace(0, 0.9, len(comparison)))

    for ax, metric in zip(axes.flat, METRICS, strict=True):
        for i, run in enumerate(comparison["run"]):
            values = [
                comparison.loc[comparison["run"] == run, f"mean_{metric}@{k}"].to_numpy()[0]
                if f"mean_{metric}@{k}" in comparison.columns else np.nan
                for k in top_ks
            ]
            ax.plot(top_ks, values, marker="o", linewidth=2, label=run, color=colors[i])
        ax.set_title(METRIC_LABELS[metric])
        ax.set_xlabel("k")
        ax.set_ylabel("Mean score")
        ax.set_ylim(0, 1.05)
        ax.grid(True, linestyle="--", alpha=0.4)
        ax.legend(fontsize=8)

    fig.suptitle("Question sets compared — metrics across k")
    fig.tight_layout()

    path = output_dir / "question_set_by_k.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    _log.info("Plot saved → %s", path)
    return path


def log_verdict(comparison: pd.DataFrame, k: int) -> None:
    """Print the deltas against the first run, treated as the reference arm."""
    column = f"mean_ndcg@{k}"
    if column not in comparison.columns or len(comparison) < 2:
        return
    reference = comparison.iloc[0]
    _log.info("Reference arm: '%s' (nDCG@%d = %.4f)", reference["run"], k, reference[column])
    for _, row in comparison.iloc[1:].iterrows():
        delta = row[column] - reference[column]
        _log.info(
            "  %-20s nDCG@%d = %.4f  (%+.4f, %+.1f%%)",
            row["run"], k, row[column], delta,
            100 * delta / reference[column] if reference[column] else float("nan"),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare question sets evaluated against the same corpus.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--runs",
        nargs="+",
        required=True,
        help="Run labels to compare. The first is the reference arm "
             "(e.g. pipeline_hyq slots_v1 slots_v2).",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--top-ks",
        default=",".join(map(str, TOP_KS)),
        help=f"Comma-separated k values. Default: {','.join(map(str, TOP_KS))}",
    )
    parser.add_argument("--canonical-k", type=int, default=CANONICAL_K)
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        runs = load_runs(args.output_dir, args.runs)
        top_ks = available_ks(runs, [int(k) for k in args.top_ks.split(",")])
    except (FileNotFoundError, ValueError) as exc:
        _log.error("%s", exc)
        sys.exit(1)

    check_same_corpus(runs)

    k = args.canonical_k if args.canonical_k in top_ks else top_ks[-1]
    if k != args.canonical_k:
        _log.warning("k=%d absent from the runs — falling back to k=%d.", args.canonical_k, k)

    comparison = build_comparison(runs, top_ks)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = args.output_dir / "question_set_comparison.csv"
    comparison.to_csv(comparison_path, index=False)
    _log.info("Comparison saved → %s", comparison_path)

    plot_grouped_bars(comparison, k, args.output_dir)
    plot_by_k(comparison, top_ks, args.output_dir)

    slots = build_slot_breakdown(runs, k)
    if slots.empty:
        _log.info("No slot_* column in the runs — slot breakdown skipped.")
    else:
        slot_path = args.output_dir / "slot_breakdown.csv"
        slots.sort_values(["run", "slot", f"mean_ndcg@{k}"]).to_csv(slot_path, index=False)
        _log.info("Slot breakdown saved → %s", slot_path)

    log_verdict(comparison, k)
    _log.info("Done. %d run(s) compared.", len(runs))


if __name__ == "__main__":
    main()
