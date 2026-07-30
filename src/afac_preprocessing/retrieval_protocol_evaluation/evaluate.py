"""
CLI — Evaluate Recall@k, Precision@k, nDCG@k and MRR@k for HyQ questions
against all document embeddings.

Usage (all questions of a document):
    python evaluate.py --doc-name "Adhésion traitement"

Usage (single question, for testing):
    python evaluate.py --doc-name "Adhésion traitement" --question-idx 1

Usage (custom k values and output):
    python evaluate.py --doc-name "Adhésion traitement" --top-ks 1,3,5,10 --output-dir ./results
"""
import argparse
import logging
import sys
from pathlib import Path

import numpy as np

from .config import DEFAULT_OUTPUT_DIR, DEFAULT_STAGE5, TOP_KS
from .loaders import load_all_doc_embeddings, load_hyq_questions
from .metrics import evaluate_all_metrics
from .report import plot_all_charts, save_results_csv
from .similarity import compute_similarity_matrix, rank_docs

_log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate retrieval metrics for HyQ questions vs document embeddings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--doc-name",
        required=True,
        help="Document name without extension (e.g. 'Adhésion traitement').",
    )
    parser.add_argument("--stage5", type=Path, default=DEFAULT_STAGE5)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--top-ks",
        default=",".join(map(str, TOP_KS)),
        help=f"Comma-separated k values. Default: {','.join(map(str, TOP_KS))}",
    )
    parser.add_argument(
        "--question-idx",
        type=int,
        default=None,
        help="Evaluate only this question index (1-based). Omit to evaluate all questions.",
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

    questions = load_hyq_questions(args.stage5, args.doc_name)
    if args.question_idx is not None:
        questions = [q for q in questions if q.question_idx == args.question_idx]
        if not questions:
            _log.error("No question found with index %d", args.question_idx)
            sys.exit(1)

    docs = load_all_doc_embeddings(args.stage5)
    if not docs:
        _log.error("No document embeddings found in %s", args.stage5)
        sys.exit(1)

    doc_names = [d.doc_name for d in docs]
    query_embeddings = np.stack([q.embedding for q in questions])
    doc_embeddings = np.stack([d.embedding for d in docs])

    _log.info("Computing similarity matrix (%d questions × %d docs)", len(questions), len(docs))
    sim_matrix = compute_similarity_matrix(query_embeddings, doc_embeddings)

    results = []
    for i, question in enumerate(questions):
        ranked_indices = rank_docs(sim_matrix[i])
        ranked_doc_names = [doc_names[j] for j in ranked_indices]
        source_doc_name = question.source_doc_title.removesuffix(".pdf")

        scores = evaluate_all_metrics(
            sim_scores=sim_matrix[i],
            doc_names=doc_names,
            source_doc_name=source_doc_name,
            top_ks=top_ks,
        )

        row: dict = {
            "doc_name": args.doc_name,
            "question_idx": question.question_idx,
            "question": question.content,
            "source_doc": source_doc_name,
            "top1_doc": ranked_doc_names[0] if ranked_doc_names else "",
            "top1_score": float(sim_matrix[i][ranked_indices[0]]),
        }
        for k in top_ks:
            row[f"recall@{k}"]    = scores["recall"][k]
            row[f"precision@{k}"] = scores["precision"][k]
            row[f"ndcg@{k}"]      = scores["ndcg"][k]
            row[f"mrr@{k}"]       = scores["mrr"][k]
        results.append(row)

        _log.info(
            "Q%02d | R@1=%d P@1=%.3f nDCG@5=%.3f MRR@5=%.3f | source='%s' | top1='%s' (%.4f)",
            question.question_idx,
            scores["recall"][1],
            scores["precision"][1],
            scores["ndcg"].get(5, 0),
            scores["mrr"].get(5, 0),
            source_doc_name,
            ranked_doc_names[0] if ranked_doc_names else "?",
            float(sim_matrix[i][ranked_indices[0]]),
        )

    output_path = args.output_dir / args.doc_name / "evaluation_results.csv"
    save_results_csv(results, output_path)

    if len(results) > 1:
        plot_all_charts(output_path, args.output_dir / args.doc_name, top_ks)

    _log.info("Done. %d question(s) evaluated.", len(results))
    sys.exit(0)


if __name__ == "__main__":
    main()
