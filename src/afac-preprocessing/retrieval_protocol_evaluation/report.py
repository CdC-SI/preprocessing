"""Save evaluation results to CSV and generate metric plots."""
import logging
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

_log = logging.getLogger(__name__)

_METRICS = {
    "recall":    ("Recall@k",    "#1f77b4"),
    "precision": ("Precision@k", "#ff7f0e"),
    "ndcg":      ("nDCG@k",      "#2ca02c"),
    "mrr":       ("MRR@k",       "#d62728"),
}

_SEM_COLOR = "#4C72B0"
_RER_COLOR = "#DD8452"
_PLOT_SAVED = "Plot saved → %s"


# CSV
def save_results_csv(results: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(results).to_csv(output_path, index=False)
    _log.info("Results saved → %s", output_path)


# Per-doc line charts
def _plot_single_metric(
    df: pd.DataFrame, metric: str, top_ks: list[int], output_dir: Path
) -> None:
    label, color = _METRICS[metric]
    means = [df[f"{metric}@{k}"].mean() for k in top_ks]

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(top_ks, means, marker="o", linewidth=2, color=color)
    for x, y in zip(top_ks, means):
        ax.annotate(f"{y:.3f}", (x, y), textcoords="offset points", xytext=(4, 6), fontsize=8)
    ax.set_xlabel("k")
    ax.set_ylabel(f"{label} (mean over {len(df)} questions)")
    ax.set_title(label)
    ax.set_ylim(0, 1.05)
    ax.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()

    path = output_dir / f"{metric}_at_k.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    _log.info(_PLOT_SAVED, path)


def _plot_summary(df: pd.DataFrame, top_ks: list[int], output_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    for metric, (label, color) in _METRICS.items():
        means = [df[f"{metric}@{k}"].mean() for k in top_ks]
        ax.plot(top_ks, means, marker="o", linewidth=2, label=label, color=color)
    ax.set_xlabel("k")
    ax.set_ylabel(f"Mean score ({len(df)} questions)")
    ax.set_title("All Metrics@k — Summary")
    ax.set_ylim(0, 1.05)
    ax.legend(loc="lower right")
    ax.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()

    path = output_dir / "all_metrics_summary.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    _log.info(_PLOT_SAVED, path)


def _plot_per_question_heatmap(
    df: pd.DataFrame, top_ks: list[int], output_dir: Path
) -> None:
    """nDCG@k per question as a heatmap — highlights hard questions."""
    cols = [f"ndcg@{k}" for k in top_ks if f"ndcg@{k}" in df.columns]
    data = df[cols].set_index(df["question_idx"].astype(str))

    fig, ax = plt.subplots(
        figsize=(max(6, len(top_ks) * 1.2), max(4, len(df) * 0.4 + 1))
    )
    im = ax.imshow(data.values, aspect="auto", cmap="RdYlGn", vmin=0, vmax=1)
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels([c.replace("ndcg@", "k=") for c in cols])
    ax.set_yticks(range(len(df)))
    ax.set_yticklabels(data.index)
    ax.set_xlabel("k")
    ax.set_ylabel("Question index")
    ax.set_title("nDCG@k per question")
    plt.colorbar(im, ax=ax, label="nDCG@k")
    fig.tight_layout()

    path = output_dir / "ndcg_per_question_heatmap.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    _log.info(_PLOT_SAVED, path)


def plot_all_charts(results_csv: Path, output_dir: Path, top_ks: list[int]) -> None:
    """Generate all per-doc metric plots from a single evaluation CSV."""
    df = pd.read_csv(results_csv)
    output_dir.mkdir(parents=True, exist_ok=True)
    for metric in _METRICS:
        _plot_single_metric(df, metric, top_ks, output_dir)
    _plot_summary(df, top_ks, output_dir)
    _plot_per_question_heatmap(df, top_ks, output_dir)


# Global bar charts (across all documents)
def _barchart_single_metric(
    summary_df: pd.DataFrame,
    metric: str,
    k: int,
    output_dir: Path,
    include_reranker: bool,
) -> None:
    label, _ = _METRICS[metric]
    sem_col = f"sem_mean_{metric}@{k}"

    if sem_col not in summary_df.columns:
        _log.warning("Column %s not found in summary — skipping.", sem_col)
        return

    df = summary_df.sort_values(sem_col, ascending=True).copy()
    n = len(df)
    y = np.arange(n)

    rer_col = f"rer_mean_{metric}@{k}"
    has_rer = include_reranker and rer_col in df.columns

    bar_h = 0.35 if has_rer else 0.55
    fig_h = max(5, n * (0.8 if has_rer else 0.5) + 1)
    fig, ax = plt.subplots(figsize=(10, fig_h))

    if has_rer:
        bars_s = ax.barh(y + bar_h / 2, df[sem_col], bar_h,
                         label="Semantic", color=_SEM_COLOR, alpha=0.85)
        bars_r = ax.barh(y - bar_h / 2, df[rer_col], bar_h,
                         label="Semantic + Reranker", color=_RER_COLOR, alpha=0.85)
        ax.bar_label(bars_s, fmt="%.3f", padding=3, fontsize=7)
        ax.bar_label(bars_r, fmt="%.3f", padding=3, fontsize=7)
        ax.legend(loc="lower right")
    else:
        bars = ax.barh(y, df[sem_col], bar_h, color=_SEM_COLOR, alpha=0.85)
        ax.bar_label(bars, fmt="%.3f", padding=3, fontsize=7)

    ax.set_yticks(y)
    ax.set_yticklabels(df["doc_name"], fontsize=8)
    ax.set_xlabel(f"Mean {label}")
    ax.set_title(f"{label}@{k} per document — sorted")
    ax.set_xlim(0, 1.15)
    ax.axvline(df[sem_col].mean(), color=_SEM_COLOR, linestyle="--",
               linewidth=1, alpha=0.6, label=f"Sem. mean={df[sem_col].mean():.3f}")
    if has_rer:
        ax.axvline(df[rer_col].mean(), color=_RER_COLOR, linestyle="--",
                   linewidth=1, alpha=0.6, label=f"Rer. mean={df[rer_col].mean():.3f}")
    ax.grid(True, axis="x", linestyle="--", alpha=0.3)
    fig.tight_layout()

    path = output_dir / f"global_{metric}_at_{k}.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    _log.info(_PLOT_SAVED, path)


def _barchart_pipeline_comparison(
    summary_df: pd.DataFrame,
    k: int,
    output_dir: Path,
    include_reranker: bool,
) -> None:
    """One grouped bar per metric showing overall mean scores for both pipelines."""
    metrics = list(_METRICS.keys())
    labels = [_METRICS[m][0] for m in metrics]

    sem_means = []
    rer_means = []
    for metric in metrics:
        col_s = f"sem_mean_{metric}@{k}"
        col_r = f"rer_mean_{metric}@{k}"
        sem_means.append(summary_df[col_s].mean() if col_s in summary_df.columns else 0.0)
        rer_means.append(
            summary_df[col_r].mean()
            if (include_reranker and col_r in summary_df.columns) else None
        )

    x = np.arange(len(metrics))
    bar_w = 0.35
    fig, ax = plt.subplots(figsize=(8, 5))

    if include_reranker and any(v is not None for v in rer_means):
        rer_vals = [v if v is not None else 0.0 for v in rer_means]
        bars_s = ax.bar(x - bar_w / 2, sem_means, bar_w,
                        label="Semantic", color=_SEM_COLOR, alpha=0.85)
        bars_r = ax.bar(x + bar_w / 2, rer_vals, bar_w,
                        label="Semantic + Reranker", color=_RER_COLOR, alpha=0.85)
        ax.bar_label(bars_s, fmt="%.3f", padding=3, fontsize=9)
        ax.bar_label(bars_r, fmt="%.3f", padding=3, fontsize=9)
        ax.legend()
    else:
        bars = ax.bar(x, sem_means, bar_w * 1.5, color=_SEM_COLOR, alpha=0.85)
        ax.bar_label(bars, fmt="%.3f", padding=3, fontsize=9)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel(f"Mean score @ k={k} (all documents)")
    ax.set_title(f"Pipeline comparison — all metrics @ k={k}")
    ax.set_ylim(0, 1.15)
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    fig.tight_layout()

    path = output_dir / f"global_pipeline_comparison_at_{k}.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    _log.info(_PLOT_SAVED, path)


def plot_global_barcharts(
    summary_df: pd.DataFrame,
    output_dir: Path,
    k: int,
    include_reranker: bool = True,
) -> None:
    """Generate all global bar charts from the summary DataFrame."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for metric in _METRICS:
        _barchart_single_metric(summary_df, metric, k, output_dir, include_reranker)
    _barchart_pipeline_comparison(summary_df, k, output_dir, include_reranker)
