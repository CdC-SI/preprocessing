"""Save evaluation results to CSV and generate recall@k plots."""
import logging
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

_log = logging.getLogger(__name__)


def save_results_csv(results: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(results).to_csv(output_path, index=False)
    _log.info("Results saved → %s", output_path)


def plot_recall_curves(results_csv: Path, output_dir: Path, top_ks: list[int]) -> None:
    """Plot mean recall@k curve across all evaluated questions."""
    df = pd.read_csv(results_csv)
    output_dir.mkdir(parents=True, exist_ok=True)

    recall_means = [df[f"recall@{k}"].mean() for k in top_ks]

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(top_ks, recall_means, marker="o", linewidth=2)
    for x, y in zip(top_ks, recall_means):
        ax.annotate(f"{y:.2f}", (x, y), textcoords="offset points", xytext=(4, 6), fontsize=8)
    ax.set_xlabel("k")
    ax.set_ylabel("Recall@k (mean)")
    ax.set_title(f"Recall@k — {len(df)} question(s)")
    ax.set_ylim(0, 1.05)
    ax.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()

    plot_path = output_dir / "recall_at_k.png"
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)
    _log.info("Plot saved → %s", plot_path)
