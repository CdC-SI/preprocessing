"""
Batch evaluation — Recall@k, Precision@k, nDCG@k, MRR@k for every AFAC document.
Runs two pipelines per question: semantic search alone, then semantic + reranker.

Outputs:
  <output-dir>/<doc-name>/evaluation_results.csv          (semantic, per-doc)
  <output-dir>/<doc-name>/evaluation_results_reranked.csv (reranker, per-doc)
  <output-dir>/<doc-name>/<metric>_at_k.png               (per-doc line charts)
  <output-dir>/global_summary.csv                         (mean metrics per doc, both pipelines)
  <output-dir>/global_<metric>@<k>_comparison.png         (ordered grouped bar charts)
  <output-dir>/global_pipeline_comparison.png             (overall means summary)

Usage:
    python evaluate_all_docs.py
    python evaluate_all_docs.py --canonical-k 5 --top-ks 1,3,5,10,20
    python evaluate_all_docs.py --no-reranker
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .config import CANONICAL_K, DEFAULT_OUTPUT_DIR, DEFAULT_STAGE5, TOP_KS
from .loaders import load_all_doc_embeddings, load_doc_resumes, load_hyq_questions
from .metrics import evaluate_all_metrics
from .report import plot_all_charts, plot_global_barcharts, save_results_csv
from .reranker import rerank
from .similarity import compute_similarity_matrix, rank_docs

_log = logging.getLogger(__name__)



# Helpers
def discover_doc_names(stage5_dir: Path) -> list[str]:
    """All doc names that have both a document embedding and at least one HyQ question."""
    names = []
    for csv_path in sorted(stage5_dir.rglob("metadata/*_final.csv")):
        doc_name = csv_path.stem.removesuffix("_final")
        hyq_dir = csv_path.parent / f"hyq_{doc_name}"
        if hyq_dir.exists() and any(hyq_dir.glob("question_*.csv")):
            names.append(doc_name)
    return names


def _build_row(
    doc_name: str,
    question,
    top1_doc: str,
    top1_score: float,
    scores: dict[str, dict[int, float]],
    top_ks: list[int],
) -> dict:
    row: dict = {
        "doc_name": doc_name,
        "question_idx": question.question_idx,
        "question": question.content,
        "source_doc": question.source_doc_title.removesuffix(".pdf"),
        "top1_doc": top1_doc,
        "top1_score": top1_score,
    }
    for k in top_ks:
        row[f"recall@{k}"]    = scores["recall"][k]
        row[f"precision@{k}"] = scores["precision"][k]
        row[f"ndcg@{k}"]      = scores["ndcg"][k]
        row[f"mrr@{k}"]       = scores["mrr"][k]
    return row


async def evaluate_doc(
    doc_name: str,
    doc_names: list[str],
    doc_embeddings: np.ndarray,
    doc_texts: list[str],
    stage5_dir: Path,
    top_ks: list[int],
    use_reranker: bool,
) -> tuple[list[dict], list[dict]]:
    """
    Returns (semantic_rows, reranker_rows).
    reranker_rows is empty if use_reranker=False or if all reranker calls fail.
    """
    try:
        questions = load_hyq_questions(stage5_dir, doc_name)
    except FileNotFoundError as exc:
        _log.warning("Skipping '%s': %s", doc_name, exc)
        return [], []

    if not questions:
        _log.warning("No questions for '%s' — skipping.", doc_name)
        return [], []

    query_embeddings = np.stack([q.embedding for q in questions])
    sim_matrix = compute_similarity_matrix(query_embeddings, doc_embeddings)

    sem_rows: list[dict] = []
    rer_rows: list[dict] = []
    reranker_failed = False

    for i, question in enumerate(questions):
        source = question.source_doc_title.removesuffix(".pdf")

        # Semantic pipeline
        sem_idx = rank_docs(sim_matrix[i])
        sem_ranked = [doc_names[j] for j in sem_idx]
        sem_scores = evaluate_all_metrics(
            sim_scores=sim_matrix[i],
            doc_names=doc_names,
            source_doc_name=source,
            top_ks=top_ks,
        )
        sem_rows.append(_build_row(
            doc_name, question,
            top1_doc=sem_ranked[0],
            top1_score=float(sim_matrix[i][sem_idx[0]]),
            scores=sem_scores,
            top_ks=top_ks,
        ))

        # Reranker pipeline
        if use_reranker and not reranker_failed:
            rer_raw = await rerank(question.content, doc_texts)
            if rer_raw is None:
                _log.warning("Reranker unavailable for '%s' — skipping reranker for this doc.", doc_name)
                reranker_failed = True
            else:
                rer_arr = np.array(rer_raw, dtype=np.float32)
                rer_idx = rank_docs(rer_arr)
                rer_ranked = [doc_names[j] for j in rer_idx]
                rer_scores = evaluate_all_metrics(
                    sim_scores=rer_arr,
                    doc_names=doc_names,
                    source_doc_name=source,
                    top_ks=top_ks,
                )
                rer_rows.append(_build_row(
                    doc_name, question,
                    top1_doc=rer_ranked[0],
                    top1_score=float(rer_arr[rer_idx[0]]),
                    scores=rer_scores,
                    top_ks=top_ks,
                ))

        _log.debug(
            "  Q%02d | sem R@1=%d nDCG@5=%.3f | source='%s'",
            question.question_idx,
            sem_scores["recall"].get(1, 0),
            sem_scores["ndcg"].get(5, 0),
            source,
        )

    return sem_rows, rer_rows


def _add_mean_metrics(
    row: dict,
    df: pd.DataFrame,
    prefix: str,
    top_ks: list[int],
) -> None:
    """Ajoute dans `row` les moyennes de chaque métrique@k pour le pipeline `prefix`."""
    for metric in ("recall", "precision", "ndcg", "mrr"):
        for k in top_ks:
            col = f"{metric}@{k}"
            if col in df.columns:
                row[f"{prefix}_mean_{col}"] = df[col].mean()


def build_global_summary(
    sem_results: dict[str, list[dict]],
    rer_results: dict[str, list[dict]],
    top_ks: list[int],
) -> pd.DataFrame:
    """Une ligne par document — métriques moyennes pour les deux pipelines."""
    rows = []
    for doc_name, sem_rows in sem_results.items():
        if not sem_rows:
            continue
        sem_df = pd.DataFrame(sem_rows)
        row: dict = {"doc_name": doc_name, "n_questions": len(sem_df)}
        _add_mean_metrics(row, sem_df, "sem", top_ks)

        rer_rows = rer_results.get(doc_name, [])
        if rer_rows:
            _add_mean_metrics(row, pd.DataFrame(rer_rows), "rer", top_ks)

        rows.append(row)

    return pd.DataFrame(rows)


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch retrieval evaluation for all AFAC documents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--stage5", type=Path, default=DEFAULT_STAGE5)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--top-ks",
        default=",".join(map(str, TOP_KS)),
        help=f"Comma-separated k values. Default: {','.join(map(str, TOP_KS))}",
    )
    parser.add_argument(
        "--canonical-k",
        type=int,
        default=CANONICAL_K,
        help=f"k used for ordered bar charts (default: {CANONICAL_K}).",
    )
    parser.add_argument(
        "--no-reranker",
        action="store_true",
        help="Skip reranker evaluation (semantic search only).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    top_ks = [int(k) for k in args.top_ks.split(",")]
    use_reranker = not args.no_reranker

    # Load shared corpus data once
    docs = load_all_doc_embeddings(args.stage5)
    if not docs:
        _log.error("No document embeddings found in %s", args.stage5)
        sys.exit(1)

    doc_names = [d.doc_name for d in docs]
    doc_embeddings = np.stack([d.embedding for d in docs])

    resumes = load_doc_resumes(args.stage5)
    doc_texts = [resumes.get(name, "") for name in doc_names]
    if use_reranker and not any(doc_texts):
        _log.warning("No resume.md found — reranker will receive empty document texts.")

    target_docs = discover_doc_names(args.stage5)
    _log.info("Evaluating %d documents (reranker=%s) …", len(target_docs), use_reranker)

    sem_results: dict[str, list[dict]] = {}
    rer_results: dict[str, list[dict]] = {}

    for doc_name in target_docs:
        _log.info("── %s", doc_name)
        sem_rows, rer_rows = await evaluate_doc(
            doc_name=doc_name,
            doc_names=doc_names,
            doc_embeddings=doc_embeddings,
            doc_texts=doc_texts,
            stage5_dir=args.stage5,
            top_ks=top_ks,
            use_reranker=use_reranker,
        )

        sem_results[doc_name] = sem_rows
        rer_results[doc_name] = rer_rows

        doc_out = args.output_dir / doc_name

        if sem_rows:
            sem_path = doc_out / "evaluation_results.csv"
            save_results_csv(sem_rows, sem_path)
            if len(sem_rows) > 1:
                plot_all_charts(sem_path, doc_out, top_ks)

        if rer_rows:
            rer_path = doc_out / "evaluation_results_reranked.csv"
            save_results_csv(rer_rows, rer_path)

    # Global summary
    summary_df = build_global_summary(sem_results, rer_results, top_ks)
    summary_path = args.output_dir / "global_summary.csv"
    summary_df.to_csv(summary_path, index=False)
    _log.info("Global summary saved → %s", summary_path)

    # Global bar charts
    has_reranker_data = any(bool(v) for v in rer_results.values())
    plot_global_barcharts(
        summary_df=summary_df,
        output_dir=args.output_dir,
        k=args.canonical_k,
        include_reranker=has_reranker_data,
    )

    _log.info("Done. %d documents evaluated.", len(target_docs))


if __name__ == "__main__":
    asyncio.run(main())
