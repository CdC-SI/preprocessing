"""
Compares baseline_results.csv (raw docling, no preprocessing) against the results of the
preprocessing pipeline (data/pipeline_evaluation/global_summary.csv, sem_mean_*
columns — semantic pipeline, no reranker) and generates a markdown report.

Both result sets evaluate the same 20-document corpus with the
same HyQ questions (see single_docling_baseline.py) — only the
document representation differs (raw docling markdown vs preprocessed
markdown). The delta per metric@k therefore directly measures the
contribution of the preprocessing pipeline.

Inputs:
  data/baseline_evaluation/baseline_results.csv   — one row per (doc, HyQ question), baseline
  data/pipeline_evaluation/global_summary.csv      — one row per doc, pipeline averages (sem_mean_*)

A VLM call (text, structured output) then analyzes the numeric report and
produces an explicit verdict (baseline / pipeline / equivalent) + justification,
inserted at the top of the report. Can be disabled with --no-vlm-analysis.

Output:
  data/baseline_evaluation/comparison_report.md

Usage:
    uv run python compare_baseline_report.py
    uv run python compare_baseline_report.py --canonical-k 5 --top-ks 1,3,5,10,20
    uv run python compare_baseline_report.py --dotenv ../.env.test --no-vlm-analysis
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pydantic import BaseModel

# Bootstrap sys.path from the on-disk layout (not PROJECT_ROOT-aware: this locates the
# *source* directories to import sibling packages from, distinct from utils.paths.project_root()
# below which resolves *data* paths and does honor a PROJECT_ROOT override).
_HERE = Path(__file__).resolve().parent
_SRC_ROOT = _HERE.parent
_RETRIEVAL_EVAL_DIR = _SRC_ROOT / "retrieval_protocol_evaluation"

for _path in (_SRC_ROOT, _RETRIEVAL_EVAL_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from utils.paths import project_root  # noqa: E402
from utils.vlm_client import build_vlm_config, build_sync_client, text_completion_structured  # noqa: E402
from config import TOP_KS, CANONICAL_K  # noqa: E402

_log = logging.getLogger(__name__)

ANALYSIS_PROMPT = """Tu es un expert en évaluation de systèmes de retrieval sémantique.

On te donne un rapport (format markdown) comparant deux représentations d'un même corpus
de documents, évaluées avec les mêmes questions HyQ et les mêmes métriques (Recall,
Precision, nDCG, MRR @ plusieurs k) :
- "baseline" = markdown généré directement par docling, sans aucun prétraitement
- "pipeline" = markdown enrichi par le pipeline de prétraitement (OCR, enrichissement VLM,
  structuration, descriptions d'images, etc.)

En te basant uniquement sur les chiffres du rapport (résumé global + détail par document),
détermine laquelle des deux représentations offre globalement le meilleur retrieval. Utilise
le résumé global comme critère principal ; mentionne dans la justification les documents où
l'écart est le plus marqué dans un sens ou dans l'autre, et signale explicitement si le
résultat est mitigé plutôt que de forcer une conclusion tranchée. Réponds en français,
dans un rapport détaillé, sans reformuler le tableau ligne par ligne."""


class ComparisonVerdict(BaseModel):
    verdict: Literal["baseline", "pipeline", "équivalent"]
    justification: str

DEFAULT_BASELINE_RESULTS = project_root() / "data" / "baseline_evaluation" / "baseline_results.csv"
DEFAULT_PIPELINE_SUMMARY = project_root() / "data" / "pipeline_evaluation" / "global_summary.csv"
DEFAULT_OUTPUT_MD = project_root() / "data" / "baseline_evaluation" / "comparison_report.md"
DEFAULT_CHARTS_DIRNAME = "charts"

_METRICS = ["recall", "precision", "ndcg", "mrr"]
_METRIC_LABELS = {"recall": "Recall", "precision": "Precision", "ndcg": "nDCG", "mrr": "MRR"}
_PIPELINE_COLOR = "#4C72B0"
_BASELINE_COLOR = "#DD8452"
_CHART_SAVED = "Chart saved → %s"


# Data loading / merging
def load_baseline_means(baseline_results_path: Path, top_ks: list[int]) -> pd.DataFrame:
    """Per-question baseline_results.csv -> per-doc mean metrics (+ n_questions)."""
    df = pd.read_csv(baseline_results_path)
    metric_cols = [f"{m}@{k}" for m in _METRICS for k in top_ks]
    means = df.groupby("doc_name")[metric_cols].mean()
    means["n_questions"] = df.groupby("doc_name").size()
    return means.reset_index()


def load_pipeline_means(pipeline_summary_path: Path, top_ks: list[int]) -> pd.DataFrame:
    """global_summary.csv (sem_mean_* columns) -> same shape as load_baseline_means."""
    df = pd.read_csv(pipeline_summary_path)
    rename = {f"sem_mean_{m}@{k}": f"{m}@{k}" for m in _METRICS for k in top_ks}
    cols = ["doc_name", "n_questions"] + list(rename.keys())
    return df[cols].rename(columns=rename)


def build_comparison(
    baseline_means: pd.DataFrame,
    pipeline_means: pd.DataFrame,
    top_ks: list[int],
) -> pd.DataFrame:
    """One row per doc: baseline/pipeline/delta for every metric@k, inner-joined on doc_name."""
    merged = baseline_means.merge(
        pipeline_means, on="doc_name", suffixes=("_baseline", "_pipeline"), how="inner"
    )
    missing_baseline = set(pipeline_means["doc_name"]) - set(baseline_means["doc_name"])
    missing_pipeline = set(baseline_means["doc_name"]) - set(pipeline_means["doc_name"])
    if missing_baseline:
        _log.warning("Docs missing from baseline: %s", sorted(missing_baseline))
    if missing_pipeline:
        _log.warning("Docs missing from pipeline: %s", sorted(missing_pipeline))

    for m in _METRICS:
        for k in top_ks:
            merged[f"{m}@{k}_delta"] = merged[f"{m}@{k}_baseline"] - merged[f"{m}@{k}_pipeline"]
    return merged


# Markdown report
def _fmt(x: float) -> str:
    return f"{x:.3f}"


def _fmt_delta(x: float) -> str:
    sign = "+" if x > 0 else ""
    return f"{sign}{x:.3f}"


def render_global_section(comparison: pd.DataFrame, top_ks: list[int]) -> list[str]:
    lines = ["## Résumé global (moyenne sur les 20 documents)", ""]
    header = "| Métrique | " + " | ".join(f"k={k}" for k in top_ks) + " |"
    sep = "|---|" + "---|" * len(top_ks)
    for m in _METRICS:
        lines.append(f"### {m.upper()}")
        lines.append(header)
        lines.append(sep)
        row_pipe = "| Pipeline | " + " | ".join(_fmt(comparison[f"{m}@{k}_pipeline"].mean()) for k in top_ks) + " |"
        row_base = "| Baseline | " + " | ".join(_fmt(comparison[f"{m}@{k}_baseline"].mean()) for k in top_ks) + " |"
        row_delta = "| Delta | " + " | ".join(_fmt_delta(comparison[f"{m}@{k}_delta"].mean()) for k in top_ks) + " |"
        lines.extend([row_pipe, row_base, row_delta, ""])
    return lines


def render_per_doc_section(comparison: pd.DataFrame, canonical_k: int) -> list[str]:
    lines = [f"## Détail par document (k={canonical_k}, trié par delta nDCG croissant)", ""]
    header = (
        "| Document | Questions | Recall (pipe/base/Δ) | Precision (pipe/base/Δ) "
        "| nDCG (pipe/base/Δ) | MRR (pipe/base/Δ) |"
    )
    sep = "|---|---|---|---|---|---|"
    lines.extend([header, sep])

    sorted_df = comparison.sort_values(f"ndcg@{canonical_k}_delta")
    for _, row in sorted_df.iterrows():
        cells = [row["doc_name"], str(int(row["n_questions_baseline"]))]
        for m in _METRICS:
            p = row[f"{m}@{canonical_k}_pipeline"]
            b = row[f"{m}@{canonical_k}_baseline"]
            d = row[f"{m}@{canonical_k}_delta"]
            cells.append(f"{_fmt(p)} / {_fmt(b)} / {_fmt_delta(d)}")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return lines


# Charts
def plot_global_bars_at_k(comparison: pd.DataFrame, canonical_k: int, output_dir: Path) -> Path:
    """4 subplots (one per metric): Pipeline vs Baseline at the canonical k."""
    fig, axes = plt.subplots(2, 2, figsize=(9, 7))
    for ax, m in zip(axes.flat, _METRICS):
        pipe_val = comparison[f"{m}@{canonical_k}_pipeline"].mean()
        base_val = comparison[f"{m}@{canonical_k}_baseline"].mean()
        bars = ax.bar(["Pipeline", "Baseline"], [pipe_val, base_val],
                       color=[_PIPELINE_COLOR, _BASELINE_COLOR], width=0.6)
        ax.bar_label(bars, fmt="%.3f", padding=3)
        ax.set_ylim(0, 1.1)
        ax.set_title(f"{_METRIC_LABELS[m]}@{canonical_k}")
        ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    fig.suptitle(f"Comparaison globale — moyenne sur {len(comparison)} documents (k={canonical_k})")
    fig.tight_layout()
    path = output_dir / "global_comparison.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    _log.info(_CHART_SAVED, path)
    return path


def plot_metrics_by_k(comparison: pd.DataFrame, top_ks: list[int], output_dir: Path) -> Path:
    """4 subplots: Pipeline vs Baseline trend across k, one per metric."""
    fig, axes = plt.subplots(2, 2, figsize=(10, 7))
    for ax, m in zip(axes.flat, _METRICS):
        pipe_means = [comparison[f"{m}@{k}_pipeline"].mean() for k in top_ks]
        base_means = [comparison[f"{m}@{k}_baseline"].mean() for k in top_ks]
        ax.plot(top_ks, pipe_means, marker="o", linewidth=2, label="Pipeline", color=_PIPELINE_COLOR)
        ax.plot(top_ks, base_means, marker="o", linewidth=2, label="Baseline", color=_BASELINE_COLOR)
        for x, y in zip(top_ks, pipe_means):
            ax.annotate(f"{y:.3f}", (x, y), textcoords="offset points", xytext=(0, 7),
                        ha="center", fontsize=7, color=_PIPELINE_COLOR)
        for x, y in zip(top_ks, base_means):
            ax.annotate(f"{y:.3f}", (x, y), textcoords="offset points", xytext=(0, -11),
                        ha="center", fontsize=7, color=_BASELINE_COLOR)
        ax.set_title(_METRIC_LABELS[m])
        ax.set_xlabel("k")
        ax.set_ylim(0, 1.1)
        ax.grid(True, linestyle="--", alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle("Évolution des métriques selon k — Pipeline vs Baseline")
    fig.tight_layout()
    path = output_dir / "global_metrics_by_k.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    _log.info(_CHART_SAVED, path)
    return path


def plot_per_doc_ndcg(comparison: pd.DataFrame, canonical_k: int, output_dir: Path) -> Path:
    """Horizontal bars per document (Pipeline vs Baseline), sorted by ascending nDCG delta."""
    df = comparison.sort_values(f"ndcg@{canonical_k}_delta")
    n = len(df)
    y = np.arange(n)
    bar_h = 0.35

    fig, ax = plt.subplots(figsize=(9, max(5, n * 0.4 + 1)))
    bars_pipe = ax.barh(y + bar_h / 2, df[f"ndcg@{canonical_k}_pipeline"], bar_h, label="Pipeline", color=_PIPELINE_COLOR, alpha=0.9)
    bars_base = ax.barh(y - bar_h / 2, df[f"ndcg@{canonical_k}_baseline"], bar_h, label="Baseline", color=_BASELINE_COLOR, alpha=0.9)
    ax.bar_label(bars_pipe, fmt="%.3f", padding=3, fontsize=7)
    ax.bar_label(bars_base, fmt="%.3f", padding=3, fontsize=7)
    ax.set_yticks(y)
    ax.set_yticklabels(df["doc_name"], fontsize=8)
    ax.set_xlabel(f"nDCG@{canonical_k}")
    ax.set_title(f"nDCG@{canonical_k} par document (Pipeline vs Baseline)")
    ax.set_xlim(0, 1.15)
    ax.legend(loc="lower right")
    ax.grid(True, axis="x", linestyle="--", alpha=0.3)
    fig.tight_layout()

    path = output_dir / f"per_doc_ndcg_at_{canonical_k}.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    _log.info(_CHART_SAVED, path)
    return path


def plot_all_charts(comparison: pd.DataFrame, top_ks: list[int], canonical_k: int, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    return [
        plot_global_bars_at_k(comparison, canonical_k, output_dir),
        plot_metrics_by_k(comparison, top_ks, output_dir),
        plot_per_doc_ndcg(comparison, canonical_k, output_dir),
    ]


def render_charts_section(chart_paths: list[Path], report_dir: Path) -> str:
    lines = ["## Graphiques", ""]
    for path in chart_paths:
        rel = path.relative_to(report_dir) if path.is_relative_to(report_dir) else path
        lines.append(f"![{path.stem}]({rel.as_posix()})")
        lines.append("")
    return "\n".join(lines)


def render_body(comparison: pd.DataFrame, top_ks: list[int], canonical_k: int) -> str:
    """Numeric report (without VLM verdict) — this is the text sent to the VLM for analysis."""
    lines = [
        "# Rapport de comparaison — Baseline docling brut vs Pipeline de prétraitement",
        "",
        f"Généré le {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        (
            "Protocole : même corpus (20 documents), mêmes questions HyQ, mêmes métriques "
            "(Recall/Precision/nDCG/MRR@k) — seule la représentation du document change "
            "(markdown docling brut pour la baseline, markdown prétraité pour le pipeline). "
            "Delta = baseline − pipeline (positif = la baseline fait mieux, donc le "
            "prétraitement dégrade ; négatif = le prétraitement améliore le retrieval)."
        ),
        "",
    ]
    lines.extend(render_global_section(comparison, top_ks))
    lines.extend(render_per_doc_section(comparison, canonical_k))
    return "\n".join(lines)


def render_verdict_section(verdict: ComparisonVerdict) -> str:
    label = {
        "baseline": "Baseline (docling brut) meilleure — le prétraitement dégrade le retrieval",
        "pipeline": "Pipeline de prétraitement meilleur",
        "équivalent": "Résultat mitigé / équivalent",
    }[verdict.verdict]
    return (
        "## Verdict (analyse VLM)\n\n"
        f"**{label}**\n\n"
        f"{verdict.justification}\n"
    )


# VLM analysis
def analyze_with_vlm(report_body: str, dotenv_path: Path | None) -> ComparisonVerdict:
    cfg = build_vlm_config(dotenv_path=dotenv_path)
    client = build_sync_client(cfg)
    return text_completion_structured(
        client, cfg.vlm_model_name, ANALYSIS_PROMPT, report_body, ComparisonVerdict
    )


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare raw docling baseline vs preprocessing pipeline, generate a markdown report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--baseline-results", type=Path, default=DEFAULT_BASELINE_RESULTS)
    parser.add_argument("--pipeline-summary", type=Path, default=DEFAULT_PIPELINE_SUMMARY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_MD)
    parser.add_argument(
        "--top-ks",
        default=",".join(map(str, TOP_KS)),
        help=f"Comma-separated k values. Default: {','.join(map(str, TOP_KS))}",
    )
    parser.add_argument("--canonical-k", type=int, default=CANONICAL_K)
    parser.add_argument("--dotenv", type=Path, default=None)
    parser.add_argument(
        "--no-vlm-analysis",
        action="store_true",
        help="Do not call the VLM to analyze the report (numeric tables only).",
    )
    parser.add_argument(
        "--charts-dir",
        type=Path,
        default=None,
        help=f"Charts output directory. Default: <--output directory>/{DEFAULT_CHARTS_DIRNAME}",
    )
    parser.add_argument(
        "--no-charts",
        action="store_true",
        help="Do not generate charts.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    top_ks = [int(k) for k in args.top_ks.split(",")]

    if not args.baseline_results.exists():
        _log.error("File not found: %s (run single_docling_baseline.py first)", args.baseline_results)
        sys.exit(1)
    if not args.pipeline_summary.exists():
        _log.error("File not found: %s (run evaluate_all_docs.py first)", args.pipeline_summary)
        sys.exit(1)

    baseline_means = load_baseline_means(args.baseline_results, top_ks)
    pipeline_means = load_pipeline_means(args.pipeline_summary, top_ks)
    comparison = build_comparison(baseline_means, pipeline_means, top_ks)

    body = render_body(comparison, top_ks, args.canonical_k)

    verdict_section = ""
    if not args.no_vlm_analysis:
        try:
            _log.info("Analyzing the report with the VLM...")
            verdict = analyze_with_vlm(body, args.dotenv)
            verdict_section = render_verdict_section(verdict) + "\n"
            _log.info("VLM verdict: %s", verdict.verdict)
        except Exception:
            _log.exception("VLM analysis unavailable — report generated without a verdict.")

    charts_section = ""
    if not args.no_charts:
        charts_dir = args.charts_dir or (args.output.parent / DEFAULT_CHARTS_DIRNAME)
        chart_paths = plot_all_charts(comparison, top_ks, args.canonical_k, charts_dir)
        charts_section = render_charts_section(chart_paths, args.output.parent) + "\n"

    title, _, rest = body.partition("\n\n")
    report = f"{title}\n\n{verdict_section}{charts_section}{rest}" if (verdict_section or charts_section) else body

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    _log.info("Report generated → %s", args.output)


if __name__ == "__main__":
    main()
