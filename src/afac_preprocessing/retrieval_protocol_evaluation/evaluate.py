"""
CLI — Evaluate Recall@k, Precision@k, nDCG@k and MRR@k for a question set
against all document embeddings.

The question set comes from exactly one of three sources:

  --doc-name        the pipeline HyQ of a single document (historical behaviour)
  --all-docs        the pipeline HyQ of the whole corpus, as one flat set
  --questions-file  an externally authored set (the slot-based variants),
                    embedded on the fly

The last two produce the same CSV schema over the same corpus, so comparing
their results isolates the question set as the only variable — that is the point
of the slot experiment. Use ``compare_question_sets.py`` on the resulting runs.

Usage (all questions of a document):
    python -m afac_preprocessing.retrieval_protocol_evaluation.evaluate --doc-name "Adhésion traitement"

Usage (single question, for testing):
    python -m ...evaluate --doc-name "Adhésion traitement" --question-idx 1

Usage (control arm — every pipeline-generated HyQ):
    python -m ...evaluate --all-docs --run-label pipeline_hyq

Usage (slot arm):
    python -m ...evaluate --questions-file slots_v1.json --run-label slots_v1
"""
import argparse
import logging
import sys
from pathlib import Path

import numpy as np

from ..clients.bundle import ClientBundle
from ..settings import Settings, default_dotenv
from .config import DEFAULT_OUTPUT_DIR, DEFAULT_STAGE5, TOP_KS
from .loaders import (
    QuestionRecord,
    load_all_doc_embeddings,
    load_all_pipeline_questions,
    load_custom_questions,
    load_hyq_questions,
    parse_questions_file,
    validate_ground_truth,
)
from .metrics import evaluate_all_metrics
from .report import plot_all_charts, save_results_csv
from .similarity import compute_similarity_matrix, rank_docs

_log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate retrieval metrics for a question set vs document embeddings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--doc-name",
        help="Evaluate the pipeline HyQ of this document (name without extension).",
    )
    source.add_argument(
        "--all-docs",
        action="store_true",
        help="Evaluate every pipeline-generated HyQ of the corpus as one flat set.",
    )
    source.add_argument(
        "--questions-file",
        type=Path,
        help="JSON question set: [{question, source_doc, slots?, id?}, …] "
             "or {variant, questions: [...]}.",
    )

    parser.add_argument("--stage5", type=Path, default=DEFAULT_STAGE5)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--run-label",
        default=None,
        help="Sub-folder receiving the results. Defaults to the document name, "
             "the question file's variant, or 'pipeline_hyq'.",
    )
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
        "--dotenv",
        type=Path,
        default=None,
        help="Path to the .env providing EMBEDDING_URL (--questions-file only).",
    )
    parser.add_argument(
        "--embedding-cache",
        type=Path,
        default=None,
        help="Question-embedding cache (--questions-file only). "
             "Defaults to <questions-file>.embeddings.json; use 'none' to disable.",
    )
    parser.add_argument(
        "--allow-unknown-docs",
        action="store_true",
        help="Drop questions whose source_doc is absent from the corpus "
             "instead of failing.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def _resolve_cache_path(args: argparse.Namespace) -> Path | None:
    if args.embedding_cache is not None:
        return None if str(args.embedding_cache).lower() == "none" else args.embedding_cache
    return args.questions_file.with_suffix(".embeddings.json")


def load_questions(args: argparse.Namespace, corpus_doc_names: list[str]) -> tuple[str, list]:
    """Return (run label, questions) for whichever source was requested."""
    if args.doc_name:
        return args.run_label or args.doc_name, load_hyq_questions(args.stage5, args.doc_name)

    if args.all_docs:
        return args.run_label or "pipeline_hyq", load_all_pipeline_questions(args.stage5)

    variant, entries = parse_questions_file(args.questions_file)
    entries = validate_ground_truth(
        entries, corpus_doc_names, strict=not args.allow_unknown_docs
    )
    if not entries:
        raise ValueError("No question left after ground-truth validation.")

    settings = Settings.from_dotenv(args.dotenv or default_dotenv())
    # The bundle owns the loop and closes the HTTP client; main() is sync, so
    # the embedding coroutine is driven through run_async (never asyncio.run).
    with ClientBundle(settings) as clients:
        questions = clients.run_async(load_custom_questions(
            entries,
            clients.embeddings(),
            embedding_model=settings.embedding_model_name,
            cache_path=_resolve_cache_path(args),
        ))
    return args.run_label or variant, questions


def build_row(
    question: QuestionRecord,
    ranked_doc_names: list[str],
    top1_score: float,
    scores: dict[str, dict[int, float]],
    top_ks: list[int],
) -> dict:
    """One results row. Slots become ``slot_<name>`` columns so the metrics can
    be sliced by slot type directly in pandas."""
    row: dict = {
        "question_id": question.question_id,
        "question_idx": question.question_idx,
        "question": question.content,
        # doc_name mirrors source_doc: for a single-document run they are equal
        # (historical column preserved), for a cross-document set it is the only
        # meaningful grouping key.
        "doc_name": question.source_doc_title.removesuffix(".pdf"),
        "source_doc": question.source_doc_title.removesuffix(".pdf"),
        "top1_doc": ranked_doc_names[0] if ranked_doc_names else "",
        "top1_score": top1_score,
    }
    for k in top_ks:
        row[f"recall@{k}"]    = scores["recall"][k]
        row[f"precision@{k}"] = scores["precision"][k]
        row[f"ndcg@{k}"]      = scores["ndcg"][k]
        row[f"mrr@{k}"]       = scores["mrr"][k]
    for name, value in question.slots.items():
        row[f"slot_{name}"] = value
    return row


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    top_ks = [int(k) for k in args.top_ks.split(",")]

    # The corpus is loaded first: it is both the ranking target and the
    # reference used to validate the ground truth of a custom question set.
    docs = load_all_doc_embeddings(args.stage5)
    if not docs:
        _log.error("No document embeddings found in %s", args.stage5)
        sys.exit(1)

    doc_names = [d.doc_name for d in docs]
    doc_embeddings = np.stack([d.embedding for d in docs])

    try:
        run_label, questions = load_questions(args, doc_names)
    except (FileNotFoundError, ValueError) as exc:
        _log.error("%s", exc)
        sys.exit(1)

    if args.question_idx is not None:
        questions = [q for q in questions if q.question_idx == args.question_idx]
        if not questions:
            _log.error("No question found with index %d", args.question_idx)
            sys.exit(1)

    if not questions:
        _log.error("No question to evaluate.")
        sys.exit(1)

    query_embeddings = np.stack([q.embedding for q in questions])
    if query_embeddings.shape[1] != doc_embeddings.shape[1]:
        _log.error(
            "Embedding dimension mismatch: questions=%d, documents=%d — the two "
            "were produced by different models, the scores would be meaningless.",
            query_embeddings.shape[1], doc_embeddings.shape[1],
        )
        sys.exit(1)

    _log.info(
        "Run '%s': computing similarity matrix (%d questions × %d docs)",
        run_label, len(questions), len(docs),
    )
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
        results.append(build_row(
            question=question,
            ranked_doc_names=ranked_doc_names,
            top1_score=float(sim_matrix[i][ranked_indices[0]]),
            scores=scores,
            top_ks=top_ks,
        ))

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

    output_path = args.output_dir / run_label / "evaluation_results.csv"
    save_results_csv(results, output_path)

    if len(results) > 1:
        plot_all_charts(output_path, args.output_dir / run_label, top_ks)

    _log.info("Done. %d question(s) evaluated → %s", len(results), output_path)
    sys.exit(0)


if __name__ == "__main__":
    main()
